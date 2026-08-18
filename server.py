import os
import io
import json
import logging
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from dotenv import load_dotenv
import asyncio
from datetime import datetime

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("procurement-server")

# Import internal services
from services.ocr_engine import UnifiedOCREngine
from services.business_rules import detect_business_category, format_receipt_to_sheet_rows, check_vat_alert
from services.spell_checker import validate_receipt_fields, check_vietnamese_spelling
from services.duplicate_checker import DuplicateChecker
from services.google_service import GoogleSyncService

# Try to import pytesseract
try:
    import pytesseract
except ImportError:
    pytesseract = None

# Initialize core services
ocr_engine = UnifiedOCREngine()
google_service = GoogleSyncService()
duplicate_checker = DuplicateChecker()

app = FastAPI(
    title="Procurement Receipt OCR & Automation API",
    description="Automated Receipt OCR pipeline: Google Drive Sync -> Classification -> Rules -> Duplicate & VAT Checks -> Google Sheets.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory batch staging store
staged_receipts: List[Dict[str, Any]] = []

# Concurrency Mutex Lock for Scans & Sheet Exports
scan_lock = asyncio.Lock()

# Auto-scan configuration & real-time telemetry
AUTO_SCAN_ENABLED = False
SCAN_INTERVAL_SECONDS = 60
ACTIVE_AI_MODEL = os.getenv("OPENAI_MODEL", "gemini-3.1-flash-lite")
LAST_SCAN_TIME = None
LAST_PROCESSED_COUNT = 0
LAST_SCAN_MESSAGE = ""
CURRENT_SCAN_PROGRESS = ""
SCAN_STOP_REQUESTED = False


@app.get("/health")
@app.get("/api/v1/status")
async def system_status():
    """
    Check overall system health and status of Google APIs, Cloud LLM, and Local Ollama.
    """
    return {
        "status": "healthy",
        "ai_engine_mode": os.getenv("AI_ENGINE_MODE", "auto"),
        "cloud_llm_ready": bool(os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")),
        "cloud_model": os.getenv("OPENAI_MODEL", "gemini-2.5-flash-lite"),
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "qwen2.5-vl:7b"),
        "google_connected": google_service.is_connected(),
        "drive_folder_configured": bool(os.getenv("GOOGLE_DRIVE_FOLDER_ID")),
        "sheet_configured": bool(os.getenv("GOOGLE_SHEET_ID")),
        "tesseract_available": pytesseract is not None,
        "staged_count": len(staged_receipts)
    }


@app.post("/ocr/")
@app.post("/api/v1/receipt-ocr")
async def process_single_receipt(
    file: UploadFile = File(...),
    mode: Optional[str] = Form("auto"),
    model: Optional[str] = Form(None)
):
    """
    Process a single receipt image through OCR, Category Classification (DT1-4),
    Sheet Row Formatting, Duplicate Checking, VAT Checking, and Vietnamese Spell Checking.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File provided is not an image.")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size exceeds 10MB limit.")

    try:
        # 1. Run OCR (Cloud / Ollama / Auto) - Offloaded to worker thread
        target_model = model or ACTIVE_AI_MODEL
        parsed_data = await asyncio.to_thread(
            ocr_engine.process_image, content, mode=mode, custom_model=target_model
        )

        # 2. Detect Business Category (1, 2, 3, 4)
        cat_id, cat_name = detect_business_category(parsed_data)

        # 3. Check for duplicates
        doc_codes = [
            parsed_data.get("object_code"),
            parsed_data.get("dt_code"),
            parsed_data.get("order_id"),
            parsed_data.get("tracking_number"),
            parsed_data.get("invoice_number"),
            parsed_data.get("receipt_number")
        ]
        is_dup = False
        dup_msg = ""
        for c in doc_codes:
            if c and str(c).strip():
                is_d, msg = duplicate_checker.check_duplicate(str(c))
                if is_d:
                    is_dup = True
                    dup_msg = msg
                    break

        # 4. Check for VAT
        has_vat, vat_amt, vat_alert = check_vat_alert(parsed_data)

        # 5. Check Vietnamese spelling
        spell_warnings = validate_receipt_fields(parsed_data)

        # 6. Format into Google Sheet rows (simulate index 1 for preview)
        rows, meta = format_receipt_to_sheet_rows(parsed_data, category_id=cat_id, current_index=1)

        result_payload = {
            "success": True,
            "filename": file.filename,
            "category": {
                "id": cat_id,
                "name": cat_name,
                "code_prefix": f"DT{cat_id}"
            },
            "warnings": {
                "is_duplicate": is_dup,
                "duplicate_message": dup_msg,
                "has_vat": has_vat,
                "vat_amount": vat_amt,
                "vat_alert": vat_alert,
                "spell_warnings": spell_warnings
            },
            "raw_parsed_data": parsed_data,
            "formatted_sheet_rows": rows,
            "engine_used": parsed_data.get("_engine_used", mode)
        }
        return JSONResponse(content=result_payload)
    except Exception as e:
        logger.error(f"Receipt processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Xử lý hóa đơn thất bại: {str(e)}")


# =============================================================================
# GOOGLE DRIVE & GOOGLE SHEETS BATCH PIPELINE
# =============================================================================
@app.post("/api/v1/drive/scan")
async def scan_drive_folder(folder_id: Optional[str] = Form(None)):
    """
    List all pending invoice images from the configured Google Drive folder.
    """
    try:
        files = await asyncio.to_thread(google_service.list_images_in_folder, folder_id)
        return {"success": True, "count": len(files), "files": files}
    except Exception as e:
        logger.error(f"Drive scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# BACKGROUND AUTOMATED SCANNER
# =============================================================================
async def run_automated_scan():
    """
    Core logic for automated end-to-end scanning (Non-blocking & Concurrency-safe).
    Downloads -> OCR -> Check Duplicates -> Format -> Append Sheet -> Move to Da_Xu_Ly
    """
    global LAST_SCAN_TIME, LAST_PROCESSED_COUNT, LAST_SCAN_MESSAGE, CURRENT_SCAN_PROGRESS, SCAN_STOP_REQUESTED

    if scan_lock.locked():
        logger.info("Scan operation is already in progress. Skipping concurrent run.")
        return 0

    SCAN_STOP_REQUESTED = False

    async with scan_lock:
        try:
            drive_files = await asyncio.to_thread(google_service.list_images_in_folder)
            if not drive_files:
                CURRENT_SCAN_PROGRESS = ""
                LAST_SCAN_TIME = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
                LAST_SCAN_MESSAGE = f"Quét xong lúc {LAST_SCAN_TIME}: Không có file mới trong Inbox."
                return 0

            checker = DuplicateChecker()
            try:
                sheet_rows = await asyncio.to_thread(google_service.get_sheet_data)
                checker.load_from_sheet_rows(sheet_rows)
                max_indexes = await asyncio.to_thread(google_service.get_max_indexes_by_category)
            except Exception:
                max_indexes = {1: 0, 2: 0, 3: 0, 4: 0}

            processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")
            success_count = 0
            total_files = len(drive_files)

            for idx, f in enumerate(drive_files, start=1):
                if SCAN_STOP_REQUESTED:
                    logger.info("Scan cancelled by user request.")
                    CURRENT_SCAN_PROGRESS = ""
                    LAST_SCAN_MESSAGE = f"Đã dừng quét theo yêu cầu lúc {datetime.now().strftime('%H:%M:%S')}"
                    break

                file_id = f["id"]
                file_name = f["name"]
                CURRENT_SCAN_PROGRESS = f"Đang xử lý ({idx}/{total_files}): {file_name}"
                try:
                    img_bytes, _ = await asyncio.to_thread(google_service.download_file_bytes, file_id)
                    parsed_data = await asyncio.to_thread(ocr_engine.process_image, img_bytes, custom_model=ACTIVE_AI_MODEL)
                    cat_id, cat_name = detect_business_category(parsed_data)
                    
                    doc_codes = [
                        parsed_data.get("order_id"),
                        parsed_data.get("tracking_number"),
                        parsed_data.get("invoice_number"),
                        parsed_data.get("receipt_number")
                    ]
                    is_dup = False
                    dup_msg = ""
                    for c in doc_codes:
                        if c and str(c).strip():
                            is_d, msg = checker.check_duplicate(str(c))
                            if is_d:
                                is_dup = True
                                dup_msg = msg
                                break

                    # Nếu nghi vấn trùng: Gắn cờ cảnh báo rõ ràng để người dùng/kế toán đối soát và quyết định
                    if is_dup:
                        logger.warning(f"File {file_name} có nghi vấn trùng: {dup_msg}. Ghi nhận và gắn cờ cảnh báo cho người dùng quyết định.")
                        existing_notes = str(parsed_data.get("notes") or "")
                        parsed_data["notes"] = f"[⚠️ NGHI VẤN TRÙNG: {dup_msg}] {existing_notes}".strip()

                    for c in doc_codes:
                        if c and str(c).strip():
                            checker.add_code(str(c))

                    max_indexes[cat_id] += 1
                    curr_idx = max_indexes[cat_id]

                    rows, meta = format_receipt_to_sheet_rows(parsed_data, category_id=cat_id, current_index=curr_idx)
                    
                    if rows:
                        await asyncio.to_thread(google_service.append_rows_to_sheet, rows)
                        link_url = f"https://drive.google.com/file/d/{file_id}/view"
                        dt_code = meta["dt_code"]
                        confirm_initial = "⚠️ Nghi vấn trùng" if is_dup else ""
                        await asyncio.to_thread(google_service.append_links_to_sheet, [[dt_code, file_name, link_url, confirm_initial]])
                        
                        if processed_folder_id:
                            await asyncio.to_thread(google_service.move_file_to_folder, file_id, processed_folder_id)
                        
                        success_count += 1

                except Exception as item_err:
                    logger.error(f"Error auto-processing file {file_name}: {item_err}")

            LAST_PROCESSED_COUNT = success_count
            LAST_SCAN_TIME = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
            if not SCAN_STOP_REQUESTED:
                LAST_SCAN_MESSAGE = f"Đã quét & đẩy lên Sheet {success_count} hóa đơn lúc {LAST_SCAN_TIME}"
            CURRENT_SCAN_PROGRESS = ""
            return success_count
        except Exception as e:
            logger.error(f"Auto scan error: {e}", exc_info=True)
            CURRENT_SCAN_PROGRESS = ""
            return 0

async def background_scanner():
    global AUTO_SCAN_ENABLED, SCAN_INTERVAL_SECONDS
    logger.info("Background scanner task started.")
    while True:
        if AUTO_SCAN_ENABLED:
            logger.info("Running scheduled auto scan...")
            await run_automated_scan()
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scanner())


@app.post("/api/v1/auto-scan/toggle")
async def toggle_auto_scan(enabled: bool = Body(..., embed=True)):
    global AUTO_SCAN_ENABLED
    AUTO_SCAN_ENABLED = enabled
    return {"success": True, "auto_scan_enabled": AUTO_SCAN_ENABLED}

@app.post("/api/v1/auto-scan/stop")
async def stop_auto_scan():
    global SCAN_STOP_REQUESTED, AUTO_SCAN_ENABLED
    AUTO_SCAN_ENABLED = False
    SCAN_STOP_REQUESTED = True
    return {"success": True, "message": "Đã gửi lệnh dừng quét thành công."}

@app.post("/api/v1/auto-scan/trigger")
async def trigger_auto_scan(background_tasks: BackgroundTasks):
    if scan_lock.locked():
        return {
            "success": True,
            "running": True,
            "is_scanning": True,
            "message": "Hệ thống đang trong quá trình quét và xử lý ảnh từ Drive...",
            "processed_count": 0
        }
    background_tasks.add_task(run_automated_scan)
    return {
        "success": True,
        "running": True,
        "is_scanning": True,
        "message": "Đã bắt đầu quét Google Drive trong nền!"
    }

@app.get("/api/v1/auto-scan/status")
async def auto_scan_status():
    global AUTO_SCAN_ENABLED, SCAN_INTERVAL_SECONDS, ACTIVE_AI_MODEL
    global LAST_SCAN_TIME, LAST_PROCESSED_COUNT, LAST_SCAN_MESSAGE, CURRENT_SCAN_PROGRESS
    return {
        "success": True,
        "auto_scan_enabled": AUTO_SCAN_ENABLED,
        "interval_seconds": SCAN_INTERVAL_SECONDS,
        "active_model": ACTIVE_AI_MODEL,
        "is_scanning": scan_lock.locked(),
        "current_progress": CURRENT_SCAN_PROGRESS,
        "last_scan_time": LAST_SCAN_TIME,
        "last_processed_count": LAST_PROCESSED_COUNT,
        "last_scan_message": LAST_SCAN_MESSAGE
    }

@app.get("/api/v1/config/model")
async def get_active_model():
    global ACTIVE_AI_MODEL
    return {"success": True, "active_model": ACTIVE_AI_MODEL}

@app.post("/api/v1/config/model")
async def set_active_model(payload: dict = Body(...)):
    global ACTIVE_AI_MODEL
    model_name = payload.get("model", "").strip()
    if model_name:
        ACTIVE_AI_MODEL = model_name
        os.environ["OPENAI_MODEL"] = model_name
        logger.info(f"Active AI model updated to: {ACTIVE_AI_MODEL}")
        try:
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                import re
                if re.search(r"^OPENAI_MODEL=.*$", content, flags=re.MULTILINE):
                    new_content = re.sub(r"^OPENAI_MODEL=.*$", f"OPENAI_MODEL={model_name}", content, flags=re.MULTILINE)
                else:
                    new_content = content.rstrip() + f"\nOPENAI_MODEL={model_name}\n"
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
        except Exception as env_err:
            logger.warning(f"Could not persist model to .env: {env_err}")
    return {"success": True, "active_model": ACTIVE_AI_MODEL}


@app.post("/api/v1/drive/process-batch")
async def process_drive_batch(
    folder_id: Optional[str] = Form(None),
    mode: Optional[str] = Form("auto")
):
    """
    Automatically download images from Google Drive folder, run OCR & Classification,
    perform Duplicate/VAT/Spell checks, and stage results for review.
    """
    global staged_receipts
    try:
        drive_files = google_service.list_images_in_folder(folder_id)
        if not drive_files:
            return {"success": True, "message": "Không tìm thấy ảnh nào trong thư mục Google Drive.", "staged": []}

        # Initialize duplicate checker fresh from current sheet data
        checker = DuplicateChecker()
        try:
            sheet_rows = google_service.get_sheet_data()
            checker.load_from_sheet_rows(sheet_rows)
            max_indexes = google_service.get_max_indexes_by_category()
        except Exception:
            max_indexes = {1: 0, 2: 0, 3: 0, 4: 0}

        staged_results = []
        for f in drive_files:
            file_id = f["id"]
            file_name = f["name"]
            try:
                img_bytes, _ = google_service.download_file_bytes(file_id)
                parsed_data = ocr_engine.process_image(img_bytes, mode=mode, custom_model=ACTIVE_AI_MODEL)

                cat_id, cat_name = detect_business_category(parsed_data)
                max_indexes[cat_id] += 1
                curr_idx = max_indexes[cat_id]

                doc_codes = [
                    parsed_data.get("object_code"),
                    parsed_data.get("dt_code"),
                    parsed_data.get("order_id"),
                    parsed_data.get("tracking_number"),
                    parsed_data.get("invoice_number"),
                    parsed_data.get("receipt_number")
                ]
                is_dup = False
                dup_msg = ""
                for c in doc_codes:
                    if c and str(c).strip():
                        is_d, msg = checker.check_duplicate(str(c))
                        if is_d:
                            is_dup = True
                            dup_msg = msg
                            break

                for c in doc_codes:
                    if c and str(c).strip():
                        checker.add_code(str(c))

                has_vat, vat_amt, vat_alert = check_vat_alert(parsed_data)
                spell_warnings = validate_receipt_fields(parsed_data)

                rows, meta = format_receipt_to_sheet_rows(parsed_data, category_id=cat_id, current_index=curr_idx)

                item_entry = {
                    "drive_file_id": file_id,
                    "filename": file_name,
                    "category": {"id": cat_id, "name": cat_name, "dt_code": meta["dt_code"]},
                    "warnings": {
                        "is_duplicate": is_dup,
                        "duplicate_message": dup_msg,
                        "has_vat": has_vat,
                        "vat_amount": vat_amt,
                        "vat_alert": vat_alert,
                        "spell_warnings": spell_warnings
                    },
                    "formatted_rows": rows,
                    "buyer_address": parsed_data.get("customer_address") or "",
                    "approved": not is_dup, # Auto-approve if not duplicate
                    "engine_used": parsed_data.get("_engine_used", mode)
                }
                staged_results.append(item_entry)
            except Exception as item_err:
                logger.error(f"Error processing Drive file {file_name}: {item_err}")
                staged_results.append({
                    "drive_file_id": file_id,
                    "filename": file_name,
                    "error": str(item_err)
                })

        staged_receipts = staged_results
        return {
            "success": True,
            "total_processed": len(staged_results),
            "staged": staged_results
        }
    except Exception as e:
        logger.error(f"Batch processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/sheets/export")
async def export_to_google_sheet(payload: Dict[str, Any] = Body(...)):
    """
    Append approved rows into Google Sheet, export links, and auto-move files.
    Guarded with scan_lock and pre-append duplicate filtering.
    """
    rows_to_export: List[List[Any]] = payload.get("rows", [])
    if not rows_to_export:
        # Fallback to approved staged receipts
        for item in staged_receipts:
            if item.get("approved") and "formatted_rows" in item:
                rows_to_export.extend(item["formatted_rows"])

    if not rows_to_export:
        raise HTTPException(status_code=400, detail="Không có dòng dữ liệu nào được chọn để nhập vào Google Sheet.")

    async with scan_lock:
        try:
            # 0. Live check against current sheet rows
            sheet_rows = await asyncio.to_thread(google_service.get_sheet_data)
            checker = DuplicateChecker()
            checker.load_from_sheet_rows(sheet_rows)

            filtered_rows = []
            for r in rows_to_export:
                if not r or len(r) == 0:
                    continue
                dt_code = str(r[0]).strip() if len(r) > 0 else ""
                order_id = str(r[5]).strip() if len(r) > 5 else (str(r[4]).strip() if len(r) > 4 else "")
                
                # Check duplicate
                if dt_code and dt_code.startswith("DT") and checker.check_duplicate(dt_code)[0]:
                    logger.warning(f"Export skipped row {dt_code}: DT Code already exists in Sheet.")
                    continue
                if order_id and len(order_id) >= 6 and checker.check_duplicate(order_id)[0]:
                    logger.warning(f"Export skipped row {dt_code}: Order/Doc ID '{order_id}' already exists in Sheet.")
                    continue

                filtered_rows.append(r)
                if dt_code:
                    checker.add_code(dt_code)
                if order_id:
                    checker.add_code(order_id)

            if not filtered_rows:
                return {
                    "success": True,
                    "message": "Các dòng hóa đơn được chọn ĐÃ TỒN TẠI trên Google Sheet (hệ thống tự động bỏ qua để tránh trùng lặp).",
                    "updated_rows": 0
                }

            # 1. Export main data
            res = await asyncio.to_thread(google_service.append_rows_to_sheet, filtered_rows)
            
            # 2. Export links and move files
            exported_dt_codes = {row[0] for row in filtered_rows if row and len(row) > 0}
            link_rows = []
            moved_files = 0
            processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

            for item in staged_receipts:
                if "category" in item and "dt_code" in item["category"]:
                    dt_code = item["category"]["dt_code"]
                    if dt_code in exported_dt_codes:
                        drive_id = item.get("drive_file_id")
                        filename = item.get("filename", "")
                        
                        if drive_id:
                            link_url = f"https://drive.google.com/file/d/{drive_id}/view"
                            link_rows.append([dt_code, filename, link_url])
                            
                            if processed_folder_id:
                                if await asyncio.to_thread(google_service.move_file_to_folder, drive_id, processed_folder_id):
                                    moved_files += 1

            # Append unique link rows to secondary sheet
            unique_links = []
            seen_dt = set()
            for r in link_rows:
                if r[0] not in seen_dt:
                    unique_links.append(r)
                    seen_dt.add(r[0])
                    
            if unique_links:
                await asyncio.to_thread(google_service.append_links_to_sheet, unique_links)

            message = f"Đã nhập thành công {len(filtered_rows)} dòng vào Google Sheet!"
            if moved_files > 0:
                message += f" Đã di chuyển {moved_files} ảnh vào thư mục Đã Xử Lý."
                
            return {
                "success": True,
                "message": message,
                "details": res
            }
        except Exception as e:
            logger.error(f"Sheet export error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Lỗi nhập vào Google Sheet: {str(e)}")


@app.post("/api/v1/sheets/deduplicate")
async def trigger_deduplication():
    """
    Purge duplicate entries across Bang_Ke_Hoa_Don and Links_Hoa_Don.
    """
    async with scan_lock:
        try:
            res = await asyncio.to_thread(google_service.deduplicate_all_sheets)
            return res
        except Exception as e:
            logger.error(f"Deduplication error: {e}")
            raise HTTPException(status_code=500, detail=f"Lỗi khi dọn dẹp trùng lặp: {str(e)}")

@app.get("/api/v1/drive/image/{file_id}")
async def get_drive_image_proxy(file_id: str):
    """
    Stream image bytes from Google Drive for iframe/img rendering with client & disk caching.
    """
    try:
        content, mime = await asyncio.to_thread(google_service.download_file_bytes, file_id, True)
        return Response(
            content=content,
            media_type=mime or "image/jpeg",
            headers={
                "Cache-Control": "public, max-age=604800, immutable",
                "X-Cache-Status": "HIT"
            }
        )
    except Exception as e:
        logger.error(f"Error proxying Drive image {file_id}: {e}")
        raise HTTPException(status_code=404, detail=f"Không thể tải ảnh: {str(e)}")


@app.get("/api/v1/sheets/records")
async def get_historical_records():
    """
    Fetch historical records from Google Sheets for read-only verification.
    """
    try:
        records = await asyncio.to_thread(google_service.get_historical_records)
        return {
            "success": True,
            "records": records
        }
    except Exception as e:
        logger.error(f"Error fetching historical records: {e}")
        raise HTTPException(status_code=500, detail=f"Không thể tải lịch sử từ Sheet: {str(e)}")

@app.put("/api/v1/sheets/record")
async def update_sheet_record(payload: dict):
    """
    Update a specific record row in Google Sheet by dt_code.
    Expects: { dt_code, row: [A, B, C, ... M] }
    """
    dt_code = payload.get("dt_code", "").strip()
    row = payload.get("row", [])
    if not dt_code or not row:
        raise HTTPException(status_code=400, detail="Thiếu dt_code hoặc row data.")
    try:
        result = await asyncio.to_thread(google_service.update_sheet_row, dt_code, row)
        if result.get("success"):
            return {"success": True, "message": f"Đã cập nhật {result['updated_rows']} dòng cho {dt_code}."}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Cập nhật thất bại."))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật Sheet: {str(e)}")

@app.post("/api/v1/sheets/confirm")
async def confirm_sheet_record(payload: dict = Body(...)):
    """
    Confirm or reject a receipt in Links_Hoa_Don (marks 'x' in Column D if confirmed, clears if rejected).
    """
    dt_code = payload.get("dt_code", "").strip()
    status = payload.get("status", "x").strip()
    row = payload.get("row")
    if not dt_code:
        raise HTTPException(status_code=400, detail="Thiếu dt_code")
    try:
        if row and isinstance(row, list) and len(row) >= 11:
            await asyncio.to_thread(google_service.update_sheet_row, dt_code, row)

        res = await asyncio.to_thread(google_service.confirm_receipt_in_links, dt_code, status=status)
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "Lỗi xác nhận."))
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming sheet record: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi xác nhận: {str(e)}")

@app.delete("/api/v1/sheets/record/{dt_code}")
async def delete_sheet_record(dt_code: str):
    """
    Permanently delete a record from Bang_Ke_Hoa_Don and Links_Hoa_Don.
    """
    clean_dt = dt_code.strip()
    if not clean_dt:
        raise HTTPException(status_code=400, detail="Thiếu mã đối tượng dt_code.")
    async with scan_lock:
        try:
            res = await asyncio.to_thread(google_service.delete_sheet_record, clean_dt)
            if not res.get("success"):
                raise HTTPException(status_code=400, detail=res.get("error", "Xóa hóa đơn thất bại."))
            return res
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting sheet record {clean_dt}: {e}")
            raise HTTPException(status_code=500, detail=f"Lỗi khi xóa hóa đơn: {str(e)}")

@app.get("/api/v1/audit/reconciliation")
async def get_audit_reconciliation():
    """
    Detailed audit comparing all Google Drive files vs Google Sheet records.
    """
    try:
        res = await asyncio.to_thread(google_service.get_audit_reconciliation)
        return {"success": True, "data": res}
    except Exception as e:
        logger.error(f"Error generating audit reconciliation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tesseract/ocr")
@app.post("/api/v1/raw-ocr")
async def extract_raw_ocr(file: UploadFile = File(...), lang: str = Form("eng+vie")):
    """
    Extract raw text using Tesseract OCR engine without LLM.
    """
    if not pytesseract:
        raise HTTPException(status_code=500, detail="Tesseract OCR is not installed.")

    content = await file.read()
    try:
        image = Image.open(io.BytesIO(content))
        raw_text = pytesseract.image_to_string(image, lang=lang)
        return JSONResponse(content={
            "success": True,
            "filename": file.filename,
            "language": lang,
            "raw_text": raw_text.strip()
        })
    except Exception as e:
        logger.error(f"Tesseract OCR error: {e}")
        raise HTTPException(status_code=500, detail=f"Tesseract OCR failed: {str(e)}")


# =============================================================================
# WEB DASHBOARD (HTML / JS / CSS)
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def dashboard_ui():
    return """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="theme-color" content="#0b1120">
        <title>Procurement Receipt OCR | Control Center</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg: #F8FAFC;
                --bg-deep: #EFF4FB;
                --sidebar: #0F172A;
                --card: #FFFFFF;
                --card-soft: #F1F5FB;
                --card-hover: #E2ECFB;
                --line: rgba(37, 99, 235, .14);
                --line-strong: rgba(37, 99, 235, .26);
                --text: #0F172A;
                --muted: #64748B;
                --dim: #94A3B8;
                --blue: #2563EB;
                --blue-soft: #3B82F6;
                --violet: #06B6D4;
                --cyan: #06B6D4;
                --green: #10b981;
                --amber: #f59e0b;
                --red: #ef4444;
                --shadow: 0 8px 32px rgba(37, 99, 235, .10);
                --gradient: linear-gradient(135deg, #2563EB 0%, #06B6D4 100%);
            }

            * { box-sizing: border-box; }
            html { min-height: 100%; background: var(--bg); }
            body {
                min-height: 100vh;
                margin: 0;
                color: var(--text);
                background:
                    radial-gradient(circle at 80% -10%, rgba(6, 182, 212, .07), transparent 30rem),
                    radial-gradient(circle at 10% 100%, rgba(37, 99, 235, .06), transparent 26rem),
                    var(--bg);
                font-family: 'Inter', sans-serif;
                font-size: 14px;
            }
            button, input, select { font: inherit; }
            button { border: 0; }
            button:focus-visible, input:focus-visible, select:focus-visible {
                outline: 3px solid rgba(96, 165, 250, .4);
                outline-offset: 2px;
            }
            .app-shell { min-height: 100vh; }
            .sidebar {
                position: fixed;
                z-index: 20;
                inset: 0 auto 0 0;
                width: 240px;
                display: flex;
                flex-direction: column;
                padding: 25px 16px 18px;
                border-right: 1px solid rgba(37, 99, 235, .12);
                background: linear-gradient(180deg, #0F172A 0%, #0B1120 100%);
                box-shadow: 4px 0 30px rgba(37, 99, 235, .10);
            }
            .brand { display: flex; align-items: center; gap: 12px; padding: 0 10px 28px; }
            .brand-mark {
                width: 38px; height: 38px; display: grid; place-items: center; border-radius: 12px;
                color: white; font-size: 19px; background: var(--gradient); box-shadow: 0 10px 22px rgba(37, 99, 235, .35);
            }
            .brand-copy strong { display: block; letter-spacing: -.03em; font-size: 14px; }
            .brand-copy span { display: block; margin-top: 3px; color: var(--muted); font-size: 10px; letter-spacing: .09em; text-transform: uppercase; }
            .nav-label { padding: 0 12px 10px; color: var(--dim); font-size: 10px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
            .nav-list { display: grid; gap: 7px; }
            .nav-item {
                display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 12px; border: 1px solid transparent;
                border-radius: 12px; color: var(--muted); background: transparent; cursor: pointer; text-align: left;
                transition: transform .2s ease, color .2s ease, background .2s ease, border-color .2s ease;
            }
            .nav-item:hover { color: #DBEAFE; background: rgba(37, 99, 235, .12); transform: translateX(2px); }
            .nav-item.active { color: #DBEAFE; border-color: rgba(37, 99, 235, .3); background: linear-gradient(90deg, rgba(37, 99, 235, .28), rgba(6, 182, 212, .14)); box-shadow: inset 3px 0 0 var(--blue); }
            .nav-icon { width: 25px; text-align: center; font-size: 17px; }
            .nav-text { flex: 1; font-weight: 600; }
            .nav-count { min-width: 21px; padding: 2px 6px; border-radius: 999px; color: #bfdbfe; background: rgba(37, 99, 235, .25); font-size: 10px; text-align: center; }
            .sidebar-footer { margin-top: auto; padding: 16px 10px 0; border-top: 1px solid var(--line); }
            .connection-pill { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; }
            .pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 4px rgba(16, 185, 129, .1); }
            .sidebar-footer small { display: block; margin-top: 9px; color: var(--dim); font-size: 10px; }

            .main-content { min-height: 100vh; margin-left: 240px; padding: 32px 38px 48px; }
            .topbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 28px; }
            .eyebrow { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; color: var(--blue-soft); font-size: 11px; font-weight: 700; letter-spacing: .13em; text-transform: uppercase; }
            .eyebrow-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 13px var(--cyan); }
            h1 { margin: 0; color: #0F172A; font-size: clamp(25px, 3vw, 34px); letter-spacing: -.045em; line-height: 1.1; }
            .subtitle { max-width: 700px; margin: 10px 0 0; color: var(--muted); line-height: 1.65; }
            .topbar-actions { display: flex; align-items: center; gap: 10px; padding-top: 7px; }
            .top-chip { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: 1px solid rgba(37,99,235,.2); border-radius: 10px; color: var(--muted); background: #FFFFFF; font-size: 11px; }
            .top-chip strong { color: var(--text); font-size: 12px; }

            .tab-pane { display: none; animation: tabIn .28s ease both; }
            .tab-pane.active { display: block; }
            @keyframes tabIn { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: translateY(0); } }
            .section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
            .section-heading h2 { margin: 0; font-size: 20px; letter-spacing: -.03em; }
            .section-heading p { margin: 6px 0 0; color: var(--muted); }
            .card {
                padding: 22px; border: 1px solid var(--line); border-radius: 18px;
                background: #FFFFFF;
                box-shadow: var(--shadow); transition: border-color .2s ease, transform .2s ease;
            }
            .card:hover { border-color: rgba(37, 99, 235, .25); }
            .card + .card { margin-top: 18px; }
            .card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
            .card-head h3 { margin: 0; font-size: 16px; letter-spacing: -.02em; }
            .card-head p { margin: 6px 0 0; color: var(--muted); line-height: 1.55; }
            .muted { color: var(--muted); }
            .dim { color: var(--dim); }
            .gradient-text { background: var(--gradient); -webkit-background-clip: text; background-clip: text; color: transparent; }

            .btn {
                display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; padding: 10px 15px;
                border: 1px solid transparent; border-radius: 10px; color: white; background: var(--gradient); cursor: pointer; font-weight: 700; font-size: 12px;
                box-shadow: 0 8px 20px rgba(37, 99, 235, .18); transition: transform .2s ease, box-shadow .2s ease, filter .2s ease, opacity .2s ease;
            }
            .btn:hover:not(:disabled) { transform: translateY(-2px); filter: brightness(1.08); box-shadow: 0 12px 26px rgba(37, 99, 235, .28); }
            .btn:active:not(:disabled) { transform: translateY(0); }
            .btn:disabled { cursor: not-allowed; opacity: .52; box-shadow: none; }
            .btn-secondary { color: #1e40af; border-color: rgba(37,99,235,.3); background: rgba(37,99,235,.08); box-shadow: none; }
            .btn-secondary:hover:not(:disabled) { background: rgba(37,99,235,.15); }
            .btn-success { background: linear-gradient(135deg, #059669, #10b981); }
            .btn-danger { background: linear-gradient(135deg, #b91c1c, #ef4444); }
            .btn-ghost { min-height: 34px; padding: 7px 10px; color: var(--blue); background: transparent; box-shadow: none; }
            .btn-ghost:hover:not(:disabled) { background: rgba(37, 99, 235, .08); box-shadow: none; }
            .btn-sm { min-height: 33px; padding: 7px 10px; font-size: 11px; }
            .button-row { display: flex; flex-wrap: wrap; gap: 10px; }
            .spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.34); border-top-color: white; border-radius: 50%; animation: spin .7s linear infinite; }
            @keyframes spin { to { transform: rotate(360deg); } }

            .hero-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(240px, .7fr); gap: 18px; }
            .hero-card { position: relative; overflow: hidden; min-height: 195px; }
            .hero-card::after { content: ''; position: absolute; right: -70px; bottom: -95px; width: 280px; height: 280px; border-radius: 50%; background: radial-gradient(circle, rgba(96, 165, 250, .17), transparent 68%); pointer-events: none; }
            .hero-icon { display: grid; place-items: center; width: 45px; height: 45px; margin-bottom: 18px; border: 1px solid rgba(96, 165, 250, .24); border-radius: 14px; background: rgba(37, 99, 235, .16); font-size: 22px; }
            .hero-card h2 { max-width: 600px; margin: 0; font-size: 25px; letter-spacing: -.04em; }
            .hero-card p { max-width: 610px; margin: 11px 0 19px; color: var(--muted); line-height: 1.65; }
            .metric-card { display: flex; flex-direction: column; justify-content: space-between; min-height: 195px; }
            .metric-label { color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
            .metric-value { margin-top: 17px; font-size: 45px; font-weight: 800; letter-spacing: -.06em; }
            .metric-note { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 11px; }
            .metric-note .pulse { width: 6px; height: 6px; }

            .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 13px; }
            table { width: 100%; border-collapse: collapse; min-width: 760px; }
            th, td { padding: 13px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }
            th { color: #2563EB; background: #EFF4FB; font-size: 10px; letter-spacing: .1em; text-transform: uppercase; }
            td { color: #0F172A; font-size: 12px; }
            tr:last-child td { border-bottom: 0; }
            tbody tr { transition: background .18s ease; }
            tbody tr:hover { background: rgba(37, 99, 235, .04); }
            .file-cell { display: flex; align-items: center; gap: 10px; min-width: 180px; }
            .file-icon { display: grid; place-items: center; width: 30px; height: 30px; flex: 0 0 30px; border-radius: 9px; background: rgba(96,165,250,.12); }
            .file-name { max-width: 230px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
            .badge { display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border: 1px solid transparent; border-radius: 999px; font-size: 10px; font-weight: 800; letter-spacing: .02em; }
            .badge-dt1 { color: #1d4ed8; border-color: rgba(37,99,235,.32); background: rgba(37,99,235,.10); }
            .badge-dt2 { color: #047857; border-color: rgba(16,185,129,.32); background: rgba(16,185,129,.10); }
            .badge-dt3 { color: #b45309; border-color: rgba(245,158,11,.32); background: rgba(245,158,11,.10); }
            .badge-dt4 { color: #7c3aed; border-color: rgba(168,85,247,.32); background: rgba(168,85,247,.10); }
            .status-badge { color: #065f46; border-color: rgba(16,185,129,.3); background: rgba(16,185,129,.10); }
            .warning-list { display: grid; gap: 5px; min-width: 195px; }
            .warning { display: inline-flex; align-items: center; gap: 5px; width: fit-content; padding: 4px 7px; border-radius: 6px; font-size: 10px; line-height: 1.25; }
            .warning.red { color: #fca5a5; background: rgba(239, 68, 68, .13); }
            .warning.amber { color: #fcd34d; background: rgba(245, 158, 11, .13); }
            .empty-table { padding: 38px 18px; color: var(--muted); text-align: center; }

            .verify-layout { display: grid; grid-template-columns: minmax(360px, 1fr) minmax(360px, 1fr); gap: 18px; align-items: stretch; }
            .verify-form-card, .preview-card { min-height: 670px; }
            .verify-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 15px; margin-bottom: 17px; }
            .verify-title { display: flex; align-items: center; gap: 11px; }
            .verify-title h2 { margin: 0; font-size: 19px; letter-spacing: -.035em; }
            .verify-title p { margin: 5px 0 0; color: var(--muted); font-size: 11px; }
            .verify-counter { padding: 8px 10px; border: 1px solid var(--line); border-radius: 9px; color: var(--blue-soft); background: rgba(37,99,235,.1); font-size: 11px; font-weight: 800; white-space: nowrap; }
            .progress-track { height: 6px; margin: 11px 0 15px; overflow: hidden; border-radius: 99px; background: rgba(148,163,184,.13); }
            .progress-bar { height: 100%; border-radius: inherit; background: var(--gradient); transition: width .25s ease; }
            .verify-alerts { display: flex; flex-wrap: wrap; gap: 7px; min-height: 28px; margin-bottom: 5px; }
            .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 9px; border: 1px solid transparent; border-radius: 7px; font-size: 10px; font-weight: 800; }
            .chip.red { color: #fca5a5; border-color: rgba(239,68,68,.3); background: rgba(239,68,68,.14); }
            .chip.amber { color: #fcd34d; border-color: rgba(245,158,11,.3); background: rgba(245,158,11,.14); }
            .field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 13px; }
            .field { min-width: 0; }
            .field.full { grid-column: 1 / -1; }
            .field label { display: flex; align-items: center; gap: 6px; margin: 0 0 6px 2px; color: var(--muted); font-size: 10px; font-weight: 700; }
            .field input, .field textarea, .search-input, .engine-select {
                width: 100%; border: 1px solid rgba(37,99,235,.2); border-radius: 9px; color: #0F172A; background: #FFFFFF; font-size: 12px; transition: border-color .2s ease, background .2s ease;
            }
            .field input, .search-input, .engine-select { height: 38px; padding: 0 11px; }
            .field textarea { min-height: 58px; padding: 10px 11px; resize: vertical; }
            .field input:focus, .field textarea:focus, .search-input:focus, .engine-select:focus { border-color: #2563EB; background: #F0F6FF; outline: none; box-shadow: 0 0 0 3px rgba(37,99,235,.12); }
            .field input.money { color: #059669; font-size: 15px; font-weight: 800; }
            .verify-actions { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; margin-top: 20px; padding-top: 17px; border-top: 1px solid var(--line); }
            .verify-actions .btn { width: 100%; }
            .verify-actions .center-action { display: flex; align-items: center; justify-content: center; }
            .approve-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 39px; padding: 8px 12px; border: 1px solid rgba(16,185,129,.3); border-radius: 10px; color: #065f46; background: rgba(16,185,129,.12); cursor: pointer; font-size: 11px; font-weight: 800; transition: all .2s ease; }
            .approve-toggle.rejected { color: #991b1b; border-color: rgba(239,68,68,.3); background: rgba(239,68,68,.09); }
            .verify-search { display: flex; gap: 8px; margin-top: 13px; }
            .search-input { flex: 1; }
            .preview-card { display: flex; flex-direction: column; padding: 15px; overflow: hidden; }
            .preview-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 3px 4px 12px; }
            .preview-head strong { font-size: 12px; }
            .preview-head span { color: var(--muted); font-size: 10px; }
            .iframe-wrap { position: relative; flex: 1; min-height: 530px; overflow: hidden; border: 1px solid rgba(37,99,235,.15); border-radius: 13px; background: #EFF4FB; }
            .iframe-wrap iframe { display: block; width: 100%; height: 100%; min-height: 530px; border: 0; }
            .preview-empty, .verify-empty { display: grid; place-items: center; min-height: 530px; padding: 34px; color: var(--muted); text-align: center; }
            .empty-icon { display: grid; place-items: center; width: 62px; height: 62px; margin: 0 auto 16px; border: 1px solid rgba(37,99,235,.2); border-radius: 20px; color: var(--blue); background: rgba(37,99,235,.08); font-size: 28px; }
            .empty-copy h3 { margin: 0; color: var(--text); font-size: 16px; }
            .empty-copy p { max-width: 330px; margin: 9px auto 18px; line-height: 1.6; }
            .preview-footer { display: flex; justify-content: flex-end; padding-top: 12px; }

            .upload-grid { display: grid; grid-template-columns: minmax(0, 1.02fr) minmax(340px, .98fr); gap: 18px; }
            .upload-zone { display: grid; place-items: center; min-height: 240px; padding: 28px; border: 1.5px dashed rgba(37,99,235,.3); border-radius: 15px; color: var(--muted); background: rgba(37,99,235,.03); cursor: pointer; text-align: center; transition: border-color .2s ease, background .2s ease, transform .2s ease; }
            .upload-zone:hover, .upload-zone.dragover { border-color: var(--blue); background: rgba(37,99,235,.08); transform: translateY(-2px); }
            .upload-zone .upload-icon { margin-bottom: 12px; font-size: 34px; }
            .upload-zone strong { display: block; color: var(--text); }
            .upload-zone span { display: block; margin-top: 7px; font-size: 11px; }
            .control-label { display: block; margin: 0 0 7px; color: var(--muted); font-size: 11px; font-weight: 700; }
            .engine-select { color: var(--text); }
            .manual-controls { display: grid; gap: 15px; align-content: start; }
            .selected-file { display: flex; align-items: center; gap: 9px; min-height: 39px; padding: 9px 11px; border: 1px solid rgba(37,99,235,.2); border-radius: 9px; color: var(--muted); background: #F8FAFC; font-size: 11px; }
            .result-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 13px; }
            .result-kpi { padding: 12px; border: 1px solid rgba(37,99,235,.15); border-radius: 10px; background: #F1F5FB; }
            .result-kpi span { display: block; color: var(--muted); font-size: 10px; }
            .result-kpi strong { display: block; margin-top: 5px; color: #0F172A; font-size: 12px; }
            .result-fields { display: grid; gap: 8px; }
            .result-row { display: flex; justify-content: space-between; gap: 15px; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 11px; }
            .result-row span { color: var(--muted); }
            .result-row strong { color: #0F172A; text-align: right; }
            .result-row.total strong { color: #059669; font-size: 14px; }

            .status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
            .status-card { position: relative; min-height: 133px; overflow: hidden; padding: 17px; border: 1px solid rgba(37,99,235,.15); border-radius: 15px; background: #FFFFFF; }
            .status-card::after { content: ''; position: absolute; right: -25px; bottom: -35px; width: 100px; height: 100px; border-radius: 50%; background: var(--status-glow, rgba(37,99,235,.08)); filter: blur(2px); }
            .status-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
            .status-icon { font-size: 20px; }
            .status-state { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); box-shadow: 0 0 0 4px rgba(245,158,11,.11); }
            .status-state.ok { background: var(--green); box-shadow: 0 0 0 4px rgba(16,185,129,.11); }
            .status-state.error { background: var(--red); box-shadow: 0 0 0 4px rgba(239,68,68,.11); }
            .status-card h3 { margin: 16px 0 5px; font-size: 12px; color: #0F172A; }
            .status-card p { overflow: hidden; margin: 0; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
            .config-details { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 12px; }
            .detail-line { display: flex; justify-content: space-between; gap: 12px; padding: 12px; border: 1px solid rgba(37,99,235,.12); border-radius: 10px; background: #F1F5FB; font-size: 11px; }
            .detail-line span { color: var(--muted); }
            .detail-line strong { color: #1e40af; text-align: right; }

            .toast-stack { position: fixed; z-index: 50; right: 22px; bottom: 22px; display: grid; gap: 9px; width: min(360px, calc(100vw - 44px)); }
            .toast { display: flex; align-items: flex-start; gap: 10px; padding: 13px 14px; border: 1px solid var(--toast-border); border-radius: 12px; color: #0F172A; background: #FFFFFF; box-shadow: 0 8px 24px rgba(37,99,235,.14); animation: toastIn .25s ease both; font-size: 12px; line-height: 1.45; }
            .toast.success { --toast-border: rgba(16,185,129,.3); }
            .toast.error { --toast-border: rgba(239,68,68,.38); }
            .toast.info { --toast-border: rgba(96,165,250,.3); }
            @keyframes toastIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
            .toast-icon { font-size: 15px; }
            .toast-close { margin-left: auto; color: var(--muted); background: transparent; cursor: pointer; }
            .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

            @media (max-width: 1180px) {
                .main-content { padding-right: 25px; padding-left: 25px; }
                .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            }
            @media (max-width: 1023px) {
                .sidebar { position: static; width: 100%; min-height: auto; padding: 15px; border-right: 0; border-bottom: 1px solid var(--line); }
                .brand { padding-bottom: 15px; }
                .nav-label, .sidebar-footer { display: none; }
                .nav-list { grid-template-columns: repeat(4, minmax(0, 1fr)); }
                .nav-item { justify-content: center; padding: 10px 6px; }
                .nav-text { flex: initial; font-size: 11px; }
                .nav-count { display: none; }
                .main-content { margin-left: 0; padding: 25px 18px 40px; }
                .verify-layout { grid-template-columns: 1fr; }
                .verify-form-card, .preview-card { min-height: auto; }
                .preview-card { min-height: 560px; }
            }
            @media (max-width: 720px) {
                .topbar, .section-heading, .card-head { display: block; }
                .topbar-actions { margin-top: 17px; }
                .top-chip { width: fit-content; }
                .hero-grid, .upload-grid { grid-template-columns: 1fr; }
                .field-grid, .config-details { grid-template-columns: 1fr; }
                .field.full { grid-column: auto; }
                .verify-actions { grid-template-columns: 1fr 1fr; }
                .verify-actions .center-action { grid-column: 1 / -1; grid-row: 1; }
                .verify-actions .btn:first-child { grid-column: 1; }
                .verify-actions .btn:last-child { grid-column: 2; }
                .status-grid { grid-template-columns: 1fr 1fr; }
                .nav-text { display: none; }
                .nav-item { min-height: 43px; }
            }
            @media (max-width: 430px) {
                .main-content { padding-right: 12px; padding-left: 12px; }
                .status-grid { grid-template-columns: 1fr; }
                .card { padding: 16px; }
                .hero-card h2 { font-size: 21px; }
                .verify-top { display: block; }
                .verify-counter { display: inline-block; margin-top: 12px; }
                .verify-search { display: grid; }
            }
        </style>
    </head>
    <body>
        <div class="app-shell">
            <aside class="sidebar" aria-label="Điều hướng chính">
                <div class="brand">
                    <div class="brand-mark" aria-hidden="true">⌁</div>
                    <div class="brand-copy"><strong>Procurement OCR</strong><span>Control Center</span></div>
                </div>
                <div class="nav-label">Workspace</div>
                <nav class="nav-list" aria-label="Các tab dashboard">
                    <button class="nav-item active" data-tab="tab-drive" onclick="switchTab('tab-drive', this)" aria-label="Mở tab Tự Động Google Drive">
                        <span class="nav-icon">⚡</span><span class="nav-text">Tự Động Google Drive</span><span class="nav-count" id="driveNavCount">0</span>
                    </button>
                    <button class="nav-item" data-tab="tab-verify" onclick="switchTab('tab-verify', this)" aria-label="Mở tab Đối Chiếu Hóa Đơn">
                        <span class="nav-icon">🔍</span><span class="nav-text">Đối Chiếu Hóa Đơn</span><span class="nav-count" id="verifyNavCount">0</span>
                    </button>
                    <button class="nav-item" data-tab="tab-manual" onclick="switchTab('tab-manual', this)" aria-label="Mở tab Tải Lên Đơn Lẻ">
                        <span class="nav-icon">📤</span><span class="nav-text">Tải Lên Đơn Lẻ</span>
                    </button>
                    <button class="nav-item" data-tab="tab-config" onclick="switchTab('tab-config', this)" aria-label="Mở tab Cấu Hình AI và Engine">
                        <span class="nav-icon">⚙️</span><span class="nav-text">Cấu Hình AI &amp; Engine</span>
                    </button>
                </nav>
                <div class="sidebar-footer">
                    <div class="connection-pill"><span class="pulse"></span><span>Localhost API · Port 8000</span></div>
                    <small>v2.0 · Hybrid Online / Offline</small>
                </div>
            </aside>

            <main class="main-content">
                <header class="topbar">
                    <div>
                        <div class="eyebrow"><span class="eyebrow-dot"></span>Receipt intelligence workspace</div>
                        <h1 id="pageTitle">Tự Động Google Drive</h1>
                        <p class="subtitle" id="pageSubtitle">Quét, phân loại và chuẩn bị dữ liệu hóa đơn để kiểm tra trước khi xuất lên Google Sheet.</p>
                    </div>
                    <div class="topbar-actions">
                        <div class="top-chip"><span class="pulse"></span><span>System</span><strong id="topSystemState">Ready</strong></div>
                    </div>
                </header>

                <section id="tab-drive" class="tab-pane active" aria-labelledby="pageTitle">
                    <div class="hero-grid">
                        <div class="card hero-card">
                            <div class="hero-icon" aria-hidden="true">⚡</div>
                            <h2>Biến hóa đơn rời rạc thành <span class="gradient-text">dữ liệu sẵn sàng</span>.</h2>
                            <p>Quét thư mục Drive, chạy OCR hybrid, áp dụng quy tắc nghiệp vụ và duyệt từng kết quả trong một quy trình liền mạch.</p>
                            <div class="button-row">
                                <button class="btn" id="scanDriveBtn" onclick="scanDrive()" aria-label="Quét thư mục Google Drive">⌁ Quét Thư Mục Drive</button>
                                <button class="btn btn-secondary" id="processBatchBtn" onclick="processDriveBatch()" aria-label="Chạy OCR toàn bộ hóa đơn">▶ Chạy OCR Toàn Bộ</button>
                            </div>
                        </div>
                        <div class="card metric-card">
                            <div>
                                <div class="metric-label">Staged receipts</div>
                                <div class="metric-value gradient-text" id="stagedMetric">0</div>
                            </div>
                            <div class="metric-note"><span class="pulse"></span><span id="metricNote">Chưa có phiên xử lý hiện tại</span></div>
                        </div>
                    </div>

                    <div class="card" id="scanResultCard" style="display:none;">
                        <div class="card-head">
                            <div><h3>Danh sách file trong Drive</h3><p id="scanSummary">Các file chờ xử lý sẽ hiển thị tại đây.</p></div>
                            <span class="badge badge-dt1" id="scanCountBadge">0 file</span>
                        </div>
                        <div class="table-wrap"><table aria-label="Danh sách file Google Drive"><thead><tr><th>File</th><th>Loại</th><th>ID Drive</th><th>Trạng thái</th></tr></thead><tbody id="scanTableBody"></tbody></table></div>
                    </div>

                    <div class="card" id="batchResultCard" style="display:none;">
                        <div class="card-head">
                            <div><h3>Preview kết quả OCR &amp; cảnh báo</h3><p id="batchSummaryText">Chưa có kết quả.</p></div>
                            <div class="button-row">
                                <button class="btn btn-secondary btn-sm" id="verifyNowBtn" onclick="switchTab('tab-verify')" aria-label="Mở đối chiếu hóa đơn">🔍 Đối Chiếu Ngay</button>
                                <button class="btn btn-success btn-sm" id="exportSheetBtn" onclick="exportToSheet()" aria-label="Xuất các hóa đơn đã duyệt lên Google Sheet">↗ Xuất Lên Google Sheet</button>
                            </div>
                        </div>
                        <div class="table-wrap"><table id="batchTable" aria-label="Bảng kết quả OCR"><thead><tr><th>Duyệt</th><th>File hóa đơn</th><th>Đối tượng</th><th>Mã DT</th><th>Cảnh báo</th><th>Chi tiết</th></tr></thead><tbody id="batchTableBody"></tbody></table></div>
                    </div>
                </section>

                <section id="tab-verify" class="tab-pane" aria-labelledby="pageTitle">
                    <div class="section-heading">
                        <div><h2>🔍 Đối Chiếu Hóa Đơn</h2><p>Kiểm tra từng hóa đơn trước khi chấp thuận đưa vào Google Sheet.</p></div>
                        <div class="button-row">
                            <button class="btn btn-secondary btn-sm" id="loadHistoryBtn" onclick="fetchHistoricalRecords()" aria-label="Tải lịch sử từ Sheet">📥 Tải Lịch Sử Cũ</button>
                            <span class="badge status-badge" id="verifyModeBadge">Review queue</span>
                        </div>
                    </div>
                    <div id="verifyEmpty" class="card verify-empty">
                        <div class="empty-copy"><div class="empty-icon">🔍</div><h3>Chưa có hóa đơn để đối chiếu</h3><p>Hãy chạy OCR từ Google Drive hoặc bấm "Tải Lịch Sử Cũ" để xem lại các đơn đã lưu.</p><button class="btn" onclick="switchTab('tab-drive')" aria-label="Chuyển đến tab Google Drive">⚡ Quét Google Drive Ngay</button></div>
                    </div>
                    <div id="verifyWorkspace" class="verify-layout" style="display:none;">
                        <div class="card verify-form-card">
                            <div class="verify-top"><div class="verify-title"><span class="nav-icon">🔍</span><div><h2>Thông tin hóa đơn</h2><p>Biểu mẫu chỉ đọc · review từng chứng từ</p></div></div><div class="verify-counter" id="verifyCounter">0 / 0</div></div>
                            <div id="verifyCategory"></div>
                            <div class="progress-track" aria-label="Tiến độ đối chiếu"><div class="progress-bar" id="verifyProgress" style="width:0%;"></div></div>
                            <div class="verify-alerts" id="verifyAlerts"></div>
                            <div class="field-grid" id="verifyFields"></div>
                            <div class="verify-actions">
                                <button class="btn btn-secondary" id="prevBtn" onclick="navigateReceipt(-1)" aria-label="Xem hóa đơn trước">◀ Trước</button>
                                <div class="center-action" style="display:flex; gap:10px;">
                                    <button class="btn btn-sm btn-danger" id="rejectBtn" onclick="handleRejectReceipt()" aria-label="Từ chối hóa đơn">❌ Từ Chối</button>
                                    <button class="btn btn-sm btn-success" id="confirmBtn" onclick="handleConfirmReceipt()" aria-label="Xác nhận hóa đơn">✔ Xác Nhận</button>
                                </div>
                                <button class="btn btn-secondary" id="nextBtn" onclick="navigateReceipt(1)" aria-label="Xem hóa đơn tiếp theo">Tiếp ▶</button>
                            </div>
                            <div class="verify-search" id="searchWrap"><label class="sr-only" for="receiptSearch">Tìm theo mã đối tượng</label><input class="search-input" id="receiptSearch" type="search" placeholder="🔍 Tìm theo mã DT..." onkeydown="searchReceipt(event)"><button class="btn btn-ghost" onclick="searchReceipt({key:'Enter'})" aria-label="Tìm hóa đơn">Tìm</button></div>
                            <div class="verify-search" id="historySelectWrap" style="display:none; margin-top:10px;">
                                <div style="display:flex; flex-direction:column; gap:6px; width:100%;">
                                    <input class="search-input" id="historyInput" type="search" list="historyOptions" placeholder="🔍 Nhập mã DT rồi Enter..." oninput="onHistoryInput(this.value)" onkeydown="if(event.key==='Enter') loadHistoricalRecord(this.value.split(' ')[0].trim())" style="flex:1;">
                                    <datalist id="historyOptions"></datalist>
                                    <select class="search-input" id="historySelect" onchange="loadHistoricalRecord(this.value); document.getElementById('historyInput').value=this.value;" style="font-size:11px;"><option value="">-- Hoặc chọn từ danh sách --</option></select>
                                </div>
                            </div>
                        </div>
                        <div class="card preview-card">
                            <div class="preview-head"><strong>Ảnh hóa đơn gốc</strong><span id="previewFileName">—</span></div>
                            <div class="iframe-wrap" id="receiptPreview"><div class="preview-empty"><div class="empty-copy"><div class="empty-icon">🖼️</div><h3>Chọn một hóa đơn</h3><p>Ảnh hoặc PDF từ Google Drive sẽ hiển thị tại đây.</p></div></div></div>
                            <div class="preview-footer"><button class="btn btn-secondary btn-sm" id="fullscreenBtn" onclick="openCurrentReceipt()" disabled aria-label="Xem ảnh hóa đơn toàn màn hình">↗ Xem toàn màn hình</button></div>
                        </div>
                    </div>
                </section>

                <section id="tab-manual" class="tab-pane" aria-labelledby="pageTitle">
                    <div class="section-heading"><div><h2>📤 Tải Lên Đơn Lẻ</h2><p>Phân tích nhanh một ảnh hóa đơn trực tiếp từ máy tính của bạn.</p></div><span class="badge badge-dt2">Single receipt</span></div>
                    <div class="upload-grid">
                        <div class="card"><div class="card-head"><div><h3>Chọn ảnh hóa đơn</h3><p>JPG, PNG hoặc WEBP · tối đa 10MB</p></div></div><div class="upload-zone" id="uploadZone" onclick="document.getElementById('manualFileInput').click()" role="button" tabindex="0" aria-label="Chọn hoặc kéo thả ảnh hóa đơn"><div><div class="upload-icon">☁</div><strong id="manualLabel">Kéo thả ảnh vào đây hoặc nhấn để chọn</strong><span>OCR sẽ tự động nhận diện và chuẩn hóa dữ liệu.</span></div></div><input id="manualFileInput" type="file" accept="image/*" hidden onchange="handleManualFile(event)"><div class="selected-file" id="selectedFile">📎 Chưa chọn file</div></div>
                        <div class="card manual-controls"><div><label class="control-label" for="manualEngineMode">Engine xử lý</label><select class="engine-select" id="manualEngineMode" aria-label="Chọn engine OCR"><option value="auto">Auto · Cloud ưu tiên, Offline fallback</option><option value="cloud">Cloud · Gemini / OpenAI</option><option value="ollama">Offline · Ollama Local</option></select></div><button class="btn" id="manualSubmitBtn" onclick="processManualReceipt()" aria-label="Bắt đầu phân tích hóa đơn">▶ Bắt Đầu Phân Tích</button><div id="manualResultBox" style="display:none;"><div class="card-head"><div><h3>Kết quả đã chuẩn hóa</h3><p id="manualEngineUsed">—</p></div><span id="manualCategoryBadge"></span></div><div id="manualAlertContainer"></div><div id="manualResultContent"></div></div></div>
                    </div>
                </section>

                <section id="tab-config" class="tab-pane" aria-labelledby="pageTitle">
                    <div class="section-heading"><div><h2>⚙️ Cấu Hình AI &amp; Engine</h2><p>Quan sát nhanh tình trạng kết nối của pipeline OCR và Google services.</p></div><button class="btn btn-secondary btn-sm" onclick="fetchStatus(true)" aria-label="Làm mới trạng thái hệ thống">↻ Làm Mới</button></div>
                    <div class="status-grid" id="statusCards"><div class="status-card"><div class="status-top"><span class="status-icon">🤖</span><span class="status-state"></span></div><h3>AI Engine</h3><p>Đang tải...</p></div><div class="status-card"><div class="status-top"><span class="status-icon">☁️</span><span class="status-state"></span></div><h3>Gemini Cloud</h3><p>Đang tải...</p></div><div class="status-card"><div class="status-top"><span class="status-icon">🏠</span><span class="status-state"></span></div><h3>Ollama Local</h3><p>Đang tải...</p></div><div class="status-card"><div class="status-top"><span class="status-icon">📊</span><span class="status-state"></span></div><h3>Google Sheet</h3><p>Đang tải...</p></div></div>
                    <div class="card"><div class="card-head"><div><h3>Thông tin hệ thống</h3><p>Thông số kết nối được lấy trực tiếp từ API localhost.</p></div></div><div class="config-details" id="statusDetails"><div class="detail-line"><span>Đang tải</span><strong>—</strong></div></div></div>
                </section>
            </main>
        </div>
        <div class="toast-stack" id="toastStack" aria-live="polite" aria-atomic="true"></div>

        <script>
            // Shared global staging store required by the dashboard contract.
            window.stagedData = [];
            window.historicalData = [];
            window.isHistoryMode = false;
            let currentReceiptIndex = 0;
            let scannedDriveFiles = [];
            let manualSelectedFile = null;
            const numberFormatter = new Intl.NumberFormat('vi-VN');
            const tabMeta = {
                'tab-drive': { title: 'Tự Động Google Drive', subtitle: 'Quét, phân loại và chuẩn bị dữ liệu hóa đơn để kiểm tra trước khi xuất lên Google Sheet.' },
                'tab-verify': { title: 'Đối Chiếu Hóa Đơn', subtitle: 'Review từng chứng từ với ảnh gốc trước khi đưa dữ liệu vào Google Sheet.' },
                'tab-manual': { title: 'Tải Lên Đơn Lẻ', subtitle: 'Phân tích nhanh một ảnh hóa đơn trực tiếp từ máy tính của bạn.' },
                'tab-config': { title: 'Cấu Hình AI & Engine', subtitle: 'Quan sát nhanh tình trạng kết nối của pipeline OCR và Google services.' }
            };

            function escapeHtml(value) {
                return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
            }
            function cleanValue(value) { return String(value ?? '').replace(/^'+/, '').trim(); }
            function formatMoney(value) {
                const numeric = Number(String(value ?? '').replace(/[^0-9.-]/g, ''));
                return Number.isFinite(numeric) ? numberFormatter.format(numeric) : '—';
            }
            function formatText(value, fallback = '—') { const clean = cleanValue(value); return clean || fallback; }
            function showToast(message, type = 'info') {
                const stack = document.getElementById('toastStack');
                const toast = document.createElement('div');
                const icon = type === 'success' ? '✓' : type === 'error' ? '!' : 'i';
                toast.className = `toast ${type}`;
                toast.innerHTML = `<span class="toast-icon">${icon}</span><span>${escapeHtml(message)}</span><button class="toast-close" aria-label="Đóng thông báo">×</button>`;
                toast.querySelector('.toast-close').onclick = () => toast.remove();
                stack.appendChild(toast);
                window.setTimeout(() => toast.remove(), 5200);
            }
            function setLoading(button, loading, label) {
                if (!button) return;
                button.disabled = loading;
                button.innerHTML = loading ? `<span class="spinner" aria-hidden="true"></span> Đang xử lý...` : label;
            }
            async function apiFetch(url, options = {}, button = null, label = '') {
                setLoading(button, true, label);
                try {
                    const response = await fetch(url, options);
                    let data = {};
                    try { data = await response.json(); } catch (_) { data = {}; }
                    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                    return data;
                } finally {
                    setLoading(button, false, label);
                }
            }
            function switchTab(tabId, element = null) {
                document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.toggle('active', pane.id === tabId));
                document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.tab === tabId));
                const meta = tabMeta[tabId] || tabMeta['tab-drive'];
                document.getElementById('pageTitle').textContent = meta.title;
                document.getElementById('pageSubtitle').textContent = meta.subtitle;
                if (tabId === 'tab-verify') renderCurrentReceipt();
                if (tabId === 'tab-config') fetchStatus();
            }
            function updateStagedCounters() {
                const count = window.stagedData.length;
                document.getElementById('stagedMetric').textContent = count;
                document.getElementById('verifyNavCount').textContent = count;
                document.getElementById('driveNavCount').textContent = count;
                document.getElementById('metricNote').textContent = count ? `${count} hóa đơn trong hàng đợi đối chiếu` : 'Chưa có phiên xử lý hiện tại';
            }
            function getCategory(item) { return item?.category || { id: 4, name: 'Khác', dt_code: 'DT4' }; }
            function getRow(item) { return Array.isArray(item?.formatted_rows) ? item.formatted_rows[0] || [] : []; }
            function warningMarkup(warnings = {}) {
                const parts = [];
                if (warnings.is_duplicate) parts.push(`<span class="warning red">⚠ ${escapeHtml(warnings.duplicate_message || 'Trùng mã chứng từ')}</span>`);
                if (warnings.has_vat) parts.push(`<span class="warning amber">◈ ${escapeHtml(warnings.vat_alert || `Có VAT ${warnings.vat_amount || ''}`)}</span>`);
                if (warnings.spell_warnings?.length) parts.push(`<span class="warning amber">✎ ${warnings.spell_warnings.length} nghi vấn chính tả</span>`);
                return parts.length ? `<div class="warning-list">${parts.join('')}</div>` : '<span class="status-badge badge">✓ Hợp lệ</span>';
            }
            function renderScanTable(files) {
                const body = document.getElementById('scanTableBody');
                body.innerHTML = files.length ? files.map(file => `<tr><td><div class="file-cell"><span class="file-icon">🖼️</span><span class="file-name" title="${escapeHtml(file.name || file.filename)}">${escapeHtml(file.name || file.filename || '—')}</span></div></td><td>${escapeHtml(file.mimeType || file.mime_type || 'image')}</td><td class="dim">${escapeHtml(file.id || file.drive_file_id || '—')}</td><td><span class="status-badge badge">Sẵn sàng</span></td></tr>`).join('') : '<tr><td colspan="4"><div class="empty-table">Không tìm thấy file ảnh nào trong thư mục Drive.</div></td></tr>';
                document.getElementById('scanSummary').textContent = files.length ? `${files.length} file ảnh đang chờ được OCR.` : 'Không có file chờ xử lý.';
                document.getElementById('scanCountBadge').textContent = `${files.length} file`;
            }
            async function scanDrive() {
                const button = document.getElementById('scanDriveBtn');
                try {
                    const result = await apiFetch('/api/v1/drive/scan', { method: 'POST' }, button, '⌁ Quét Thư Mục Drive');
                    scannedDriveFiles = result.files || [];
                    document.getElementById('scanResultCard').style.display = 'block';
                    renderScanTable(scannedDriveFiles);
                    showToast(`Đã quét ${scannedDriveFiles.length} file từ Google Drive.`, 'success');
                } catch (error) { showToast(`Không thể quét Drive: ${error.message}`, 'error'); }
            }
            async function processDriveBatch() {
                const button = document.getElementById('processBatchBtn');
                try {
                    const result = await apiFetch('/api/v1/drive/process-batch', { method: 'POST' }, button, '▶ Chạy OCR Toàn Bộ');
                    if (!result.success) throw new Error(result.detail || 'Xử lý batch thất bại');
                    window.stagedData = Array.isArray(result.staged) ? result.staged : [];
                    currentReceiptIndex = 0;
                    updateStagedCounters();
                    renderBatchTable(window.stagedData);
                    document.getElementById('batchResultCard').style.display = 'block';
                    document.getElementById('verifyNowBtn').style.display = window.stagedData.length ? 'inline-flex' : 'none';
                    document.getElementById('exportSheetBtn').style.display = window.stagedData.length ? 'inline-flex' : 'none';
                    showToast(`OCR hoàn tất: ${window.stagedData.length} hóa đơn đã sẵn sàng đối chiếu.`, 'success');
                } catch (error) { showToast(`Không thể chạy OCR batch: ${error.message}`, 'error'); }
            }
            function renderBatchTable(items) {
                const body = document.getElementById('batchTableBody');
                document.getElementById('batchSummaryText').textContent = `${items.length} hóa đơn đã được phân tích · ${items.filter(item => item.approved).length} đang được duyệt`;
                body.innerHTML = items.length ? items.map((item, index) => {
                    if (item.error) return `<tr><td>—</td><td><div class="file-cell"><span class="file-icon">!</span><span class="file-name">${escapeHtml(item.filename)}</span></div></td><td><span class="warning red">Lỗi xử lý</span></td><td>—</td><td><span class="warning red">${escapeHtml(item.error)}</span></td><td>—</td></tr>`;
                    const cat = getCategory(item);
                    return `<tr><td><input type="checkbox" ${item.approved ? 'checked' : ''} onchange="toggleApprove(${index}, this.checked)" aria-label="Duyệt hóa đơn ${escapeHtml(item.filename)}"></td><td><div class="file-cell"><span class="file-icon">🧾</span><span class="file-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span></div></td><td><span class="badge badge-dt${cat.id}">DT${cat.id} · ${escapeHtml(cat.name)}</span></td><td><strong>${escapeHtml(cat.dt_code || `DT${cat.id}`)}</strong></td><td>${warningMarkup(item.warnings)}</td><td><button class="btn btn-ghost btn-sm" onclick="jumpToReceipt(${index})" aria-label="Mở đối chiếu ${escapeHtml(item.filename)}">🔍 Mở</button></td></tr>`;
                }).join('') : '<tr><td colspan="6"><div class="empty-table">Chưa có dữ liệu. Hãy chạy OCR từ Google Drive.</div></td></tr>';
            }
            function toggleApprove(index, approved) { if (window.stagedData[index]) { window.stagedData[index].approved = approved; updateStagedCounters(); renderBatchTable(window.stagedData); } }
            function jumpToReceipt(index) { currentReceiptIndex = index; switchTab('tab-verify'); renderCurrentReceipt(); }
            function renderCurrentReceipt() {
                const dataArray = window.isHistoryMode ? window.historicalData : window.stagedData;
                const hasData = dataArray.length > 0;
                document.getElementById('verifyEmpty').style.display = hasData ? 'none' : 'grid';
                document.getElementById('verifyWorkspace').style.display = hasData ? 'grid' : 'none';
                if (!hasData) return;
                currentReceiptIndex = Math.max(0, Math.min(currentReceiptIndex, dataArray.length - 1));
                const item = dataArray[currentReceiptIndex];
                const row = getRow(item);
                const cat = getCategory(item);
                const warnings = item.warnings || {};
                document.getElementById('verifyCounter').textContent = `${currentReceiptIndex + 1} / ${dataArray.length}`;
                document.getElementById('verifyProgress').style.width = `${((currentReceiptIndex + 1) / dataArray.length) * 100}%`;
                document.getElementById('verifyCategory').innerHTML = `<span class="badge badge-dt${cat.id}">DT${cat.id} · ${escapeHtml(cat.name)}</span>`;
                const alertParts = [];
                if (warnings.is_duplicate) alertParts.push(`<span class="chip red">⚠ TRÙNG LẶP</span>`);
                if (warnings.has_vat) alertParts.push(`<span class="chip amber">◈ CÓ VAT ${escapeHtml(warnings.vat_amount ? formatMoney(warnings.vat_amount) : '')}</span>`);
                document.getElementById('verifyAlerts').innerHTML = alertParts.join('');
                const fields = [
                    ['code', '#', 'Mã đối tượng', row[0]],
                    ['datetime', '◷', 'Ngày / Giờ', row[1]],
                    ['company', '▣', 'Tên công ty', row[2]],
                    ['address', '⌖', 'Địa chỉ bên bán', row[3]],
                    ['addressBuyer', '⌖', 'Địa chỉ bên nhận', row[4] || item.buyer_address || ''],
                    ['invoice', '⌑', 'Mã hóa đơn / chứng từ', row[5]],
                    ['product', '▧', 'Tên hàng hóa', row[6]],
                    ['quantity', '∷', 'Số lượng', row[7]],
                    ['unitPrice', '₫', 'Đơn giá (Chưa VAT)', row[8], true],
                    ['vatRate', '%', '% VAT', row[9]],
                    ['vatAmount', '◇', 'Tiền VAT', row[10], true],
                    ['total', '◆', 'Thành tiền', row[11], true, true],
                    ['receiver', '♙', 'Người mua / nhận hàng', row[12]],
                    ['note', '✎', 'Ghi chú', row[13], false, false, true]
                ];
                document.getElementById('verifyFields').innerHTML = fields.map(([id, icon, label, value, money, total, textarea]) => `<div class="field ${textarea ? 'full' : ''}"><label for="verify-${id}"><span>${icon}</span>${label}</label>${textarea ? `<textarea id="verify-${id}" data-field="${id}">${escapeHtml(formatText(value, ''))}</textarea>` : `<input id="verify-${id}" class="${money ? 'money' : ''}" data-field="${id}" data-raw="${escapeHtml(String(value ?? ''))}" value="${escapeHtml(money ? (formatMoney(value) + ' VNĐ') : formatText(value))}">`}</div>`).join('');
                
                // Update button states
                const isConfirmed = window.isHistoryMode ? !!item.confirmed : !!item.approved;
                const confirmBtn = document.getElementById('confirmBtn');
                const rejectBtn = document.getElementById('rejectBtn');
                
                if (isConfirmed) {
                    confirmBtn.textContent = window.isHistoryMode ? '✔ Đã Xác Nhận (x)' : '✔ Đã Duyệt';
                    confirmBtn.style.boxShadow = '0 0 0 3px rgba(16, 185, 129, .4)';
                    confirmBtn.style.opacity = '1';
                    rejectBtn.textContent = '❌ Từ Chối';
                    rejectBtn.style.boxShadow = 'none';
                    rejectBtn.style.opacity = '0.7';
                } else {
                    confirmBtn.textContent = '✔ Xác Nhận';
                    confirmBtn.style.boxShadow = 'none';
                    confirmBtn.style.opacity = '0.7';
                    rejectBtn.textContent = window.isHistoryMode ? '❌ Chưa Xác Nhận' : '❌ Đã Từ Chối';
                    rejectBtn.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, .35)';
                    rejectBtn.style.opacity = '1';
                }
                
                document.getElementById('prevBtn').disabled = currentReceiptIndex === 0;
                document.getElementById('nextBtn').disabled = currentReceiptIndex === dataArray.length - 1;
                document.getElementById('previewFileName').textContent = item.filename || '—';
                const preview = document.getElementById('receiptPreview');
                const url = item.drive_file_id ? `https://drive.google.com/file/d/${encodeURIComponent(item.drive_file_id)}/preview` : '';
                const fullscreen = document.getElementById('fullscreenBtn');
                fullscreen.disabled = !url;
                fullscreen.dataset.url = url;
                preview.innerHTML = url ? `<iframe src="${url}" title="Ảnh hóa đơn ${escapeHtml(item.filename || '')}" loading="lazy" allow="autoplay"></iframe>` : `<div class="preview-empty"><div class="empty-copy"><div class="empty-icon">!</div><h3>Không có link ảnh</h3><p>Hóa đơn này không chứa drive_file_id để xem ảnh gốc.</p></div></div>`;
            }
            function navigateReceipt(direction) { 
                const dataArray = window.isHistoryMode ? window.historicalData : window.stagedData;
                if (!dataArray.length) return; 
                currentReceiptIndex = Math.max(0, Math.min(dataArray.length - 1, currentReceiptIndex + direction)); 
                renderCurrentReceipt(); 
                if (window.isHistoryMode) {
                    document.getElementById('historySelect').value = dataArray[currentReceiptIndex].category.dt_code;
                }
            }
            function toggleCurrentApproval() { if (window.isHistoryMode || !window.stagedData.length) return; window.stagedData[currentReceiptIndex].approved = !window.stagedData[currentReceiptIndex].approved; renderCurrentReceipt(); renderBatchTable(window.stagedData); updateStagedCounters(); }
            function searchReceipt(event) {
                if (event.key !== 'Enter') return;
                const dataArray = window.isHistoryMode ? window.historicalData : window.stagedData;
                if (!dataArray.length) return;
                const query = document.getElementById('receiptSearch').value.trim().toLowerCase();
                if (!query) return;
                const index = dataArray.findIndex(item => String(getRow(item)[0] || '').toLowerCase().includes(query) || String(item.category?.dt_code || '').toLowerCase().includes(query));
                if (index < 0) { showToast(`Không tìm thấy hóa đơn với mã “${query}”.`, 'error'); return; }
                currentReceiptIndex = index; renderCurrentReceipt(); showToast(`Đã mở hóa đơn ${index + 1}/${dataArray.length}.`, 'success');
                if (window.isHistoryMode) {
                    document.getElementById('historySelect').value = dataArray[currentReceiptIndex].category.dt_code;
                }
            }
            function exportToSheet_guard() { showToast('Không thể xuất khi đang ở chế độ Xem Lịch Sử.', 'error'); }
            
            function getEditedFormRow() {
                const fieldOrder = ['code', 'datetime', 'company', 'address', 'addressBuyer', 'invoice', 'product', 'quantity', 'unitPrice', 'vatRate', 'vatAmount', 'total', 'receiver', 'note'];
                return fieldOrder.map(id => {
                    const el = document.getElementById(`verify-${id}`);
                    if (!el) return '';
                    // For money fields, strip ' VND' formatting to get raw number
                    let val = el.tagName === 'TEXTAREA' ? el.value : el.value;
                    // Remove VND suffix and thousand-separator dots
                    val = val.replace(/\s*VNĐ$/, '').replace(/\./g, '').replace(/,/g, '.');
                    return isNaN(Number(val)) || val.trim() === '' ? el.value.replace(/\s*VNĐ$/, '').trim() : val.trim();
                });
            }
            
            async function handleConfirmReceipt() {
                const dataArray = window.isHistoryMode ? window.historicalData : window.stagedData;
                if (!dataArray.length) return;
                const item = dataArray[currentReceiptIndex];
                const newRow = getEditedFormRow();
                const dtCode = item.category?.dt_code || newRow[0];
                item.formatted_rows[0] = newRow;

                const btn = document.getElementById('confirmBtn');
                if (window.isHistoryMode) {
                    try {
                        const res = await apiFetch('/api/v1/sheets/confirm', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ dt_code: dtCode, status: 'x', row: newRow })
                        }, btn, '✔ Xác Nhận');
                        item.confirmed = true;
                        renderCurrentReceipt();
                        showToast(`Đã xác nhận ${dtCode} và đánh dấu chữ 'x' vào trang tính 2!`, 'success');
                    } catch (e) {
                        showToast('Lỗi xác nhận: ' + e.message, 'error');
                    }
                } else {
                    item.approved = true;
                    renderCurrentReceipt();
                    renderBatchTable(window.stagedData);
                    updateStagedCounters();
                    showToast(`Đã duyệt hóa đơn ${dtCode}.`, 'success');
                }
            }

            async function handleRejectReceipt() {
                const dataArray = window.isHistoryMode ? window.historicalData : window.stagedData;
                if (!dataArray.length) return;
                const item = dataArray[currentReceiptIndex];
                const newRow = getEditedFormRow();
                const dtCode = item.category?.dt_code || newRow[0];
                item.formatted_rows[0] = newRow;

                const btn = document.getElementById('rejectBtn');
                if (window.isHistoryMode) {
                    try {
                        const res = await apiFetch('/api/v1/sheets/confirm', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ dt_code: dtCode, status: '', row: newRow })
                        }, btn, '❌ Từ Chối');
                        item.confirmed = false;
                        renderCurrentReceipt();
                        showToast(`Đã hủy xác nhận (xóa dấu 'x') cho ${dtCode} trên trang tính 2!`, 'info');
                    } catch (e) {
                        showToast('Lỗi từ chối: ' + e.message, 'error');
                    }
                } else {
                    item.approved = false;
                    renderCurrentReceipt();
                    renderBatchTable(window.stagedData);
                    updateStagedCounters();
                    showToast(`Đã từ chối hóa đơn ${dtCode}.`, 'info');
                }
            }
            async function exportToSheet() {
                if (window.isHistoryMode) { showToast('Không thể xuất khi đang ở chế độ Xem Lịch Sử.', 'error'); return; }
                const approvedRows = window.stagedData.filter(item => item.approved && item.formatted_rows).flatMap(item => item.formatted_rows);
                if (!approvedRows.length) { showToast('Vui lòng duyệt ít nhất một hóa đơn hợp lệ trước khi xuất.', 'error'); return; }
                const button = document.getElementById('exportSheetBtn');
                try {
                    const data = await apiFetch('/api/v1/sheets/export', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ rows: approvedRows }) }, button, '↗ Xuất Lên Google Sheet');
                    showToast(data.message || 'Đã xuất dữ liệu lên Google Sheet.', 'success');
                } catch (error) { showToast(`Xuất Google Sheet thất bại: ${error.message}`, 'error'); }
            }
            
            async function fetchHistoricalRecords() {
                const btn = document.getElementById('loadHistoryBtn');
                try {
                    const res = await apiFetch('/api/v1/sheets/records', {}, btn, '📥 Tải Lịch Sử Cũ');
                    if (res.records && res.records.length > 0) {
                        window.isHistoryMode = true;
                        document.getElementById('verifyModeBadge').textContent = 'History Mode';
                        document.getElementById('verifyModeBadge').className = 'badge badge-dt3';
                        document.getElementById('searchWrap').style.display = 'none';
                        document.getElementById('historySelectWrap').style.display = 'block';
                        
                        // Map records to stagedData format
                        window.historicalData = res.records.map(r => {
                            let catStr = r.dt_code.substring(2, 3);
                            let cat_id = parseInt(catStr) || 4;
                            let drive_id = '';
                            if (r.file_info.link) {
                                let match = r.file_info.link.match(/\/d\/(.+?)\//);
                                if (match) drive_id = match[1];
                            }
                            return {
                                category: { id: cat_id, name: 'Lịch sử Sheet', dt_code: r.dt_code },
                                formatted_rows: [
                                    [r.dt_code, r.datetime, r.merchant, r.address_seller || '', r.address_buyer || '', r.order_id, 
                                     r.items.map(i => i.item_name).join('\\\\n'), 
                                     r.items.reduce((sum, i) => sum + Number(i.quantity||0), 0),
                                     r.items[0]?.price || 0,
                                     r.items[0]?.vat_rate || 0,
                                     r.items.reduce((sum, i) => sum + Number(i.vat_amount||0), 0),
                                     r.items.reduce((sum, i) => sum + Number(i.row_total||0), 0),
                                     r.customer, r.notes]
                                ],
                                warnings: {},
                                approved: true,
                                confirmed: !!r.confirmed,
                                filename: r.file_info.file_name || 'Lịch sử',
                                drive_file_id: drive_id
                            };
                        });
                        
                        // Populate dropdown
                        const select = document.getElementById('historySelect');
                        select.innerHTML = '<option value="">-- Chọn mã ĐT để xem --</option>' + 
                            window.historicalData.map((item, idx) => `<option value="${item.category.dt_code}">[${item.category.dt_code}] ${item.category.name} - ${item.formatted_rows[0][2] || 'Không rõ'}</option>`).join('');
                        
                        currentReceiptIndex = 0;
                        renderCurrentReceipt();
                        select.value = window.historicalData[0].category.dt_code;
                        document.getElementById('historyInput').value = window.historicalData[0].category.dt_code;
                        showToast(`Đã tải ${window.historicalData.length} đơn hàng từ lịch sử.`, 'success');
                    } else {
                        showToast('Không tìm thấy dữ liệu lịch sử nào trên Sheet.', 'info');
                    }
                } catch (e) {
                    showToast('Lỗi nạp lịch sử: ' + e.message, 'error');
                }
            }
            
            function loadHistoricalRecord(dtCode) {
                if (!dtCode) return;
                const clean = dtCode.trim().toUpperCase();
                const idx = window.historicalData.findIndex(item => item.category.dt_code.toUpperCase() === clean);
                if (idx >= 0) {
                    currentReceiptIndex = idx;
                    renderCurrentReceipt();
                    document.getElementById('historySelect').value = window.historicalData[idx].category.dt_code;
                    document.getElementById('historyInput').value = window.historicalData[idx].category.dt_code;
                }
            }
            function onHistoryInput(val) {
                if (!val) return;
                const clean = val.trim().toUpperCase();
                // Auto-load when exact match found (user selected from datalist)
                const idx = window.historicalData.findIndex(item => item.category.dt_code.toUpperCase() === clean);
                if (idx >= 0) loadHistoricalRecord(clean);
            }
            function handleManualFile(event) { const file = event.target.files[0]; if (file) setManualFile(file); }
            function setManualFile(file) { manualSelectedFile = file; document.getElementById('manualLabel').textContent = file.name; document.getElementById('selectedFile').textContent = `📎 ${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`; }
            function renderManualResult(data) {
                const raw = data.raw_parsed_data || {};
                const row = Array.isArray(data.formatted_sheet_rows?.[0]) ? data.formatted_sheet_rows[0] : [];
                const category = data.category || {};
                document.getElementById('manualResultBox').style.display = 'block';
                document.getElementById('manualEngineUsed').textContent = `Engine: ${formatText(data.engine_used)}`;
                document.getElementById('manualCategoryBadge').innerHTML = `<span class="badge badge-dt${category.id || 4}">DT${category.id || 4} · ${escapeHtml(category.name || 'Khác')}</span>`;
                let alerts = '';
                if (data.warnings?.is_duplicate) alerts += `<div class="warning red" style="margin-bottom:8px;">⚠ ${escapeHtml(data.warnings.duplicate_message || 'Trùng lặp')}</div>`;
                if (data.warnings?.has_vat) alerts += `<div class="warning amber" style="margin-bottom:8px;">◈ ${escapeHtml(data.warnings.vat_alert || 'Có VAT')}</div>`;
                document.getElementById('manualAlertContainer').innerHTML = alerts;
                const values = [['Mã chứng từ', raw.order_id || raw.invoice_number || raw.tracking_number || row[4]], ['Tên công ty', raw.merchant_name || raw.company_name || row[2]], ['Ngày / Giờ', raw.date || raw.transaction_date || row[1]], ['Địa chỉ', raw.address || row[3]], ['Thành tiền', row[10], true], ['Người nhận', raw.buyer_name || raw.receiver || row[11]]];
                document.getElementById('manualResultContent').innerHTML = `<div class="result-summary"><div class="result-kpi"><span>Phân loại</span><strong>DT${category.id || 4}</strong></div><div class="result-kpi"><span>Trạng thái</span><strong>${data.warnings?.is_duplicate ? 'Cần kiểm tra' : 'Sẵn sàng review'}</strong></div></div><div class="result-fields">${values.map(([label, value, money]) => `<div class="result-row ${money ? 'total' : ''}"><span>${label}</span><strong>${escapeHtml(money ? `${formatMoney(value)} VNĐ` : formatText(value))}</strong></div>`).join('')}</div>`;
            }
            async function processManualReceipt() {
                if (!manualSelectedFile) { showToast('Vui lòng chọn một ảnh hóa đơn trước.', 'error'); return; }
                const button = document.getElementById('manualSubmitBtn');
                const formData = new FormData(); formData.append('file', manualSelectedFile); formData.append('mode', document.getElementById('manualEngineMode').value);
                try { const data = await apiFetch('/api/v1/receipt-ocr', { method: 'POST', body: formData }, button, '▶ Bắt Đầu Phân Tích'); renderManualResult(data); showToast('Đã phân tích hóa đơn thành công.', 'success'); }
                catch (error) { showToast(`OCR đơn lẻ thất bại: ${error.message}`, 'error'); }
            }
            function setStatusCard(index, text, state, detail) { const card = document.querySelectorAll('#statusCards .status-card')[index]; if (!card) return; card.querySelector('.status-state').className = `status-state ${state}`; card.querySelector('p').textContent = detail; card.querySelector('h3').dataset.value = text; }
            async function fetchStatus(showMessage = false) {
                try {
                    const data = await apiFetch('/api/v1/status');
                    const cloudOk = !!data.cloud_llm_ready, ollamaOk = !!data.ollama_model, sheetOk = !!(data.google_connected && data.sheet_configured);
                    setStatusCard(0, data.ai_engine_mode, 'ok', String(data.ai_engine_mode || 'auto').toUpperCase());
                    setStatusCard(1, cloudOk ? 'Connected' : 'Error', cloudOk ? 'ok' : 'error', cloudOk ? `Connected · ${data.cloud_model || 'Cloud model'}` : 'Chưa cấu hình API key');
                    setStatusCard(2, ollamaOk ? 'Ready' : 'Not Ready', ollamaOk ? 'ok' : 'error', ollamaOk ? `${data.ollama_model} · Local` : 'Chưa cấu hình Ollama');
                    setStatusCard(3, sheetOk ? 'Connected' : 'Disconnected', sheetOk ? 'ok' : 'error', sheetOk ? 'Google Sheet connected' : 'Chưa cấu hình Sheet');
                    document.getElementById('statusDetails').innerHTML = [['AI engine mode', data.ai_engine_mode], ['Cloud model', data.cloud_model], ['Ollama endpoint', data.ollama_base_url], ['Drive folder', data.drive_folder_configured ? 'Configured' : 'Not configured'], ['Google Sheet', data.sheet_configured ? 'Configured' : 'Not configured'], ['Tesseract OCR', data.tesseract_available ? 'Available' : 'Not available']].map(([label, value]) => `<div class="detail-line"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? '—')}</strong></div>`).join('');
                    document.getElementById('topSystemState').textContent = data.status === 'healthy' ? 'Healthy' : 'Check';
                    if (showMessage) showToast('Đã làm mới trạng thái hệ thống.', 'success');
                } catch (error) { document.getElementById('topSystemState').textContent = 'Offline'; if (showMessage) showToast(`Không thể lấy trạng thái: ${error.message}`, 'error'); }
            }
            const uploadZone = document.getElementById('uploadZone');
            ['dragenter', 'dragover'].forEach(name => uploadZone.addEventListener(name, event => { event.preventDefault(); uploadZone.classList.add('dragover'); }));
            ['dragleave', 'drop'].forEach(name => uploadZone.addEventListener(name, event => { event.preventDefault(); uploadZone.classList.remove('dragover'); }));
            uploadZone.addEventListener('drop', event => { const file = event.dataTransfer.files[0]; if (file) setManualFile(file); });
            uploadZone.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') document.getElementById('manualFileInput').click(); });
            document.getElementById('fullscreenBtn').addEventListener('click', openCurrentReceipt);
            updateStagedCounters();
            fetchStatus();
        </script>
    </body>
    </html>
    """
