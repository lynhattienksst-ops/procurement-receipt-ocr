import os
import io
import re
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
from services.business_rules import detect_business_category, format_receipt_to_sheet_rows, format_receipt_to_relational_v2, check_vat_alert, classify_line_item
from services.spell_checker import validate_receipt_fields, check_vietnamese_spelling
from services.duplicate_checker import DuplicateChecker
from services.google_service import GoogleSyncService

# Try to import pytesseract
try:
    import pytesseract
except ImportError:
    pytesseract = None

def _fill_line_group_columns(line_rows, dt_code):
    """
    Chuẩn hóa danh sách dòng Lines lên 14 cột (A→N) và gán Cột M "Nhóm hàng" +
    Cột N "Nguồn phân loại" qua classify_line_item(tên=C, ĐVT=E).
    Dùng cho các đường ghi gọi thẳng append_relational_v2 (manual-entry, chuyển nhóm).
    DT1 = dòng gộp → chỉ giữ DV_SAN nếu là phí sàn, còn lại CAN_SOAT.
    Tôn trọng giá trị người sửa tay (Cột N == 'manual').
    """
    is_dt1 = str(dt_code or "").strip().upper().startswith("DT1")
    out = []
    for lr in line_rows:
        r = list(lr)
        while len(r) < 14:
            r.append("")
        if str(r[12] or "").strip() and str(r[13] or "").strip().lower() == "manual":
            out.append(r)
            continue
        g_code, g_src = classify_line_item(r[2], r[4], is_dt1=is_dt1)
        r[12] = g_code
        r[13] = g_src
        out.append(r)
    return out


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
# Dedicated Granular Lock for Sheet Writes (preserves monotonic continuity & prevents concurrent write collisions)
sheet_write_lock = asyncio.Lock()

# Parallel Processing Concurrency Limits (Configurable via environment variables)
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "5"))
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

MAX_CONCURRENT_OCR = int(os.getenv("MAX_CONCURRENT_OCR", "3"))
ocr_semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)

# Auto-scan configuration & real-time telemetry
AUTO_SCAN_ENABLED = False
SCAN_INTERVAL_SECONDS = 300
ACTIVE_AI_MODEL = os.getenv("OPENAI_MODEL", "gemini-2.5-flash-lite")
LAST_SCAN_TIME = "Chưa quét"
LAST_PROCESSED_COUNT = 0
LAST_SCAN_MESSAGE = ""
CURRENT_SCAN_PROGRESS = ""
SCAN_STOP_REQUESTED = False
# Output Directory for Internship Results & Exports
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/ket_qua" if os.path.exists("/app") else os.path.join(os.path.dirname(__file__), "..", "Thực tập tốt nghiệp", "Kết quả"))
try:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
except Exception:
    pass

def save_result_to_output_dir(data: Any, prefix: str = "ket_qua") -> str:
    """Save JSON snapshot of scan/export results to OUTPUT_DIR (Thực tập tốt nghiệp/Kết quả)."""
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(OUTPUT_DIR, f"{prefix}_{timestamp}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved result snapshot to: {filepath}")
        return filepath
    except Exception as err:
        logger.warning(f"Could not save snapshot to {OUTPUT_DIR}: {err}")
        return ""


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

                    link_url = f"https://drive.google.com/file/d/{file_id}/view"
                    header_row, line_rows, meta_v2 = format_receipt_to_relational_v2(
                        parsed_data, category_id=cat_id, current_index=curr_idx, drive_link=link_url
                    )

                    if header_row and line_rows:
                        await asyncio.to_thread(google_service.append_relational_v2, [header_row], line_rows)
                    else:
                        logger.error(
                            f"Auto-scan: format_receipt_to_relational_v2 returned no V2 rows for "
                            f"{file_name} (cat {cat_id}); skipping sheet write."
                        )
                        continue

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

def extract_pdf_base_key(file_name: str, file_id: str) -> Optional[str]:
    """
    Extract normalized primary key for PDF files & multi-page scan files to group all pages/continuations of the same invoice together.
    """
    fn_lower = str(file_name or "").lower().strip()
    is_pdf = fn_lower.endswith(".pdf") or ".pdf" in fn_lower
    has_page_suffix = bool(re.search(r"[\._\-]?p(?:age)?\d+", fn_lower))

    if is_pdf or has_page_suffix:
        clean_name = re.sub(r"[\._\-]?p(?:age)?\d+", "", fn_lower)
        clean_name = re.sub(r"\.(pdf|png|jpg|jpeg|webp)$", "", clean_name).strip()
        if clean_name:
            return f"pdf_name_{clean_name}"
        return f"pdf_id_{file_id}"
    return None


async def _download_drive_file_concurrent(file_info: Dict[str, Any]) -> Dict[str, Any]:
    """Download a single Drive file with concurrency throttling."""
    file_id = file_info["id"]
    file_name = file_info["name"]
    async with download_semaphore:
        try:
            img_bytes, _ = await asyncio.to_thread(google_service.download_file_bytes, file_id)
            return {"file_info": file_info, "bytes": img_bytes, "error": None}
        except Exception as dl_err:
            logger.error(f"Async download error for {file_name}: {dl_err}")
            return {"file_info": file_info, "bytes": None, "error": str(dl_err)}


async def _run_ocr_concurrent(download_res: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Run OCR for a downloaded receipt with concurrency throttling."""
    if download_res.get("error") or not download_res.get("bytes"):
        return {**download_res, "parsed_data": None}
    
    file_name = download_res["file_info"]["name"]
    img_bytes = download_res["bytes"]
    async with ocr_semaphore:
        try:
            parsed_data = await asyncio.to_thread(
                ocr_engine.process_image, img_bytes, mode=mode, custom_model=ACTIVE_AI_MODEL
            )
            return {**download_res, "parsed_data": parsed_data, "error": None}
        except Exception as ocr_err:
            logger.error(f"Async OCR error for {file_name}: {ocr_err}")
            return {**download_res, "parsed_data": None, "error": str(ocr_err)}


@app.post("/api/v1/drive/scan")
async def scan_drive_folder(
    folder_id: Optional[str] = Form(None),
    mode: str = Form("auto")
):
    """
    Parallelized batch scanner (v2.6.0):
    1. Downloads all files concurrently via download_semaphore.
    2. Runs OCR concurrently via ocr_semaphore (Gemini Cloud / Ollama).
    3. Sequentially applies Business Rules, PDF merging, and Duplicate Guards with Zero Impact on Sheet data.
    """
    global staged_receipts
    try:
        drive_files = await asyncio.to_thread(google_service.list_images_in_folder, folder_id)
        if not drive_files:
            return {"success": True, "message": "Không tìm thấy ảnh hoặc file PDF nào trong thư mục Google Drive.", "staged": []}

        # Step 1: Parallel Download from Google Drive
        logger.info(f"Starting parallel download for {len(drive_files)} files (concurrency={MAX_CONCURRENT_DOWNLOADS})...")
        download_tasks = [_download_drive_file_concurrent(f) for f in drive_files]
        download_results = await asyncio.gather(*download_tasks)

        # Step 2: Parallel OCR Execution via Gemini Cloud
        logger.info(f"Starting parallel OCR processing (concurrency={MAX_CONCURRENT_OCR})...")
        ocr_tasks = [_run_ocr_concurrent(item, mode=mode) for item in download_results]
        ocr_results = await asyncio.gather(*ocr_tasks)

        # Step 3: Initialize Duplicate Checker from live Sheet data (Read-Only)
        checker = DuplicateChecker()
        historical_codes = set()
        try:
            sheet_rows = await asyncio.to_thread(google_service.get_sheet_data)
            checker.load_from_sheet_rows(sheet_rows)
            historical_codes = set(checker.seen_codes)
            max_indexes = await asyncio.to_thread(google_service.get_max_indexes_by_category)
        except Exception:
            max_indexes = {1: 0, 2: 0, 3: 0, 4: 0}

        staged_results = []
        batch_invoice_map = {}

        # Step 4: Sequentially group, classify, format, and guard results
        for res in ocr_results:
            file_info = res["file_info"]
            file_id = file_info["id"]
            file_name = file_info["name"]

            if res.get("error") or not res.get("parsed_data"):
                staged_results.append({
                    "drive_file_id": file_id,
                    "filename": file_name,
                    "error": res.get("error", "Không thể trích xuất thông tin OCR.")
                })
                continue

            parsed_data = res["parsed_data"]
            cat_id, cat_name = detect_business_category(parsed_data)

            # Primary key for PDF file merging: PDF Filename / File ID
            pdf_base_key = extract_pdf_base_key(file_name, file_id)
            doc_codes = []
            if pdf_base_key:
                doc_codes.append(pdf_base_key)

            for c in [parsed_data.get("order_id"), parsed_data.get("tracking_number"), parsed_data.get("invoice_number"), parsed_data.get("receipt_number")]:
                if c and str(c).strip():
                    doc_codes.append(str(c).strip())

            # Check for HISTORICAL duplicates on Google Sheet
            is_hist_dup = False
            dup_msg = ""
            for c in doc_codes:
                if c and str(c).strip() and str(c).strip() in historical_codes:
                    if pdf_base_key and c == pdf_base_key:
                        continue
                    is_hist_dup = True
                    dup_msg = f"Trùng lặp lịch sử với mã chứng từ trên Sheet: {c}"
                    break

            # Check if this file belongs to an ALREADY PARSED invoice in the CURRENT batch
            batch_match_index = None
            if not is_hist_dup:
                for c in doc_codes:
                    if c and str(c).strip() and str(c).strip() in batch_invoice_map:
                        batch_match_index = batch_invoice_map[str(c).strip()]
                        break

            if batch_match_index is not None and batch_match_index < len(staged_results):
                # Smart Merge: Combine line_items into the existing staged invoice entry
                existing_entry = staged_results[batch_match_index]
                ex_parsed = existing_entry["parsed_data"]

                for field in ["merchant_name", "seller_tax_id", "merchant_address", "merchant_phone", "merchant_email", "customer_name", "customer_address", "order_id", "invoice_number", "tracking_number", "transaction_date"]:
                    if not ex_parsed.get(field) and parsed_data.get(field):
                        ex_parsed[field] = parsed_data[field]

                new_items = parsed_data.get("line_items") or []
                ex_items = ex_parsed.get("line_items") or []
                ex_items.extend(new_items)
                ex_parsed["line_items"] = ex_items

                if "page_files" not in existing_entry:
                    existing_entry["page_files"] = [existing_entry["filename"]]
                existing_entry["page_files"].append(file_name)
                existing_entry["filename"] = f"{existing_entry['page_files'][0]} (+{len(existing_entry['page_files'])-1} trang)"

                curr_idx = existing_entry["curr_idx"]
                rows, meta = format_receipt_to_sheet_rows(ex_parsed, category_id=cat_id, current_index=curr_idx)
                import urllib.parse
                safe_name = urllib.parse.quote(existing_entry["filename"])
                link_url = f"https://drive.google.com/file/d/{existing_entry['drive_file_id']}/view?name={safe_name}"
                header_row, line_rows, meta_v2 = format_receipt_to_relational_v2(
                    ex_parsed, category_id=cat_id, current_index=curr_idx, drive_link=link_url
                )

                existing_entry["formatted_rows"] = rows
                existing_entry["header_row"] = header_row
                existing_entry["line_rows"] = line_rows
                existing_entry["meta_v2"] = meta_v2
                logger.info(f"Smart Merged page file {file_name} into existing invoice {meta['dt_code']}")
                continue

            # Otherwise: New invoice entry in batch
            max_indexes[cat_id] += 1
            curr_idx = max_indexes[cat_id]

            for c in doc_codes:
                if c and str(c).strip():
                    checker.add_code(str(c))

            has_vat, vat_amt, vat_alert = check_vat_alert(parsed_data)
            spell_warnings = validate_receipt_fields(parsed_data)

            rows, meta = format_receipt_to_sheet_rows(parsed_data, category_id=cat_id, current_index=curr_idx)
            import urllib.parse
            safe_name = urllib.parse.quote(file_name)
            link_url = f"https://drive.google.com/file/d/{file_id}/view?name={safe_name}"
            header_row, line_rows, meta_v2 = format_receipt_to_relational_v2(
                parsed_data, category_id=cat_id, current_index=curr_idx, drive_link=link_url
            )

            item_entry = {
                "drive_file_id": file_id,
                "filename": file_name,
                "curr_idx": curr_idx,
                "parsed_data": parsed_data,
                "category": {"id": cat_id, "name": cat_name, "dt_code": meta["dt_code"]},
                "warnings": {
                    "is_duplicate": is_hist_dup,
                    "duplicate_message": dup_msg,
                    "has_vat": has_vat,
                    "vat_amount": vat_amt,
                    "vat_alert": vat_alert,
                    "spell_warnings": spell_warnings
                },
                "formatted_rows": rows,
                "header_row": header_row,
                "line_rows": line_rows,
                "meta_v2": meta_v2,
                "buyer_address": parsed_data.get("customer_address") or "",
                "approved": not is_hist_dup,
                "engine_used": parsed_data.get("_engine_used", mode)
            }

            new_idx = len(staged_results)
            staged_results.append(item_entry)

            for c in doc_codes:
                if c and str(c).strip():
                    batch_invoice_map[str(c).strip()] = new_idx

        staged_receipts = staged_results
        saved_file = save_result_to_output_dir(staged_results, prefix="ket_qua_quet_scan")
        return {
            "success": True,
            "total_processed": len(staged_results),
            "staged": staged_results,
            "saved_to": saved_file
        }
    except Exception as e:
        logger.error(f"Batch processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/sheets/export")
async def export_to_google_sheet(payload: Dict[str, Any] = Body(...)):
    """
    Append approved rows into Google Sheet (Data_Header_V2 and Data_Lines_V2), export links, and auto-move files.
    Guarded with scan_lock and pre-append duplicate filtering.
    """
    headers_to_export = payload.get("headers", [])
    lines_to_export = payload.get("lines", [])
    rows_legacy_to_export = payload.get("rows", [])

    # Fallback to approved staged receipts if payload arrays are empty
    if not headers_to_export and not lines_to_export and not rows_legacy_to_export:
        for item in staged_receipts:
            if item.get("approved"):
                if "header_row" in item and item["header_row"]:
                    headers_to_export.append(item["header_row"])
                if "line_rows" in item and item["line_rows"]:
                    lines_to_export.extend(item["line_rows"])
                if "formatted_rows" in item and item["formatted_rows"]:
                    rows_legacy_to_export.extend(item["formatted_rows"])

    if not headers_to_export and not lines_to_export and not rows_legacy_to_export:
        raise HTTPException(status_code=400, detail="Không có dòng dữ liệu nào được chọn để nhập vào Google Sheet.")

    async with sheet_write_lock:
        try:
            # 0. Live check against current sheet rows
            header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
            sheet_rows = await asyncio.to_thread(google_service.get_sheet_data, None, header_tab)
            checker = DuplicateChecker()
            checker.load_from_header_v2_rows(sheet_rows)

            filtered_headers = []
            filtered_dt_codes = set()

            for h in headers_to_export:
                if not h or len(h) == 0:
                    continue
                dt_code = str(h[0]).strip() if len(h) > 0 else ""
                doc_code = str(h[5]).strip() if len(h) > 5 else ""

                # Check duplicate
                if dt_code and dt_code.startswith("DT") and checker.check_duplicate(dt_code)[0]:
                    logger.warning(f"Export skipped header {dt_code}: DT Code already exists in Sheet.")
                    continue
                if doc_code and len(doc_code) >= 6 and checker.check_duplicate(doc_code)[0]:
                    logger.warning(f"Export skipped header {dt_code}: Order/Doc ID '{doc_code}' already exists in Sheet.")
                    continue

                filtered_headers.append(h)
                filtered_dt_codes.add(dt_code)
                if dt_code:
                    checker.add_code(dt_code)
                if doc_code:
                    checker.add_code(doc_code)

            # Filter matching lines
            filtered_lines = [l for l in lines_to_export if l and len(l) > 0 and str(l[0]).strip() in filtered_dt_codes]

            # 1. Export V2 Relational Data
            res_v2 = None
            if filtered_headers or filtered_lines:
                res_v2 = await asyncio.to_thread(google_service.append_relational_v2, filtered_headers, filtered_lines)

            # 3. Move processed files in Google Drive
            moved_files = 0
            processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")
            for item in staged_receipts:
                if "category" in item and "dt_code" in item["category"]:
                    dt_code = item["category"]["dt_code"]
                    if dt_code in filtered_dt_codes:
                        drive_id = item.get("drive_file_id")
                        if drive_id and processed_folder_id:
                            if await asyncio.to_thread(google_service.move_file_to_folder, drive_id, processed_folder_id):
                                moved_files += 1

            headers_count = len(filtered_headers)
            lines_count = len(filtered_lines)
            message = f"Đã nhập thành công {headers_count} Hóa đơn ({lines_count} dòng mặt hàng) vào Google Sheet V2!"
            if moved_files > 0:
                message += f" Đã di chuyển {moved_files} ảnh vào thư mục Đã Xử Lý."

            return {
                "success": True,
                "message": message,
                "headers_count": headers_count,
                "lines_count": lines_count,
                "details": res_v2
            }
        except Exception as e:
            logger.error(f"Sheet export error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Lỗi nhập vào Google Sheet: {str(e)}")


# NOTE: The one-time V1->V2 migration endpoint (`POST /api/v1/sheets/migrate-v2`)
# was removed after the flat V1 tables (Bang_Ke_Hoa_Don / Links_Hoa_Don) were
# retired. The migration itself is complete; `ops/migrations/migrate_to_v2.py`
# is kept only as a historical record.


@app.post("/api/v1/sheets/format-lines-numeric")
async def trigger_format_lines_numeric():
    """
    Format all existing and future columns F to K in Data_Lines_V2 to pure numeric types and number formatting.
    """
    async with sheet_write_lock:
        try:
            from ops.scripts.format_data_lines_v2_numeric import format_data_lines_v2_columns_f_to_k
            res = await asyncio.to_thread(format_data_lines_v2_columns_f_to_k)
            return res
        except Exception as e:
            logger.error(f"Format lines numeric error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Lỗi định dạng số Data_Lines_V2: {str(e)}")


@app.get("/api/v1/sheets/reconcile-totals")
@app.post("/api/v1/sheets/reconcile-totals")
async def reconcile_sheet_totals():
    """
    Audit and compare total payment amounts between Data_Header_V2 and Data_Lines_V2.
    """
    try:
        from services.business_rules import clean_num
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")
        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")

        header_rows = await asyncio.to_thread(google_service.get_sheet_data, target_sheet_id, header_tab) or []
        lines_rows = await asyncio.to_thread(google_service.get_sheet_data, target_sheet_id, lines_tab) or []

        headers_map = {}
        total_header_sum = 0.0
        for r in header_rows[1:]:
            if not r: continue
            dt_code = str(r[0]).strip().upper()
            if not dt_code.startswith("DT"): continue
            final_amt = clean_num(r[9]) if len(r) > 9 else 0.0
            company = str(r[2]).strip() if len(r) > 2 else ""
            headers_map[dt_code] = {"company": company, "final": final_amt}
            total_header_sum += final_amt

        lines_map = {}
        total_lines_sum = 0.0
        for r in lines_rows[1:]:
            if not r: continue
            dt_code = str(r[0]).strip().upper()
            if not dt_code.startswith("DT"): continue
            row_total = clean_num(r[10]) if len(r) > 10 else 0.0
            if dt_code not in lines_map:
                lines_map[dt_code] = {"count": 0, "sum": 0.0}
            lines_map[dt_code]["count"] += 1
            lines_map[dt_code]["sum"] += row_total
            total_lines_sum += row_total

        all_dts = sorted(list(set(list(headers_map.keys()) + list(lines_map.keys()))))
        exact_match = 0
        rounding_match = 0
        discrepancies = []

        for dt in all_dts:
            h_info = headers_map.get(dt)
            l_info = lines_map.get(dt)
            if not h_info or not l_info:
                discrepancies.append({
                    "dt_code": dt,
                    "company": (h_info or {}).get("company", "Chưa rõ"),
                    "header_total": (h_info or {}).get("final", 0.0),
                    "lines_total": (l_info or {}).get("sum", 0.0),
                    "diff": abs(((h_info or {}).get("final", 0.0)) - ((l_info or {}).get("sum", 0.0))),
                    "reason": "Thiếu dữ liệu ở 1 trong 2 bảng"
                })
                continue

            h_val = h_info["final"]
            h_disc = h_info.get("disc", 0.0)
            h_vat = h_info.get("vat", 0.0)
            l_val = l_info["sum"]
            expected_from_lines = l_val - h_disc + h_vat
            diff = round(abs(h_val - expected_from_lines), 2)
            if diff == 0:
                exact_match += 1
            elif diff <= 1000:
                rounding_match += 1
            else:
                discrepancies.append({
                    "dt_code": dt,
                    "company": h_info["company"],
                    "header_total": h_val,
                    "lines_total": l_val,
                    "header_discount": h_disc,
                    "diff": diff,
                    "reason": "Lệch số tiền"
                })

        return {
            "success": True,
            "total_header_sum": total_header_sum,
            "total_lines_sum": total_lines_sum,
            "overall_diff": round(total_header_sum - total_lines_sum, 2),
            "total_invoices": len(headers_map),
            "exact_match_count": exact_match,
            "rounding_match_count": rounding_match,
            "discrepancy_count": len(discrepancies),
            "discrepancies": discrepancies
        }
    except Exception as e:
        logger.error(f"Reconciliation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi đối chiếu số liệu 2 sheet: {str(e)}")




@app.api_route("/api/v1/drive/image/{file_id}", methods=["GET", "HEAD"])
async def get_drive_image_proxy(file_id: str):
    """
    Stream image or PDF bytes from Google Drive for iframe/img rendering with proper MIME header & client disk caching.
    """
    try:
        content, filename = await asyncio.to_thread(google_service.download_file_bytes, file_id, True)
        fn_lower = str(filename or "").lower()
        if fn_lower.endswith(".pdf") or content.startswith(b"%PDF"):
            media_type = "application/pdf"
        elif fn_lower.endswith(".png"):
            media_type = "image/png"
        elif fn_lower.endswith(".webp"):
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=604800, immutable",
                "X-Cache-Status": "HIT",
                "Content-Disposition": f"inline; filename=\"{filename}\""
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
    Update a specific record in Google Sheet by dt_code.
    Supports relational format { dt_code, header_row, line_rows } or legacy { dt_code, row }.
    Supports safe Category Migration (DTX -> DTY) with automatic sequential MAX+1 ID allocation.
    """
    from services.google_service import clean_dt_code
    from services.business_rules import parse_vietnamese_number, normalize_datetime_vn

    dt_code = payload.get("dt_code", "").strip()
    header_row = payload.get("header_row")
    line_rows = payload.get("line_rows")
    row = payload.get("row")

    if not dt_code:
        raise HTTPException(status_code=400, detail="Thiếu mã dt_code.")

    dt_clean = clean_dt_code(dt_code)

    try:
        # Determine original category
        orig_match = re.match(r"^(DT[1-4])", dt_clean.upper())
        orig_cat = orig_match.group(1) if orig_match else "DT2"

        # Determine requested category
        target_cat = ""
        category_hint = payload.get("category") or payload.get("form_type")
        if category_hint:
            cat_match = re.match(r"^(DT[1-4])", str(category_hint).strip().upper())
            if cat_match:
                target_cat = cat_match.group(1)

        if not target_cat:
            if header_row and len(header_row) > 0 and header_row[0]:
                h_match = re.match(r"^(DT[1-4])", str(header_row[0]).strip().upper())
                if h_match:
                    target_cat = h_match.group(1)
            elif row and len(row) > 0 and row[0]:
                r_match = re.match(r"^(DT[1-4])", str(row[0]).strip().upper())
                if r_match:
                    target_cat = r_match.group(1)

        if not target_cat:
            target_cat = orig_cat

        # -------------------------------------------------------------
        # CASE 1: CATEGORY MIGRATION (DTX -> DTY with X != Y)
        # -------------------------------------------------------------
        if orig_cat != target_cat and target_cat in ["DT1", "DT2", "DT3", "DT4"]:
            new_cat_num = int(target_cat.replace("DT", ""))
            max_indexes = await asyncio.to_thread(google_service.get_max_indexes_by_category)
            next_idx = max_indexes.get(new_cat_num, 0) + 1
            new_dt_code = f"DT{new_cat_num}{next_idx:04d}"

            logger.info(f"Triggering Category Migration: {dt_clean} ({orig_cat}) -> {new_dt_code} ({target_cat})")

            # Preserve Link Drive and Confirmed status from old record
            drive_link = payload.get("drive_link") or ""
            confirmed_status = ""
            try:
                all_headers = await asyncio.to_thread(google_service.get_sheet_data, None, "Data_Header_V2") or []
                for r in all_headers:
                    if r and len(r) > 0 and clean_dt_code(r[0]) == dt_clean:
                        if not drive_link:
                            if len(r) > 11 and ("drive.google.com" in str(r[11]) or str(r[11]).startswith("http")):
                                drive_link = str(r[11]).strip()
                            elif len(r) > 10 and ("drive.google.com" in str(r[10]) or str(r[10]).startswith("http")):
                                drive_link = str(r[10]).strip()
                            else:
                                for c in r:
                                    c_str = str(c).strip()
                                    if "drive.google.com" in c_str or c_str.startswith("http"):
                                        drive_link = c_str
                                        break
                        if len(r) > 13:
                            confirmed_status = str(r[13] or "").strip()
                        break
            except Exception as e:
                logger.warning(f"Could not retrieve existing Drive link for {dt_clean}: {e}")

            # Build final header_row and line_rows for new_dt_code
            if target_cat == "DT1":
                # Convert to DT1 format
                if row and len(row) >= 12:
                    upd = list(row)
                    while len(upd) < 14:
                        upd.append("")
                    date_val = normalize_datetime_vn(str(upd[1]).strip(), prefix_quote=True)
                    company_val = str(upd[2]).strip()
                    seller_addr = str(upd[3]).strip()
                    buyer_addr = str(upd[4]).strip()
                    order_id = str(upd[5]).strip()
                    if order_id.startswith('0') and len(order_id) > 1:
                        order_id = "'" + order_id
                    description = str(upd[6]).strip()
                    qty_num = parse_vietnamese_number(upd[7]) or 1
                    price_num = parse_vietnamese_number(upd[8])
                    vat_rate = str(upd[9]).strip() or "0%"
                    vat_num = parse_vietnamese_number(upd[10])
                    tot_num = parse_vietnamese_number(upd[11])
                    buyer_name = str(upd[12]).strip()
                    notes = str(upd[13]).strip()
                    raw_total = qty_num * price_num if qty_num > 0 and price_num > 0 else (tot_num - vat_num)

                    final_header = [
                        new_dt_code, date_val, company_val, seller_addr, buyer_addr,
                        order_id, raw_total, 0, vat_num, tot_num, buyer_name,
                        drive_link, notes, confirmed_status
                    ]
                    final_lines = [[
                        new_dt_code, "", description or "Đơn hàng TMĐT", qty_num, "Đơn",
                        price_num, 0, 0, vat_rate, vat_num, tot_num, notes
                    ]]
                elif header_row and len(header_row) >= 10:
                    h_copy = list(header_row)
                    while len(h_copy) < 14:
                        h_copy.append("")
                    h_copy[0] = new_dt_code
                    h_copy[11] = drive_link or h_copy[11]
                    h_copy[13] = confirmed_status or h_copy[13]
                    final_header = h_copy

                    first_line_name = ""
                    first_line_price = h_copy[9] or 0
                    if line_rows and len(line_rows) > 0 and len(line_rows[0]) > 2:
                        first_line_name = str(line_rows[0][2] or "").strip()
                        if len(line_rows[0]) > 5:
                            first_line_price = line_rows[0][5]

                    final_lines = [[
                        new_dt_code, "", first_line_name or "Đơn hàng TMĐT", 1, "Đơn",
                        first_line_price, 0, 0, "0%", h_copy[8] or 0, h_copy[9] or 0, h_copy[12] or ""
                    ]]
                else:
                    raise HTTPException(status_code=400, detail="Dữ liệu chuyển đổi DT1 không hợp lệ.")
            else:
                # Target is DT2, DT3, or DT4
                if header_row and line_rows is not None:
                    h_copy = list(header_row)
                    while len(h_copy) < 14:
                        h_copy.append("")
                    h_copy[0] = new_dt_code
                    h_copy[11] = drive_link or h_copy[11]
                    h_copy[13] = confirmed_status or h_copy[13]
                    final_header = h_copy

                    final_lines = []
                    for lr in line_rows:
                        lr_copy = list(lr)
                        while len(lr_copy) < 14:
                            lr_copy.append("")
                        lr_copy[0] = new_dt_code
                        # If target is DT3, filter out '(loại bỏ)'
                        if target_cat == "DT3":
                            item_name = str(lr_copy[2] or "").lower()
                            if "(loại bỏ)" in item_name or "(loai bo)" in item_name:
                                continue
                        final_lines.append(lr_copy)
                    if not final_lines:
                        final_lines = [[new_dt_code, "", "Mặt hàng chi tiết", 1, "Cái", 0, 0, 0, "0%", 0, 0, ""]]
                elif row and len(row) >= 12:
                    # Convert flat row to Relational Header + Line
                    upd = list(row)
                    while len(upd) < 14:
                        upd.append("")
                    date_val = normalize_datetime_vn(str(upd[1]).strip(), prefix_quote=True)
                    company_val = str(upd[2]).strip()
                    seller_addr = str(upd[3]).strip()
                    buyer_addr = str(upd[4]).strip()
                    order_id = str(upd[5]).strip()
                    if order_id.startswith('0') and len(order_id) > 1:
                        order_id = "'" + order_id
                    description = str(upd[6]).strip()
                    qty_num = parse_vietnamese_number(upd[7]) or 1
                    price_num = parse_vietnamese_number(upd[8])
                    vat_rate = str(upd[9]).strip() or "0%"
                    vat_num = parse_vietnamese_number(upd[10])
                    tot_num = parse_vietnamese_number(upd[11])
                    buyer_name = str(upd[12]).strip()
                    notes = str(upd[13]).strip()
                    raw_total = qty_num * price_num if qty_num > 0 and price_num > 0 else (tot_num - vat_num)

                    final_header = [
                        new_dt_code, date_val, company_val, seller_addr, buyer_addr,
                        order_id, raw_total, 0, vat_num, tot_num, buyer_name,
                        drive_link, notes, confirmed_status
                    ]
                    final_lines = [[
                        new_dt_code, "", description or "Mặt hàng", qty_num, "Cái",
                        price_num, 0, 0, vat_rate, vat_num, tot_num, notes
                    ]]
                else:
                    raise HTTPException(status_code=400, detail="Dữ liệu chuyển đổi không hợp lệ.")

            # Chuẩn hóa final_lines lên 14 cột (A→N): gán Nhóm hàng (M) + nguồn (N).
            final_lines = _fill_line_group_columns(final_lines, new_dt_code)

            # 1. Delete old record (WITHOUT deleting Google Drive file)
            await asyncio.to_thread(google_service.delete_sheet_record, dt_clean, delete_drive_file=False)

            # 2. Append new record safely to Google Sheet
            await asyncio.to_thread(google_service.append_relational_v2, [final_header], final_lines)

            logger.info(f"Successfully migrated {dt_clean} -> {new_dt_code} ({len(final_lines)} lines).")
            return {
                "success": True,
                "message": f"Đã chuyển đổi thành công từ {dt_clean} sang {new_dt_code}.",
                "new_dt_code": new_dt_code,
                "dt_code": new_dt_code,
                "lines_count": len(final_lines),
                "header_row": final_header,
                "line_rows": final_lines
            }

        # -------------------------------------------------------------
        # CASE 2: NORMAL RECORD UPDATE WITHIN SAME CATEGORY
        # -------------------------------------------------------------
        result = await asyncio.to_thread(google_service.dispatch_pipeline, dt_clean, payload)
        if result.get("success"):
            lines_cnt = result.get("lines_count", 1)
            return {
                "success": True,
                "message": f"Đã cập nhật thành công hóa đơn {dt_clean} ({lines_cnt} dòng mặt hàng) qua Nhà Máy Xử Lý V2.",
                "dt_code": dt_clean,
                "lines_count": lines_cnt,
                "header_row": result.get("header_row"),
                "line_rows": result.get("line_rows")
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Cập nhật thất bại."))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating sheet record {dt_code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật Sheet: {str(e)}")

@app.post("/api/v1/sheets/confirm")
async def confirm_sheet_record(payload: dict = Body(...)):
    """
    Confirm or reject a receipt in Data_Header_V2 (marks 'x' in Column N if confirmed, clears if rejected).
    """
    dt_code = payload.get("dt_code", "").strip()
    status = payload.get("status", "x").strip()
    if not dt_code:
        raise HTTPException(status_code=400, detail="Thiếu dt_code")
    try:
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
    Permanently delete a record from Data_Header_V2 and Data_Lines_V2
    (and its Drive image file).
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

@app.get("/api/v1/drive/unprocessed-files")
async def get_unprocessed_files():
    """Get list of files from unprocessed folder"""
    try:
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        if not folder_id:
            return {"files": []}
            
        files = await asyncio.to_thread(
            google_service.drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                fields="files(id, name, mimeType)",
                pageSize=50
            ).execute
        )
        return {"files": files.get("files", [])}
    except Exception as e:
        logger.error(f"Error fetching unprocessed files: {e}")
        return {"files": []}

@app.post("/api/v1/manual-entry")
async def manual_entry(data: dict = Body(...)):
    """
    Handle manual receipt entry.
    Moves image to processed folder, appends to Data_Header_V2 and Data_Lines_V2.
    """
    try:
        file_id = data.get("file_id")
        filename = data.get("filename")
        if not file_id:
            raise HTTPException(status_code=400, detail="Thiếu file_id")

        category_str = data.get("category", "DT4")
        cat_id_match = re.search(r'\d+', category_str)
        cat_id = int(cat_id_match.group()) if cat_id_match else 4
        
        # Determine dt_code
        max_indexes = await asyncio.to_thread(google_service.get_max_indexes_by_category)
        new_index = max_indexes.get(cat_id, 0) + 1
        dt_code = f"DT{cat_id}{new_index:04d}"
        
        # Move file to processed folder
        processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID") or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        success = await asyncio.to_thread(
            google_service.move_file_to_folder,
            file_id,
            processed_folder_id
        )
        
        if not success:
            logger.warning(f"Could not move file {file_id}, but will proceed with manual entry")
            
        link_url = f"https://drive.google.com/file/d/{file_id}/view"
        
        header_row = data.get("header_row")
        line_rows = data.get("line_rows")

        if header_row and line_rows is not None:
            # Replace placeholder DT code in header & lines
            header_row[0] = dt_code
            header_row[11] = link_url
            for l in line_rows:
                l[0] = dt_code

            line_rows = _fill_line_group_columns(line_rows, dt_code)
            await asyncio.to_thread(google_service.append_relational_v2, [header_row], line_rows)
        else:
            # DT1 single-line manual entry -> V2 relational schema
            # (Data_Header_V2 = 14 cols A→N; Data_Lines_V2 = 14 cols A→N, cols M/N
            #  "Nhóm hàng" filled by _fill_line_group_columns before the append).
            from services.business_rules import clean_num

            raw_amt = clean_num(data.get("unit_price", 0))
            disc_amt = clean_num(data.get("discount_amount", 0))
            vat_amt = clean_num(data.get("vat_amount", 0))
            total_amt = clean_num(data.get("total_amount", 0))
            vat_rate_str = str(data.get("vat_rate", "0")).replace("%", "").strip() or "0"
            notes = data.get("notes", "")

            # A  B     C        D              E              F         G       H        I       J        K          L        M      N
            h_row = [
                dt_code,
                data.get("date", ""),
                data.get("company", ""),
                data.get("seller_address", ""),
                data.get("buyer_address", ""),
                data.get("order_id", ""),
                raw_amt,
                disc_amt,
                vat_amt,
                total_amt,
                data.get("buyer_name", ""),
                link_url,
                notes,
                "",
            ]
            # A  B   C              D          E          F        G          H          I       J          K
            l_row = [
                dt_code,
                "",
                data.get("description", ""),
                clean_num(data.get("quantity", 1)),
                "",
                raw_amt,
                disc_amt,
                0,
                clean_num(vat_rate_str),
                vat_amt,
                total_amt,
                notes,
            ]
            l_rows = _fill_line_group_columns([l_row], dt_code)
            await asyncio.to_thread(google_service.append_relational_v2, [h_row], l_rows)

        return {"success": True, "dt_code": dt_code, "message": f"Lưu thành công hóa đơn [{dt_code}] vào Sheet V2!"}
    except Exception as e:
        logger.error(f"Error in manual entry: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# RECONCILIATION API ENDPOINTS (v2.8.0)
# =============================================================================
@app.post("/api/v1/reconcile/run")
async def trigger_reconciliation(period: str = Body(..., embed=True)):
    """
    Trigger automated reconciliation for a specific period YYYYMM.
    """
    try:
        from services.reconciliation_engine import run_reconciliation
        summary = await run_reconciliation(period, google_service)
        return {"success": True, "message": f"Đối soát kỳ {period} hoàn tất.", "summary": summary}
    except Exception as e:
        logger.error(f"Reconciliation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/reconcile/status/{period}")
async def get_reconciliation_status(period: str):
    """
    Check if a report tab exists for the given period.
    """
    try:
        report_tab = f"Reconciliation_Report_{period}"
        spreadsheet_id = os.getenv("RECON_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
        # Read from Google Sheets metadata to verify
        metadata = await asyncio.to_thread(google_service.sheets_service.spreadsheets().get, spreadsheetId=spreadsheet_id)
        sheets = metadata.execute().get("sheets", [])
        exists = any(s.get("properties", {}).get("title") == report_tab for s in sheets)
        return {"success": True, "exists": exists}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/v1/reconcile/report/{period}")
async def get_reconciliation_report(period: str):
    """
    Fetch the reconciliation report rows from Google Sheet for the given period YYYYMM.
    """
    try:
        report_tab = f"Reconciliation_Report_{period}"
        spreadsheet_id = os.getenv("RECON_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
        rows = await asyncio.to_thread(google_service.get_sheet_data, spreadsheet_id, report_tab)
        if not rows:
            return {"success": True, "records": []}
        
        headers = rows[0]
        records = []
        for r in rows[1:]:
            record = {}
            for idx, h in enumerate(headers):
                record[h] = r[idx] if idx < len(r) else ""
            records.append(record)
            
        return {"success": True, "records": records}
    except Exception as e:
        logger.error(f"Error fetching reconciliation report for {period}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
                    <div class="connection-pill"><span class="pulse"></span><span>Localhost API · Port 8080</span></div>
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
                const fn = String(item.filename || item.name || '').toLowerCase();
                const isPdf = fn.endsWith('.pdf') || (item.mimeType && item.mimeType.includes('pdf'));
                const fileUrl = item.drive_file_id ? `/api/v1/drive/image/${encodeURIComponent(item.drive_file_id)}#toolbar=1&navpanes=1` : '';
                const driveUrl = item.drive_file_id ? `https://drive.google.com/file/d/${encodeURIComponent(item.drive_file_id)}/preview` : '';
                const fullscreen = document.getElementById('fullscreenBtn');
                fullscreen.disabled = !driveUrl;
                fullscreen.dataset.url = driveUrl;
                if (fileUrl) {
                    if (isPdf) {
                        preview.innerHTML = `<iframe src="${fileUrl}" style="width:100%; height:100%; min-height:550px; border:none;" title="Hóa đơn PDF ${escapeHtml(item.filename || '')}" allow="autoplay"></iframe>`;
                    } else {
                        preview.innerHTML = `<img src="${fileUrl}" alt="Ảnh hóa đơn ${escapeHtml(item.filename || '')}" style="max-width:100%; max-height:100%; object-fit:contain;">`;
                    }
                } else {
                    preview.innerHTML = `<div class="preview-empty"><div class="empty-copy"><div class="empty-icon">!</div><h3>Không có link ảnh</h3><p>Hóa đơn này không chứa drive_file_id để xem ảnh gốc.</p></div></div>`;
                }
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
                    val = val.replace(/\\s*VNĐ$/, '').replace(/\\./g, '').replace(/,/g, '.');
                    return isNaN(Number(val)) || val.trim() === '' ? el.value.replace(/\\s*VNĐ$/, '').trim() : val.trim();
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
                                let match = r.file_info.link.match(/\\/d\\/(.+?)\\//);
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

