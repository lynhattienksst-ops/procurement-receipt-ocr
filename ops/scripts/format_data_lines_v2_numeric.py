import os
import sys
import logging
from dotenv import load_dotenv

# Set working directory to project root
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.insert(0, project_root)
os.chdir(project_root)

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("format-lines-numeric")

from services.google_service import GoogleSyncService
from services.business_rules import parse_vietnamese_number

def clean_to_number(val):
    if val is None or str(val).strip() == "":
        return 0
    try:
        s = str(val).replace("%", "").replace("đ", "").replace("VND", "").replace("vnd", "").strip()
        num = parse_vietnamese_number(s)
        return int(num) if num.is_integer() else round(num, 2)
    except Exception:
        try:
            f = float(str(val).replace(",", "").strip())
            return int(f) if f.is_integer() else round(f, 2)
        except Exception:
            return 0

def format_data_lines_v2_columns_f_to_k():
    service = GoogleSyncService()
    if not service.sheets_service:
        logger.error("[-] Không thể kết nối Google Sheets Service.")
        return {"success": False, "message": "Không kết nối được Google Sheets."}

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        logger.error("[-] Chưa cấu hình GOOGLE_SHEET_ID.")
        return {"success": False, "message": "Chưa cấu hình GOOGLE_SHEET_ID."}

    lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

    # 1. Lấy metadata của spreadsheet để lấy sheetId của tab Data_Lines_V2
    meta = service.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    lines_sheet_id = sheets_map.get(lines_tab)

    if lines_sheet_id is None:
        logger.error(f"[-] Không tìm thấy tab {lines_tab}.")
        return {"success": False, "message": f"Không tìm thấy tab {lines_tab}."}

    logger.info(f"=== 1. ĐỌC VÀ LÀM SẠCH DỮ LIỆU SỐ HỌC CỘT F ĐẾN K TRONG TAB {lines_tab} ===")
    all_lines = service.get_sheet_data(spreadsheet_id, lines_tab)
    if not all_lines or len(all_lines) == 0:
        logger.warning(f"[-] Tab {lines_tab} hiện chưa có dữ liệu.")
        return {"success": True, "message": f"Tab {lines_tab} trống."}

    header_row = all_lines[0]
    data_rows = all_lines[1:]

    cleaned_data_rows = []
    converted_count = 0

    for row in data_rows:
        r = list(row)
        while len(r) < 14:  # A→N: giữ chỗ Cột M "Nhóm hàng" + N "Nguồn phân loại"
            r.append("")
        
        # Cột D (Index 3): Số lượng
        r[3] = clean_to_number(r[3])
        # Cột F (Index 5): Đơn giá (đ)
        r[5] = clean_to_number(r[5])
        # Cột G (Index 6): Chiết khấu mặt hàng (đ)
        r[6] = clean_to_number(r[6])
        # Cột H (Index 7): Tỷ lệ chiết khấu (%)
        r[7] = clean_to_number(r[7])
        # Cột I (Index 8): Thuế suất VAT (%)
        r[8] = clean_to_number(r[8])
        # Cột J (Index 9): Tiền thuế VAT (đ)
        r[9] = clean_to_number(r[9])
        # Cột K (Index 10): Thành tiền (đ)
        r[10] = clean_to_number(r[10])

        cleaned_data_rows.append(r)
        converted_count += 1

    # Ghi lại toàn bộ dữ liệu sạch dạng pure numeric
    all_cleaned_rows = [header_row] + cleaned_data_rows
    logger.info(f"Đang cập nhật {converted_count} dòng dữ liệu số học thuần túy vào Google Sheet...")
    service.overwrite_sheet_data(all_cleaned_rows, spreadsheet_id=spreadsheet_id, sheet_name=lines_tab)

    logger.info(f"=== 2. THIẾT LẬP ĐỊNH DẠNG SỐ (NUMBER FORMAT) CHO CỘT F ĐẾN K TRÊN GOOGLE SHEETS ===")
    requests = []

    # Cột F, G (Index 5, 6): Đơn giá, Chiết khấu -> Number Format: #,##0
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": lines_sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 5,
                "endColumnIndex": 7
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {
                        "type": "NUMBER",
                        "pattern": "#,##0"
                    }
                }
            },
            "fields": "userEnteredFormat.numberFormat"
        }
    })

    # Cột H, I (Index 7, 8): Tỷ lệ CK (%), Thuế suất VAT (%) -> Number Format: 0.##
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": lines_sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 7,
                "endColumnIndex": 9
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {
                        "type": "NUMBER",
                        "pattern": "0.##"
                    }
                }
            },
            "fields": "userEnteredFormat.numberFormat"
        }
    })

    # Cột J, K (Index 9, 10): Tiền thuế VAT, Thành tiền -> Number Format: #,##0
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": lines_sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 9,
                "endColumnIndex": 11
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {
                        "type": "NUMBER",
                        "pattern": "#,##0"
                    }
                }
            },
            "fields": "userEnteredFormat.numberFormat"
        }
    })

    body = {"requests": requests}
    service.sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body=body
    ).execute()

    logger.info(f"[+] ĐÃ CHUYỂN ĐỔI TOÀN BỘ CỘT F ĐẾN K TRONG TAB {lines_tab} THÀNH ĐỊNH DẠNG SỐ THÀNH CÔNG!")
    return {
        "success": True,
        "message": f"Đã chuyển định dạng toàn bộ Cột F đến Cột K trong {lines_tab} thành kiểu số thành công ({converted_count} dòng)!"
    }

if __name__ == "__main__":
    format_data_lines_v2_columns_f_to_k()

