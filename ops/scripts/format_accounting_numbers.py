"""
Chuẩn hóa các cột SỐ trong Data_Header_V2 + Data_Lines_V2 về chuẩn kế toán VN dùng
trong Excel/Sheet: ô là SỐ THẬT (để =SUM/=AVERAGE... chạy), làm tròn 2 chữ số lẻ,
kèm Number Format kế toán VN (âm trong ngoặc).

Việc:
  1. LÀM SẠCH GIÁ TRỊ — đọc từng ô, ép chuỗi kiểu VN ('387.997', '1,00', '0,') về
     số thuần qua parse_vietnamese_number, round(x, 2). Ghi lại bằng targeted range
     update (values.update từng cột) — KHÔNG clear, KHÔNG đụng cột khác.
  2. NUMBER FORMAT — batchUpdate repeatCell:
       - Cột tiền  -> "#,##0;(#,##0)"      (âm trong ngoặc, chuẩn kế toán VN)
       - Cột %     -> "0.##"               (số trơn, giữ nguyên như cũ)
       - Số lượng  -> "#,##0.##"

Cột đụng tới (0-based index):
  Data_Header_V2 : G=6  H=7  I=8  J=9                          (đều là tiền)
  Data_Lines_V2  : D=3 (SL) ; F=5 G=6 J=9 K=10 (tiền) ; H=7 I=8 (%)

An toàn:
  - Exact Row Guard: đọc lại sau khi ghi, số dòng KHÔNG đổi.
  - Lấy sheet_write_lock (import từ server.py; fallback local lock).
  - --dry-run: in mẫu trước->sau, không ghi.

Dùng:
  docker exec procurement-server python ops/scripts/format_accounting_numbers.py --dry-run
  docker exec procurement-server python ops/scripts/format_accounting_numbers.py

Trước khi chạy thật: docker exec procurement-server python ops/scripts/backup_sheet_state.py
"""
import os
import sys
import asyncio
import argparse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.google_service import GoogleSyncService  # noqa: E402
from services.business_rules import parse_vietnamese_number  # noqa: E402

try:
    from server import sheet_write_lock
except ImportError:
    sheet_write_lock = asyncio.Lock()

HEADER_TAB = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
LINES_TAB = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

FMT_MONEY = "#,##0;(#,##0)"
FMT_PCT = "0.##"
FMT_QTY = "#,##0.##"

# (index 0-based, nhãn) — dùng cho cả làm sạch giá trị lẫn set format.
HEADER_MONEY_COLS = [(6, "G Tổng tiền hàng"), (7, "H Chiết khấu"), (8, "I Thuế VAT"), (9, "J Tổng thanh toán")]
LINES_MONEY_COLS = [(5, "F Đơn giá"), (6, "G Chiết khấu MH"), (9, "J Tiền thuế VAT"), (10, "K Thành tiền")]
LINES_PCT_COLS = [(7, "H Tỷ lệ CK %"), (8, "I Thuế suất VAT %")]
LINES_QTY_COLS = [(3, "D Số lượng")]


def _col_letter(idx0):
    """0 -> A, 25 -> Z (đủ dùng cho phạm vi A..N)."""
    return chr(ord("A") + idx0)


def _clean_num(val):
    """Ép mọi ô về số thuần, round 2 lẻ. '387.997'->387997 ; '1,00'->1 ; '0,'->0."""
    if val is None or str(val).strip() in ("", "'"):
        return 0
    s = str(val).strip().lstrip("'")
    for suf in ("đ", "Đ", "₫", "vnđ", "VNĐ", "vnd", "VND", "%"):
        s = s.replace(suf, "")
    s = s.strip().rstrip(",").strip()
    if s in ("", "-"):
        return 0
    try:
        num = parse_vietnamese_number(s)
    except Exception:
        return val  # để nguyên nếu không parse được, người soát tự xử
    if num != num:  # NaN
        return 0
    r = round(float(num), 2)
    return int(r) if r == int(r) else r


def _gather_column_values(all_rows, idx0):
    """Trả về list giá trị đã làm sạch cho 1 cột, dài = số dòng dữ liệu (bỏ header)."""
    out = []
    for r in all_rows[1:]:
        raw = r[idx0] if len(r) > idx0 else ""
        out.append(_clean_num(raw))
    return out


def _sheet_id_map(service, spreadsheet_id):
    meta = service.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def _diff_samples(all_rows, idx0, cleaned, limit=6):
    """In vài ô có thay đổi (trước -> sau)."""
    shown = 0
    for i, r in enumerate(all_rows[1:]):
        raw = r[idx0] if len(r) > idx0 else ""
        new = cleaned[i]
        if str(raw).strip().lstrip("'") != str(new):
            print(f"      dòng {i + 2:>4}: {raw!r:>16}  ->  {new!r}")
            shown += 1
            if shown >= limit:
                break
    if shown == 0:
        print("      (không ô nào đổi)")


async def _process_tab(service, spreadsheet_id, tab, money_cols, pct_cols, qty_cols,
                       sheet_id, dry_run):
    all_rows = service.get_sheet_data(spreadsheet_id, tab) or []
    if len(all_rows) < 2:
        print(f"[-] {tab}: không có dòng dữ liệu, bỏ qua.")
        return True
    n_data = len(all_rows) - 1
    old_total = len(all_rows)
    print(f"\n=== {tab}: {n_data} dòng dữ liệu ===")

    all_target_cols = money_cols + pct_cols + qty_cols
    cleaned_by_idx = {}
    for idx0, label in all_target_cols:
        cleaned_by_idx[idx0] = _gather_column_values(all_rows, idx0)
        print(f"  [{label}] cột {_col_letter(idx0)}")
        _diff_samples(all_rows, idx0, cleaned_by_idx[idx0])

    if dry_run:
        return True

    async with sheet_write_lock:
        # 1. Ghi lại giá trị đã làm sạch — targeted update từng cột, KHÔNG clear.
        data = []
        for idx0, _label in all_target_cols:
            col = _col_letter(idx0)
            rng = f"{tab}!{col}2:{col}{1 + n_data}"
            data.append({"range": rng, "values": [[v] for v in cleaned_by_idx[idx0]]})
        service.sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        print(f"  [*] Đã ghi {len(data)} cột giá trị số thuần.")

        # 2. Number format — repeatCell theo dải cột.
        requests = []

        def _fmt_req(idx0, pattern):
            return {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": idx0,
                        "endColumnIndex": idx0 + 1,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }

        for idx0, _l in money_cols:
            requests.append(_fmt_req(idx0, FMT_MONEY))
        for idx0, _l in pct_cols:
            requests.append(_fmt_req(idx0, FMT_PCT))
        for idx0, _l in qty_cols:
            requests.append(_fmt_req(idx0, FMT_QTY))

        service.sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
        print(f"  [*] Đã set number format cho {len(requests)} cột.")

        # 3. Exact Row Guard.
        after = service.get_sheet_data(spreadsheet_id, tab) or []
        if len(after) != old_total:
            print(f"  [X] EXACT ROW GUARD FAIL {tab}: {old_total} -> {len(after)}. "
                  f"Khôi phục từ ops/backups/ ngay.")
            return False
    print(f"  [✓] {tab} xong, số dòng giữ nguyên ({old_total}).")
    return True


async def _run(dry_run):
    service = GoogleSyncService()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        print("[-] Chưa cấu hình GOOGLE_SHEET_ID.")
        return 1
    if sheet_write_lock.locked():
        print("[-] sheet_write_lock đang bận. Thử lại sau.")
        return 2

    if dry_run:
        print("[dry-run] Chỉ in mẫu trước->sau, KHÔNG ghi Sheet.")

    ids = _sheet_id_map(service, spreadsheet_id)

    ok_h = await _process_tab(
        service, spreadsheet_id, HEADER_TAB,
        HEADER_MONEY_COLS, [], [], ids.get(HEADER_TAB), dry_run,
    )
    ok_l = await _process_tab(
        service, spreadsheet_id, LINES_TAB,
        LINES_MONEY_COLS, LINES_PCT_COLS, LINES_QTY_COLS, ids.get(LINES_TAB), dry_run,
    )

    if dry_run:
        print("\n[dry-run] Xong. Chạy lại không có --dry-run để ghi thật.")
        return 0
    return 0 if (ok_h and ok_l) else 3


def main():
    ap = argparse.ArgumentParser(description="Chuẩn hóa cột số Header_V2 + Lines_V2 theo chuẩn kế toán VN.")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in mẫu, không ghi Sheet.")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
