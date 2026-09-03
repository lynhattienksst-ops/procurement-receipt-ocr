"""
Migration Script: Migrate historical data from Bang_Ke_Hoa_Don and Links_Hoa_Don
into the pure 2-sheet relational architecture (Data_Header_V2 and Data_Lines_V2).
Follows exact Vietnamese number parsing rules:
- '100.000,00 đ' = 100000 (Một trăm ngàn).
- Preserves exact raw numbers without rounding up.
"""
import os
import re
import sys
import logging
from typing import Dict, List, Any
from dotenv import load_dotenv

# Load environment
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration-v2")

from services.google_service import GoogleSyncService

def parse_vietnamese_number(val: Any) -> float:
    """
    Parse numbers formatted in Vietnamese style (e.g. '387.997,00 đ', '100.000,00', '1.250.000')
    or raw numbers, preserving exact numbers without rounding.
    100.000,00 = 100000 (một trăm ngàn).
    """
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    # Remove currency suffixes and spaces
    for suffix in ["đ", "Đ", "vnđ", "VNĐ", "vnd", "VND", "₫", "$"]:
        s = s.replace(suffix, "")
    s = s.strip()
    if not s:
        return 0.0

    # 1. Both dot and comma present: '100.000,00' or '100,000.00'
    if "." in s and "," in s:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_comma > last_dot:
            # VN / EU format: dots are thousands, comma is decimal: '100.000,00' -> '100000.00'
            s = s.replace(".", "").replace(",", ".")
        else:
            # US format: commas are thousands, dot is decimal: '100,000.00' -> '100000.00'
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        # VN decimal like '0,00' or '100,50' vs thousands like '100,000'
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in [1, 2]:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s and "," not in s:
        # Dots only
        parts = s.split(".")
        if len(parts) > 2:
            # '1.200.000' -> thousands
            s = s.replace(".", "")
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                # '387.997' -> 387997
                s = s.replace(".", "")
            else:
                # '10.5'
                pass

    try:
        val_f = float(s)
        return int(val_f) if val_f.is_integer() else val_f
    except ValueError:
        return 0.0

def format_vietnamese_currency(val: Any) -> str:
    """
    Format a numeric amount to Vietnamese currency format: '387.997,00 đ', '0,00 đ'.
    """
    if val is None or val == "":
        return "0,00 đ"
    try:
        f = float(val)
        formatted = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} đ"
    except (ValueError, TypeError):
        return str(val)

def run_migration():
    logger.info("==================================================")
    logger.info(" Bắt đầu Chuyển đổi Dữ liệu sang Kiến trúc V2 (Header & Line)")
    logger.info(" Định dạng tiền tệ chuẩn: '387.997,00 đ'")
    logger.info("==================================================")

    google_service = GoogleSyncService()
    if not google_service.is_connected():
        logger.error("[-] Không thể kết nối tới Google Sheets / Service Account.")
        sys.exit(1)

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    legacy_tab = os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")
    links_tab = os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don")
    header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
    lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

    # 1. Read legacy data
    logger.info(f"[*] Đang đọc dữ liệu từ tab cũ '{legacy_tab}' và '{links_tab}'...")
    main_rows = google_service.get_sheet_data(spreadsheet_id, legacy_tab)
    links_rows = google_service.get_sheet_data(spreadsheet_id, links_tab)

    if not main_rows or len(main_rows) <= 1:
        logger.warning("[-] Bảng kê cũ rỗng hoặc chỉ có tiêu đề. Không có bản ghi nào để chuyển đổi.")
        google_service.ensure_tab_exists(header_tab, spreadsheet_id)
        google_service.ensure_tab_exists(lines_tab, spreadsheet_id)
        return

    # 2. Build Links Map {dt_code: link_url}
    links_map: Dict[str, str] = {}
    for r in links_rows:
        if not r or len(r) < 3:
            continue
        dt = str(r[0]).strip().upper()
        link = str(r[2]).strip()
        if dt.startswith("DT") and link.startswith("http"):
            links_map[dt] = link

    logger.info(f"[*] Đã nạp {len(links_map)} link ảnh từ tab '{links_tab}'.")

    # 3. Group main rows by DT Code
    grouped_rows: Dict[str, List[List[Any]]] = {}
    for idx, r in enumerate(main_rows):
        if not r or len(r) == 0:
            continue
        # Skip header
        if idx == 0 and str(r[0]).strip().lower() in ["mã đối tượng", "dt_code", "dt code", "mã đt"]:
            continue
        dt_code = str(r[0]).strip().upper()
        if not dt_code or not dt_code.startswith("DT"):
            continue

        if dt_code not in grouped_rows:
            grouped_rows[dt_code] = []
        grouped_rows[dt_code].append(r)

    logger.info(f"[*] Tìm thấy {len(grouped_rows)} hóa đơn với tổng cộng {len(main_rows)-1} dòng mặt hàng.")

    # 4. Transform into Header and Line rows
    header_rows_to_insert: List[List[Any]] = []
    line_rows_to_insert: List[List[Any]] = []

    for dt_code, rows in grouped_rows.items():
        first_row = rows[0]
        # Pad row to 14 columns if needed
        while len(first_row) < 14:
            first_row.append("")

        dt = dt_code
        dt_date = str(first_row[1]).strip()
        merchant = str(first_row[2]).strip()
        merchant_addr = str(first_row[3]).strip()
        customer_addr = str(first_row[4]).strip()
        doc_code = str(first_row[5]).strip()
        customer_name = str(first_row[12]).strip()
        notes = str(first_row[13]).strip()
        drive_link = links_map.get(dt, "")

        sum_raw = 0.0
        sum_vat = 0.0
        sum_final = 0.0

        for r in rows:
            while len(r) < 14:
                r.append("")
            item_name = str(r[6]).strip()
            qty = parse_vietnamese_number(r[7]) if r[7] != "" else 1.0
            price = parse_vietnamese_number(r[8])
            vat_rate_str = str(r[9]).strip() if r[9] != "" else "0%"
            vat_num = int(round(float(vat_rate_str.replace("%", "").strip()))) if vat_rate_str and vat_rate_str != "0%" else 0
            vat_amt_num = parse_vietnamese_number(r[10])
            line_total_num = parse_vietnamese_number(r[11])
            price_num = parse_vietnamese_number(r[8])
            qty_num = parse_vietnamese_number(r[7]) if r[7] != "" else 1.0
            line_notes = str(r[13]).strip()

            raw_val = qty_num * price_num
            if raw_val == 0 and line_total_num > 0:
                raw_val = line_total_num - vat_amt_num
            if line_total_num == 0 and raw_val > 0:
                line_total_num = raw_val + vat_amt_num

            sum_raw += raw_val
            sum_vat += vat_amt_num
            sum_final += line_total_num

            def _clean(val):
                if val is None or val == "": return 0
                try:
                    vf = float(val)
                    return int(vf) if vf.is_integer() else round(vf, 2)
                except (ValueError, TypeError): return 0

            line_row = [
                dt,                                         # Cột A: Mã đối tượng
                "",                                         # Cột B: Mã sản phẩm (SKU)
                item_name,                                  # Cột C: Tên hàng hóa, dịch vụ
                _clean(qty_num),                            # Cột D: Số lượng
                _clean(price_num),                          # Cột E: Đơn giá
                0,                                          # Cột F: Chiết khấu mặt hàng
                0,                                          # Cột G: Tỷ lệ chiết khấu (%)
                vat_num,                                    # Cột H: Thuế suất VAT (%)
                _clean(vat_amt_num),                        # Cột I: Tiền thuế VAT
                _clean(line_total_num),                     # Cột J: Thành tiền
                line_notes                                  # Cột K: Ghi chú mặt hàng
            ]
            line_rows_to_insert.append(line_row)

        header_row = [
            dt,                                             # Cột A: Mã đối tượng
            dt_date,                                        # Cột B: Ngày, tháng, năm
            merchant,                                       # Cột C: Tên công ty
            merchant_addr,                                  # Cột D: Địa chỉ bên bán
            customer_addr,                                  # Cột E: Địa chỉ bên nhận
            doc_code,                                       # Cột F: Mã hóa đơn, chứng từ
            _clean(sum_raw),                                # Cột G: Tổng tiền hàng (gốc)
            0,                                              # Cột H: Chiết khấu thương mại
            _clean(sum_vat),                                # Cột I: Thuế VAT
            _clean(sum_final),                              # Cột J: Tổng Thanh Toán
            customer_name,                                  # Cột K: Người mua/nhận hàng
            drive_link,                                     # Cột L: Link ảnh đối soát
            notes                                           # Cột M: Ghi chú chung
        ]
        header_rows_to_insert.append(header_row)

    # 5. Overwrite Data_Header_V2 and Data_Lines_V2
    logger.info(f"[*] Đang ghi đè dữ liệu chính xác vào '{header_tab}' ({len(header_rows_to_insert)} dòng) và '{lines_tab}' ({len(line_rows_to_insert)} dòng)...")
    res = google_service.overwrite_relational_v2(header_rows_to_insert, line_rows_to_insert, spreadsheet_id)

    logger.info("[+] ==================================================")
    logger.info(f"[+] CHUYỂN ĐỔI CHÍNH XÁC THÀNH CÔNG! Đã ghi {res['headers_written']} Headers và {res['lines_written']} Lines vào Google Sheets.")
    logger.info("[+] ==================================================")


if __name__ == "__main__":
    run_migration()
