#!/usr/bin/env python3
"""
Migration Script: Standardize Column B DateTime Format in Data_Header_V2.
Transforms 'YYYY-MM-DD HH:MM:SS' (or any legacy date format) -> 'HH:MM:SS DD-MM-YYYY'.

Safety Guarantees:
1. Creates full snapshot backup in ops/backups/ before modifying.
2. Updates ONLY Column B (Range: Data_Header_V2!B2:B{N}).
3. Zero Impact: All other 13 columns (A, C-N) and Data_Lines_V2 remain 100% untouched.
"""
import os
import sys
import json
import datetime
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from services.google_service import GoogleSyncService
from services.business_rules import normalize_datetime_vn


def backup_sheet_state(service: GoogleSyncService, spreadsheet_id: str) -> str:
    """Create JSON backup snapshot of current sheets before migration."""
    backup_dir = BASE_DIR / "ops" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"snapshot_before_datetime_migration_{timestamp}.json"
    
    header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
    lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")
    
    header_data = service.get_sheet_data(spreadsheet_id, header_tab) or []
    lines_data = service.get_sheet_data(spreadsheet_id, lines_tab) or []
    
    snapshot = {
        "timestamp": timestamp,
        "spreadsheet_id": spreadsheet_id,
        "header_tab": header_tab,
        "header_count": len(header_data),
        "header_data": header_data,
        "lines_tab": lines_tab,
        "lines_count": len(lines_data),
        "lines_data": lines_data
    }
    
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
    print(f"[*] [AN TOÀN] Đã lưu snapshot sao lưu tại: {backup_file.name}")
    return str(backup_file)


def migrate_header_datetime_format(dry_run: bool = False):
    """
    Standardize all Column B cells in Data_Header_V2 to 'HH:MM:SS DD-MM-YYYY'.
    """
    print("=" * 70)
    print("🚀 BẮT ĐẦU CHUẨN HÓA CẤU TRÚC THỜI GIAN CỘT B (Data_Header_V2)")
    print("   Mục tiêu: 'HH:MM:SS DD-MM-YYYY' (Giờ:Phút:Giây Ngày-Tháng-Năm)")
    print(f"   Chế độ: {'DRY RUN (Chỉ kiểm tra)' if dry_run else 'EXECUTE (Cập nhật trực tiếp)'}")
    print("=" * 70)

    service = GoogleSyncService()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID") or os.getenv("GOOGLE_SHEET_MAIN_ID")
    if not spreadsheet_id:
        print("[!] LỖI: Không tìm thấy GOOGLE_SHEET_ID trong cấu hình .env!")
        return

    header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
    header_data = service.get_sheet_data(spreadsheet_id, header_tab) or []

    if not header_data or len(header_data) <= 1:
        print("[*] Bảng Data_Header_V2 đang trống hoặc chỉ có dòng tiêu đề. Không cần di trú.")
        return

    # Backup snapshot first
    backup_file = backup_sheet_state(service, spreadsheet_id)

    total_rows = len(header_data) - 1
    modified_count = 0
    col_b_updates = []

    print(f"\n[*] Đang rà soát {total_rows} dòng dữ liệu trên '{header_tab}'...")
    print("-" * 70)
    print(f"{'Mã ĐT':<10} | {'Thời gian cũ':<25} -> {'Thời gian chuẩn hóa mới':<25}")
    print("-" * 70)

    for idx, row in enumerate(header_data[1:], start=2):
        dt_code = str(row[0]).strip() if len(row) > 0 else f"Row_{idx}"
        old_val = str(row[1]).strip() if len(row) > 1 else ""
        
        # Standardize using business_rules normalizer
        new_val = normalize_datetime_vn(old_val, prefix_quote=True) if old_val else ""

        col_b_updates.append([new_val])

        # Check if changed
        clean_old = old_val.lstrip("'")
        clean_new = new_val.lstrip("'")
        if clean_old != clean_new:
            modified_count += 1
            print(f"{dt_code:<10} | {old_val:<25} -> {new_val:<25}")
        else:
            print(f"{dt_code:<10} | {old_val:<25} == (Đã chuẩn)")

    print("-" * 70)
    print(f"[*] Tổng số bản ghi cần chuẩn hóa: {modified_count}/{total_rows}")

    if dry_run:
        print("\n[!] [DRY-RUN] Hoàn tất mô phỏng. Chưa có dữ liệu nào bị thay đổi trên Sheet.")
        return

    if modified_count == 0:
        print("\n✅ Toàn bộ Cột B trên Data_Header_V2 đã ở định dạng chuẩn. Không cần cập nhật.")
        return

    # Update ONLY Column B (Range: B2:B{N})
    end_row = 1 + len(col_b_updates)
    range_name = f"{header_tab}!B2:B{end_row}"
    
    print(f"\n[*] Đang cập nhật duy nhất Cột B (dải ô {range_name})...")
    service.sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": col_b_updates}
    ).execute()

    print(f"✅ THÀNH CÔNG: Đã chuẩn hóa toàn bộ {modified_count} dòng Cột B sang 'HH:MM:SS DD-MM-YYYY'!")
    print(f"   Bảo toàn tuyệt đối 100% các Cột A, C-N và bảng Data_Lines_V2.")
    print("=" * 70)


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    migrate_header_datetime_format(dry_run=is_dry)
