import os
import sys
import json
from datetime import datetime

# Thêm thư mục cha vào sys.path để import services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.google_service import GoogleSyncService

def main():
    service = GoogleSyncService()
    spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
    
    backup_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../backups'))
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[*] Đang tiến hành tạo bản Sao Lưu (Backup Snapshot) tại: {backup_dir}")
    
    sheets_to_backup = ['Data_Header_V2', 'Data_Lines_V2']
    backup_manifest = {
        "timestamp": timestamp,
        "sheets": {}
    }
    
    for sheet_name in sheets_to_backup:
        try:
            print(f"  - Đang đọc dữ liệu từ sheet '{sheet_name}'...")
            rows = service.get_sheet_data(spreadsheet_id, sheet_name)
            if rows is not None:
                file_name = f"{timestamp}_{sheet_name}.json"
                file_path = os.path.join(backup_dir, file_name)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, indent=2)
                backup_manifest["sheets"][sheet_name] = {
                    "rows_count": len(rows),
                    "file_path": file_path
                }
                print(f"    ✅ Đã lưu {len(rows)} dòng vào {file_name}")
            else:
                print(f"    ⚠️ Sheet '{sheet_name}' không khả dụng hoặc rỗng.")
        except Exception as e:
            print(f"    ⚠️ Lỗi khi đọc sheet '{sheet_name}': {e}")

    # Ghi manifest
    manifest_path = os.path.join(backup_dir, f"{timestamp}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(backup_manifest, f, ensure_ascii=False, indent=2)
    print(f"[🎉] HOÀN TẤT SAO LƯU DỮ LIỆU! Manifest: {manifest_path}\n")

if __name__ == "__main__":
    main()
