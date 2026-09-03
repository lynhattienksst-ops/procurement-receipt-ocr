import os
import re
import uuid
import logging
import asyncio
import json
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

# Import internal modules
from services.google_service import GoogleSyncService
from services.business_rules import parse_vietnamese_number, format_vietnamese_currency

logger = logging.getLogger("reconciliation-engine")

# Try to import sheet_write_lock from server.py, fallback to a local lock if running as a standalone script
try:
    from server import sheet_write_lock
except ImportError:
    sheet_write_lock = asyncio.Lock()

class BreakFlag(str, Enum):
    MATCHED = "MATCHED"                           # Khớp hoàn toàn
    ROUNDING_BREAK = "ROUNDING_BREAK"             # Lệch trong dung sai làm tròn (cảnh báo vàng)
    MISSING_LINES_BREAK = "MISSING_LINES_BREAK"   # Header có nhưng Lines khuyết (đỏ)
    MISSING_HEADER_BREAK = "MISSING_HEADER_BREAK" # Lines có nhưng Header khuyết (đỏ)
    REAL_DISCREPANCY = "REAL_DISCREPANCY"         # Lệch thực sự ngoài dung sai (đỏ đậm)
    OVERRIDE_BREAK = "OVERRIDE_BREAK"             # Có can thiệp thủ công nhưng lệch (xanh dương)
    CROSS_PERIOD_DUPLICATE = "CROSS_PERIOD_DUPLICATE" # Trùng hóa đơn ở các kỳ khác (đỏ)
    PENDING_REVIEW = "PENDING_REVIEW"             # Lệch lặp lại/thuế/chứng từ cần xem xét (cam)

@dataclass
class NormalizedEntry:
    dt_code: str
    dt_group: int          # 1, 2, 3, 4
    company: str
    period: str            # YYYYMM
    header_raw: float
    header_total: float
    header_discount: float
    header_vat: float
    lines_raw: float
    lines_vat: float
    lines_total: float
    lines_count: int
    invoice_number: str
    has_header: bool
    has_lines: bool
    lines_auto_corrected: int = 0   # số dòng được tính lại tự động bởi Line Auto-Compute
    auto_correct_note: str = ""     # mô tả ngắn về lý do điều chỉnh

@dataclass
class MatchResult:
    dt_code: str
    company: str
    period: str
    header_total: float
    expected_total: float
    discrepancy: float
    flag: BreakFlag
    message: str

def clean_num(val: Any) -> float:
    if val is None or str(val).strip() == "":
        return 0.0
    try:
        s = str(val).replace("%", "").replace("đ", "").replace("VND", "").replace("vnd", "").strip()
        num = parse_vietnamese_number(s)
        return float(num)
    except Exception:
        try:
            return float(str(val).replace(",", "").strip())
        except Exception:
            return 0.0

def parse_period(date_str: str) -> Optional[str]:
    if not date_str:
        return None
    s = str(date_str).strip().lstrip("'")
    # Matches "HH:MM:SS DD-MM-YYYY" or "HH:MM DD-MM-YYYY" or "DD-MM-YYYY"
    m_vn = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", s)
    if m_vn:
        return m_vn.group(3)
    # Matches YYYY-MM-DD or YYYY/MM/DD
    m1 = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m1:
        return m1.group(1)
    # Matches DD-MM-YYYY or DD/MM/YYYY
    m2 = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})", s)
    if m2:
        return m2.group(3)
    # Matches YYYYMMDD or YYYYMM
    m3 = re.match(r"^(\d{4})(\d{2})", s)
    if m3:
        return m3.group(1)
    # Fallback to search for a 4-digit year
    m4 = re.search(r"\b(20\d{2}|19\d{2})\b", s)
    if m4:
        return m4.group(1)
    return None


def compute_line_figures(
    qty: float,
    price: float,
    disc_raw: float,
    disc_rate: float,
    vat_rate: float,
    vat_amt: float,
    row_total: float,
    dt_code: str = ""
) -> Tuple[float, float, float, bool, str]:
    """
    Line Auto-Compute: Tính lại các số liệu của một dòng mặt hàng.

    Quy ước thống nhất (v2.14+):
    - disc_raw = Tổng CK Mặt Hàng (toàn bộ dòng, không phải CK đơn vị).
      Người dùng có trách nhiệm nhập đúng tổng chiết khấu của cả dòng.
    - Không còn heuristic tự động nhân (disc_raw × qty) để phân loại CK đơn vị,
      tránh tính sai khi dữ liệu đã được nhập đúng là tổng dòng.

    Returns:
        (computed_gross, computed_disc_total, computed_total, was_corrected, note)
        - computed_gross:      qty × price
        - computed_disc_total: chiết khấu tổng dòng (sau quy ước)
        - computed_total:      Thành tiền = gross - disc_total + vat
        - was_corrected:       True nếu kết quả khác row_total gốc > 1.000 VNĐ
        - note:                mô tả lý do điều chỉnh (nếu có)
    """
    gross = qty * price
    note = ""

    # --- Bước 1: Xác định Tổng CK Mặt Hàng ---
    if disc_rate > 0:
        # Ưu tiên: nếu có disc_rate (%), tính từ gross
        disc_total = gross * (disc_rate / 100.0)
        disc_source = "rate"
    elif disc_raw > 0:
        # disc_raw là Tổng CK Mặt Hàng — dùng trực tiếp (không nhân SL)
        disc_total = disc_raw
        disc_source = "order"
    else:
        disc_total = 0.0
        disc_source = "none"

    # --- Bước 2: Tính VAT ---
    net_amount = max(0.0, gross - disc_total)
    if vat_rate > 0:
        vat_computed = net_amount * (vat_rate / 100.0)
    else:
        vat_computed = vat_amt  # dùng giá trị từ sheet nếu không có rate

    # --- Bước 3: Tính Thành tiền ---
    computed_total = net_amount + vat_computed

    # --- Bước 4: So sánh với row_total gốc ---
    # Dữ liệu gốc trên Sheet có thể đã làm tròn — ưu tiên row_total từ Sheet.
    was_corrected = False
    diff = abs(computed_total - row_total)
    if row_total > 0 and diff > 1000.0:
        was_corrected = True
        note = f"Lệch tính toán: computed={computed_total:,.0f}, gốc={row_total:,.0f}, lệch={diff:,.0f}"
        logger.warning(
            f"[Line Auto-Compute] {dt_code}: gross={gross:.0f}, disc_src={disc_source}, "
            f"disc_total={disc_total:.0f}, computed={computed_total:.0f}, "
            f"original={row_total:.0f}, diff={diff:.0f}"
        )
    else:
        # Dữ liệu gốc hợp lệ — ưu tiên row_total và vat_amt
        computed_total = row_total if row_total > 0 else computed_total
        vat_computed = vat_amt if vat_amt > 0 else vat_computed

    return gross, disc_total, computed_total, was_corrected, note


def normalize_all_entries(header_rows: List[List[Any]], lines_rows: List[List[Any]]) -> Dict[str, NormalizedEntry]:
    """
    Groups and structures raw data from Data_Header_V2 and Data_Lines_V2 by DT code.
    """
    entries: Dict[str, NormalizedEntry] = {}

    # 1. Process Data_Header_V2
    # Headers: dt_code(0), Date(1), Company(2), ..., Raw(6), Disc(7), VAT(8), Final(9)
    for r in header_rows[1:]:
        if not r or len(r) == 0:
            continue
        dt_code = str(r[0]).strip().upper()
        if not dt_code or not dt_code.startswith("DT"):
            continue

        dt_group = 4
        for cat in [1, 2, 3, 4]:
            if dt_code.startswith(f"DT{cat}"):
                dt_group = cat
                break

        date_str = str(r[1]).strip() if len(r) > 1 else ""
        period = parse_period(date_str) or "UNKNOWN"
        company = str(r[2]).strip() if len(r) > 2 else "Unknown Merchant"
        invoice_number = str(r[5]).strip() if len(r) > 5 else ""
        raw_amt = clean_num(r[6]) if len(r) > 6 else 0.0
        disc_amt = clean_num(r[7]) if len(r) > 7 else 0.0
        vat_amt = clean_num(r[8]) if len(r) > 8 else 0.0
        final_amt = clean_num(r[9]) if len(r) > 9 else 0.0

        entries[dt_code] = NormalizedEntry(
            dt_code=dt_code,
            dt_group=dt_group,
            company=company,
            period=period,
            header_raw=raw_amt,
            header_total=final_amt,
            header_discount=disc_amt,
            header_vat=vat_amt,
            lines_raw=0.0,
            lines_vat=0.0,
            lines_total=0.0,
            lines_count=0,
            invoice_number=invoice_number,
            has_header=True,
            has_lines=False
        )

    # 2. Process Data_Lines_V2
    # Headers: dt_code(0), SKU(1), Name(2), Qty(3), UOM(4), Price(5), Disc(6), DiscRate(7), VATRate(8), VAT(9), Total(10)
    for r in lines_rows[1:]:
        if not r or len(r) == 0:
            continue
        dt_code = str(r[0]).strip().upper()
        if not dt_code or not dt_code.startswith("DT"):
            continue

        qty       = clean_num(r[3])  if len(r) > 3  else 1.0
        price     = clean_num(r[5])  if len(r) > 5  else 0.0
        disc_raw  = clean_num(r[6])  if len(r) > 6  else 0.0
        disc_rate = clean_num(r[7])  if len(r) > 7  else 0.0
        vat_rate  = clean_num(r[8])  if len(r) > 8  else 0.0
        line_vat  = clean_num(r[9])  if len(r) > 9  else 0.0
        row_total = clean_num(r[10]) if len(r) > 10 else 0.0

        if dt_code not in entries:
            # Missing Header case
            dt_group = 4
            for cat in [1, 2, 3, 4]:
                if dt_code.startswith(f"DT{cat}"):
                    dt_group = cat
                    break
            entries[dt_code] = NormalizedEntry(
                dt_code=dt_code,
                dt_group=dt_group,
                company="Missing Header",
                period="UNKNOWN",
                header_raw=0.0,
                header_total=0.0,
                header_discount=0.0,
                header_vat=0.0,
                lines_raw=0.0,
                lines_vat=0.0,
                lines_total=0.0,
                lines_count=0,
                invoice_number="",
                has_header=False,
                has_lines=True
            )

        # --- Line Auto-Compute ---
        line_gross, _, computed_total, was_corrected, note = compute_line_figures(
            qty=qty, price=price, disc_raw=disc_raw, disc_rate=disc_rate,
            vat_rate=vat_rate, vat_amt=line_vat, row_total=row_total, dt_code=dt_code
        )
        # Dùng computed_total (đã được kiểm tra nhất quán) thay cho row_total thô
        entries[dt_code].lines_raw   += line_gross
        entries[dt_code].lines_vat   += line_vat
        entries[dt_code].lines_total += computed_total
        entries[dt_code].lines_count += 1
        entries[dt_code].has_lines    = True
        if was_corrected:
            entries[dt_code].lines_auto_corrected += 1
            entries[dt_code].auto_correct_note = note

    return entries

def match_entry(
    entry: NormalizedEntry, 
    tolerance_vnd: float, 
    tolerance_pct: float,
    repeated_companies: Optional[Set[str]] = None,
    all_invoices: Optional[List[NormalizedEntry]] = None
) -> MatchResult:
    """
    So khớp chi tiết hóa đơn, áp dụng dung sai kép và cờ PENDING_REVIEW theo nghiệp vụ.
    """
    if repeated_companies is None:
        repeated_companies = set()

    # 1. Missing Header
    if not entry.has_header:
        return MatchResult(
            dt_code=entry.dt_code,
            company=entry.company,
            period=entry.period,
            header_total=0.0,
            expected_total=entry.lines_total,
            discrepancy=abs(entry.lines_total),
            flag=BreakFlag.MISSING_HEADER_BREAK,
            message="Thiếu thông tin chung ở bảng Data_Header_V2."
        )

    # 2. Missing Lines (for DT2, DT3, DT4)
    if not entry.has_lines and entry.dt_group != 1:
        return MatchResult(
            dt_code=entry.dt_code,
            company=entry.company,
            period=entry.period,
            header_total=entry.header_total,
            expected_total=0.0,
            discrepancy=abs(entry.header_total),
            flag=BreakFlag.MISSING_LINES_BREAK,
            message=f"Hóa đơn nhóm DT{entry.dt_group} thiếu dòng chi tiết ở bảng Data_Lines_V2."
        )

    # 3. Cross-period duplicate check
    if all_invoices and entry.invoice_number:
        duplicates = [
            x for x in all_invoices 
            if x.dt_code != entry.dt_code 
            and x.invoice_number == entry.invoice_number 
            and x.company.lower() == entry.company.lower()
            and x.period != entry.period
        ]
        if duplicates:
            return MatchResult(
                dt_code=entry.dt_code,
                company=entry.company,
                period=entry.period,
                header_total=entry.header_total,
                expected_total=entry.header_total,
                discrepancy=0.0,
                flag=BreakFlag.CROSS_PERIOD_DUPLICATE,
                message=f"Trùng số hóa đơn '{entry.invoice_number}' xuất hiện ở kỳ khác: {', '.join([x.period for x in duplicates])}."
            )

    # 4. expected total calculation
    if entry.dt_group == 1 and not entry.has_lines:
        # Category 1 without Lines: check internal math of Header
        # Expected Total Payment = Header Raw - Header Discount + Header VAT
        expected = entry.header_raw - entry.header_discount + entry.header_vat
    else:
        # Standard: Expected Total = sum(Lines.Thành_tiền) - Header.Chiết_Khấu
        standard_expected = entry.lines_total - entry.header_discount
        
        # Calculate discrepancies under both standard and net assumptions
        diff_standard = abs(entry.header_total - standard_expected)
        diff_no_discount = abs(entry.header_total - entry.lines_total)
        
        # If the discount is positive and the no-discount formula is a better match
        # (and within the rounding tolerance thresholds), use lines_total directly
        if entry.header_discount > 0 and diff_no_discount < diff_standard and diff_no_discount <= tolerance_vnd:
            pct_ok = True
            if entry.header_total > 0:
                pct_ok = (diff_no_discount / entry.header_total) <= tolerance_pct
            
            if pct_ok:
                expected = entry.lines_total
            else:
                expected = standard_expected
        else:
            expected = standard_expected

    diff = round(abs(entry.header_total - expected), 2)

    # Exact Match
    if diff == 0:
        return MatchResult(
            dt_code=entry.dt_code,
            company=entry.company,
            period=entry.period,
            header_total=entry.header_total,
            expected_total=expected,
            discrepancy=0.0,
            flag=BreakFlag.MATCHED,
            message="Khớp tuyệt đối 100%."
        )

    # 5. Check repeated rounding breaks or critical factors -> PENDING_REVIEW
    is_rounding = diff <= tolerance_vnd
    if entry.header_total > 0:
        is_rounding = is_rounding and (diff / entry.header_total) <= tolerance_pct

    # Mandatory flag to PENDING_REVIEW if critical errors exist:
    reasons = []
    
    # Check repeated pattern
    if entry.company in repeated_companies:
        reasons.append("Sai lệch lặp lại từ cùng nhà cung cấp")
        
    # Check VAT mismatch (excluding DT1)
    if entry.dt_group != 1 and entry.has_lines:
        vat_diff = round(abs(entry.header_vat - entry.lines_vat), 2)
        if vat_diff > tolerance_vnd:
            reasons.append(f"Lệch tiền thuế VAT: Header={format_vietnamese_currency(entry.header_vat)}, Lines={format_vietnamese_currency(entry.lines_vat)}")

    # Check missing invoice number
    if not entry.invoice_number:
        reasons.append("Khuyết số hóa đơn chứng từ")

    # Check unknown period
    if entry.period == "UNKNOWN":
        reasons.append("Kỳ kế toán không xác định")

    if reasons:
        return MatchResult(
            dt_code=entry.dt_code,
            company=entry.company,
            period=entry.period,
            header_total=entry.header_total,
            expected_total=expected,
            discrepancy=diff,
            flag=BreakFlag.PENDING_REVIEW,
            message=f"Cần phê duyệt duyệt thủ công. Lý do: {'; '.join(reasons)}. Chênh lệch: {format_vietnamese_currency(diff)}"
        )

    # 6. Normal Rounding Break
    if is_rounding:
        return MatchResult(
            dt_code=entry.dt_code,
            company=entry.company,
            period=entry.period,
            header_total=entry.header_total,
            expected_total=expected,
            discrepancy=diff,
            flag=BreakFlag.ROUNDING_BREAK,
            message=f"Lệch dung sai làm tròn nhỏ hợp lệ: {format_vietnamese_currency(diff)}"
        )

    # 7. Real Discrepancy
    return MatchResult(
        dt_code=entry.dt_code,
        company=entry.company,
        period=entry.period,
        header_total=entry.header_total,
        expected_total=expected,
        discrepancy=diff,
        flag=BreakFlag.REAL_DISCREPANCY,
        message=f"Chênh lệch số học lớn vượt ngưỡng dung sai: {format_vietnamese_currency(diff)}"
    )

def identify_repeated_companies(all_results: List[MatchResult], threshold: int = 2) -> Set[str]:
    """
    Identifies vendors who repeatedly have discrepancies.
    """
    stats: Dict[str, int] = {}
    for r in all_results:
        if r.flag != BreakFlag.MATCHED:
            stats[r.company] = stats.get(r.company, 0) + 1
            
    return {comp for comp, count in stats.items() if count >= threshold}

def export_packet(results: List[MatchResult], period: str) -> Dict[str, Any]:
    """
    Saves JSON report snapshot to output directory.
    """
    output_dir = os.getenv("OUTPUT_DIR", "ket_qua")
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"reconcile_report_{period}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    summary = {
        "period": period,
        "timestamp": datetime.now().isoformat(),
        "total_invoices": len(results),
        "matched_count": sum(1 for r in results if r.flag == BreakFlag.MATCHED),
        "rounding_break_count": sum(1 for r in results if r.flag == BreakFlag.ROUNDING_BREAK),
        "real_discrepancy_count": sum(1 for r in results if r.flag == BreakFlag.REAL_DISCREPANCY),
        "pending_review_count": sum(1 for r in results if r.flag == BreakFlag.PENDING_REVIEW),
        "other_breaks_count": sum(1 for r in results if r.flag not in [BreakFlag.MATCHED, BreakFlag.ROUNDING_BREAK, BreakFlag.REAL_DISCREPANCY, BreakFlag.PENDING_REVIEW]),
        "details": [asdict(r) for r in results]
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved local JSON reconciliation packet to: {filepath}")
    except Exception as e:
        logger.error(f"Failed to export JSON report: {e}")

    # Generate XLSX if openpyxl is installed
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Đối soát {period}"
        
        # Style Definitions
        font_title = Font(name="Arial", size=14, bold=True)
        font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Arial", size=10)
        fill_header = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        thin = Side(border_style="thin", color="D3D3D3")
        border_cell = Border(left=thin, right=thin, top=thin, bottom=thin)
        
        # Color Map for Flags
        color_map = {
            BreakFlag.MATCHED: "E2EFDA",            # Green
            BreakFlag.ROUNDING_BREAK: "FFF2CC",     # Yellow
            BreakFlag.PENDING_REVIEW: "FCE4D6",     # Orange
            BreakFlag.REAL_DISCREPANCY: "F8CBAD",   # Light Red
            BreakFlag.MISSING_HEADER_BREAK: "F8CBAD",
            BreakFlag.MISSING_LINES_BREAK: "F8CBAD",
            BreakFlag.CROSS_PERIOD_DUPLICATE: "C9C9C9"
        }
        
        ws.append([f"BÁO CÁO ĐỐI SOÁT SỔ SÁCH KỲ {period}"])
        ws.cell(row=1, column=1).font = font_title
        ws.append([]) # Empty row
        
        headers = ["Mã đối tượng", "Đơn vị bán", "Header Total", "Expected Total", "Chênh lệch", "Mã cờ", "Chi tiết / Ghi chú"]
        ws.append(headers)
        
        for col_idx in range(1, 8):
            cell = ws.cell(row=3, column=col_idx)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center")
            
        for r in results:
            diff_str = f"{r.discrepancy:,.0f} đ".replace(",", ".") if r.discrepancy > 0 else "0 đ"
            row_data = [
                r.dt_code,
                r.company,
                f"{r.header_total:,.0f} đ".replace(",", "."),
                f"{r.expected_total:,.0f} đ".replace(",", "."),
                diff_str,
                r.flag.value,
                r.message
            ]
            ws.append(row_data)
            
            # Formatting cells
            curr_row = ws.max_row
            flag_color = color_map.get(r.flag, "FFFFFF")
            fill_flag = PatternFill(start_color=flag_color, end_color=flag_color, fill_type="solid")
            
            for col_idx in range(1, 8):
                c = ws.cell(row=curr_row, column=col_idx)
                c.font = font_data
                c.border = border_cell
                if col_idx in [3, 4, 5]:
                    c.alignment = Alignment(horizontal="right")
                if col_idx == 6:
                    c.fill = fill_flag
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    
        # Auto-fit columns
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        xlsx_filename = f"reconcile_report_{period}_{timestamp}.xlsx"
        xlsx_filepath = os.path.join(output_dir, xlsx_filename)
        wb.save(xlsx_filepath)
        logger.info(f"Saved local Excel reconciliation packet to: {xlsx_filepath}")
        summary["excel_filepath"] = xlsx_filepath
        
    except ImportError:
        logger.warning("openpyxl is not installed. Skipping Excel report generation.")
    except Exception as e:
        logger.error(f"Failed to export Excel report: {e}")

    return summary

async def write_to_sp3(results: List[MatchResult], period: str, service: GoogleSyncService):
    """
    Ghi kết quả đối soát vào Spreadsheet #3 (tab Reconciliation_Report_YYYYMM và Recon_Audit_Log)
    """
    spreadsheet_id = os.getenv("RECON_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
    report_tab = f"Reconciliation_Report_{period}"
    audit_tab = "Recon_Audit_Log"

    # 1. Ghi Reconciliation_Report_YYYYMM
    service.ensure_tab_exists(report_tab, spreadsheet_id)
    
    headers = ["Mã đối tượng", "Đơn vị bán", "Header Total", "Expected Total", "Chênh lệch", "Loại Break", "Chi tiết / Ghi chú"]
    rows_to_write = [headers]
    for r in results:
        rows_to_write.append([
            r.dt_code,
            r.company,
            r.header_total,
            r.expected_total,
            r.discrepancy,
            r.flag.value,
            r.message
        ])
        
    # Clear and update the sheet tab
    service.sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{report_tab}!A1:Z10000"
    ).execute()
    
    service.sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{report_tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows_to_write}
    ).execute()
    
    logger.info(f"Successfully wrote {len(results)} reconciliation rows to tab '{report_tab}' of Spreadsheet '{spreadsheet_id}'.")

    # 2. Append Recon_Audit_Log
    service.ensure_tab_exists(audit_tab, spreadsheet_id)
    
    audit_headers = [
        "Run ID", "Thời gian chạy", "Kỳ đối soát", "Trigger bởi", 
        "Tổng số hóa đơn", "Khớp chính xác", "Lệch làm tròn", "Lệch thực sự", 
        "Báo cáo tab", "Trạng thái", "Người phê duyệt", "Ghi chú"
    ]
    
    existing_audit = service.get_sheet_data(spreadsheet_id, audit_tab)
    if not existing_audit or len(existing_audit) == 0:
        service.sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{audit_tab}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [audit_headers]}
        ).execute()

    matched = sum(1 for r in results if r.flag == BreakFlag.MATCHED)
    rounding = sum(1 for r in results if r.flag == BreakFlag.ROUNDING_BREAK)
    real_disc = sum(1 for r in results if r.flag in [BreakFlag.REAL_DISCREPANCY, BreakFlag.PENDING_REVIEW])
    
    audit_row = [
        str(uuid.uuid4()),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        period,
        "manual_dashboard",
        len(results),
        matched,
        rounding,
        real_disc,
        report_tab,
        "COMPLETED",
        "", # Approved by (to be filled by user)
        "Đối soát tự động bằng hệ thống"
    ]
    
    service.sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{audit_tab}!A:L",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [audit_row]}
    ).execute()
    
    logger.info("Reconciliation audit trail written successfully.")

async def run_reconciliation(period: str, service: GoogleSyncService) -> Dict[str, Any]:
    """
    Hàm điều phối chính chạy đối soát ledger (chuẩn hóa -> khớp -> flag -> ghi sheet -> xuất file)
    """
    # 1. Soft read-lock checking
    if sheet_write_lock.locked():
        # Wait up to 10 seconds for write lock to release
        logger.info("OCR write lock is currently active. Waiting...")
        for _ in range(5):
            await asyncio.sleep(2)
            if not sheet_write_lock.locked():
                break
        else:
            raise RuntimeError("Google Sheet is currently locked by a concurrent write operation. Please retry.")

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
    lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

    # Read records from GSheet
    logger.info(f"Reading records from {header_tab} and {lines_tab}...")
    header_rows = await asyncio.to_thread(service.get_sheet_data, spreadsheet_id, header_tab)
    lines_rows = await asyncio.to_thread(service.get_sheet_data, spreadsheet_id, lines_tab)

    if not header_rows or len(header_rows) <= 1:
        raise ValueError(f"Tab '{header_tab}' is empty or contains no records.")

    # 2. Normalize
    entries_map = normalize_all_entries(header_rows, lines_rows)
    all_entries = list(entries_map.values())

    # Build history results to identify repeated discrepancy patterns
    # Fitler history discrepancies for previous periods
    history_mismatches = []
    for dt, entry in entries_map.items():
        if entry.period != period and entry.period != "UNKNOWN":
            # calculate discrepancy simple
            # DT1 simple match
            if entry.dt_group == 1:
                expected = entry.header_raw - entry.header_discount + entry.header_vat
            else:
                expected = entry.lines_total - entry.header_discount
            diff = abs(entry.header_total - expected)
            if diff > 1.0: # arbitrary 1.0 VND discrepancy threshold
                history_mismatches.append(entry)

    repeated_companies = identify_repeated_companies(
        [MatchResult(x.dt_code, x.company, x.period, x.header_total, 0, 1.0, BreakFlag.ROUNDING_BREAK, "") for x in history_mismatches],
        threshold=int(os.getenv("RECON_REPEAT_FLAG_THRESHOLD", "2"))
    )

    # 3. Match and flag
    if period == "ALL":
        period_entries = all_entries
    else:
        period_entries = [e for e in all_entries if e.period == period]
        
    if not period_entries:
        raise ValueError(f"Không tìm thấy bản ghi nào cho kỳ đối soát: {period}")

    tolerance_vnd = float(os.getenv("RECON_TOLERANCE_VND", "1000"))
    tolerance_pct = float(os.getenv("RECON_TOLERANCE_PCT", "0.005"))

    results: List[MatchResult] = []
    for entry in period_entries:
        res = match_entry(entry, tolerance_vnd, tolerance_pct, repeated_companies, all_entries)
        results.append(res)

    # 4. Ghi báo cáo lên Google Sheet SP#3
    await write_to_sp3(results, period, service)

    # 5. Xuất file local packet
    summary = export_packet(results, period)
    return summary
