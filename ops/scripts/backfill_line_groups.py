"""
Backfill Cột M "Nhóm hàng" + Cột N "Nguồn phân loại" cho toàn bộ Data_Lines_V2.

ADR line-item-grouping (Việc A). Phân loại loại-hàng ở CẤP DÒNG theo trục kế toán
chi phí VN. Script này CHỈ ghi 2 cột M:N — KHÔNG đụng A→L, KHÔNG thêm/bớt dòng.

An toàn:
  - Exact Row Guard: đọc lại sau khi ghi, xác nhận số dòng KHÔNG đổi.
  - Targeted range update `Data_Lines_V2!M2:N{N}` — không `clear`, không ghi A→L.
  - Lấy `sheet_write_lock` (import từ server.py; fallback local lock khi chạy standalone),
    y hệt reconciliation_engine.py.
  - Không tự chạy khi engine OCR đang ghi (lock bận) — báo và thoát.

Dùng:
  docker exec procurement-server python ops/scripts/backfill_line_groups.py --dry-run
  docker exec procurement-server python ops/scripts/backfill_line_groups.py

Trước khi chạy thật: `docker exec procurement-server python ops/scripts/backup_sheet_state.py`
"""
import os
import sys
import asyncio
import argparse
from collections import Counter

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.google_service import GoogleSyncService  # noqa: E402
from services.business_rules import classify_line_item, LINE_GROUP_CODES  # noqa: E402

# Cùng cơ chế với reconciliation_engine.py: dùng lock của server nếu import được.
try:
    from server import sheet_write_lock
except ImportError:
    sheet_write_lock = asyncio.Lock()

LINES_TAB = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

# Index cột (0-based) trong Data_Lines_V2
COL_A_DT = 0
COL_C_NAME = 2
COL_E_UNIT = 4
COL_M_GROUP = 12
COL_N_SOURCE = 13


def _classify_row(row):
    """Trả về (group_code, source) cho 1 dòng Lines. DT1 = dòng gộp → chỉ 3 nhóm."""
    name = row[COL_C_NAME] if len(row) > COL_C_NAME else ""
    unit = row[COL_E_UNIT] if len(row) > COL_E_UNIT else ""
    dt_code = str(row[COL_A_DT] if len(row) > COL_A_DT else "").strip().upper()
    return classify_line_item(name, unit, is_dt1=dt_code.startswith("DT1"))


def _print_distribution(pairs, total_data_rows):
    by_group = Counter(g for g, _ in pairs)
    by_source = Counter(s for _, s in pairs)
    print("\n=== PHÂN BỐ NHÓM HÀNG ===")
    for code in LINE_GROUP_CODES:
        n = by_group.get(code, 0)
        pct = (n / total_data_rows * 100) if total_data_rows else 0
        print(f"  {code:<10} {n:>5}  ({pct:5.1f}%)")
    print("\n=== PHÂN BỐ NGUỒN ===")
    for src, n in by_source.most_common():
        pct = (n / total_data_rows * 100) if total_data_rows else 0
        print(f"  {src:<14} {n:>5}  ({pct:5.1f}%)")
    review = by_group.get("CAN_SOAT", 0) + by_group.get("KHAC", 0)
    review_pct = (review / total_data_rows * 100) if total_data_rows else 0
    print(f"\n  Cần soát (CAN_SOAT + KHAC): {review} / {total_data_rows}  ({review_pct:.1f}%)")


async def _run(dry_run: bool):
    service = GoogleSyncService()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        print("[-] Chưa cấu hình GOOGLE_SHEET_ID.")
        return 1

    if sheet_write_lock.locked():
        print("[-] sheet_write_lock đang bận (engine OCR có thể đang ghi). Thử lại sau.")
        return 2

    all_rows = service.get_sheet_data(spreadsheet_id, LINES_TAB) or []
    if len(all_rows) < 2:
        print(f"[-] Tab {LINES_TAB} không có dòng dữ liệu.")
        return 1

    header = all_rows[0]
    data_rows = all_rows[1:]
    old_total = len(all_rows)
    n_data = len(data_rows)
    print(f"[*] {LINES_TAB}: {n_data} dòng dữ liệu (tổng {old_total} kể cả header).")

    header_needs_write = (
        len(header) <= COL_N_SOURCE
        or str(header[COL_M_GROUP]).strip() != "Nhóm hàng"
        or str(header[COL_N_SOURCE]).strip() != "Nguồn phân loại"
    )
    if header_needs_write:
        print("[i] Tiêu đề M1:N1 sẽ được ghi = ['Nhóm hàng', 'Nguồn phân loại'].")

    pairs = [_classify_row(r) for r in data_rows]
    _print_distribution(pairs, n_data)

    if dry_run:
        print("\n[dry-run] Không ghi gì lên Sheet.")
        # In vài mẫu cần soát để tinh keyword.
        samples = [(data_rows[i][COL_C_NAME] if len(data_rows[i]) > COL_C_NAME else "", g)
                   for i, (g, _) in enumerate(pairs) if g in ("CAN_SOAT", "KHAC")]
        print("\n=== MẪU CẦN SOÁT (tối đa 40) ===")
        for name, g in samples[:40]:
            print(f"  [{g:<9}] {name!r}")
        return 0

    values = [[g, s] for g, s in pairs]
    target_range = f"{LINES_TAB}!M2:N{1 + n_data}"

    async with sheet_write_lock:
        if header_needs_write:
            print(f"[*] Ghi tiêu đề {LINES_TAB}!M1:N1 ...")
            service.sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{LINES_TAB}!M1:N1",
                valueInputOption="RAW",
                body={"values": [["Nhóm hàng", "Nguồn phân loại"]]},
            ).execute()

        print(f"\n[*] Ghi {len(values)} dòng vào {target_range} ...")
        service.sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=target_range,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

        # Exact Row Guard: đọc lại, xác nhận số dòng KHÔNG đổi.
        after = service.get_sheet_data(spreadsheet_id, LINES_TAB) or []
        if len(after) != old_total:
            print(f"[X] EXACT ROW GUARD FAIL: số dòng đổi {old_total} -> {len(after)}. "
                  f"Kiểm tra Sheet ngay (khôi phục từ ops/backups/ nếu cần).")
            return 3

    print(f"[✓] Xong. Số dòng giữ nguyên ({old_total}). Đã ghi cột M:N.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Backfill Nhóm hàng (Cột M/N) cho Data_Lines_V2.")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in phân bố nhóm, không ghi Sheet.")
    args = ap.parse_args()
    rc = asyncio.run(_run(args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()

