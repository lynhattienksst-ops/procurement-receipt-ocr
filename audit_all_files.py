import os
import re
from services.google_service import GoogleSyncService

gs = GoogleSyncService()
inbox_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
proc_id = os.getenv('GOOGLE_DRIVE_PROCESSED_FOLDER_ID')

def get_all_drive_files(folder_id):
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false and (mimeType contains 'image/' or mimeType = 'application/pdf')"
    while True:
        res = gs.drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, createdTime)",
            pageSize=100,
            pageToken=page_token
        ).execute()
        files.extend(res.get('files', []))
        page_token = res.get('nextPageToken')
        if not page_token:
            break
    return files

inbox_files = get_all_drive_files(inbox_id)
proc_files = get_all_drive_files(proc_id)

sheet_rows = gs.get_sheet_data()
links_rows = gs.get_sheet_data(sheet_name='Links_Hoa_Don')

print("=" * 60)
print("BÁO CÁO THỐNG KÊ THỰC TẾ TRÊN TOÀN BỘ HỆ THỐNG")
print("=" * 60)
print(f"1. Tổng file trong thư mục Inbox (chưa xử lý): {len(inbox_files)}")
print(f"2. Tổng file trong thư mục Da_Xu_Ly: {len(proc_files)}")
print(f"-> TỔNG CỘNG FILE TRÊN GOOGLE DRIVE: {len(inbox_files) + len(proc_files)}")
print(f"3. Tổng số dòng trong Bang_Ke_Hoa_Don: {len(sheet_rows) - 1} (trừ header)")
print(f"4. Tổng số dòng trong Links_Hoa_Don: {len(links_rows) - 1} (trừ header)")

linked_file_ids = set()
linked_file_names = set()
for r in links_rows[1:]:
    if len(r) >= 2 and r[1]:
        linked_file_names.add(str(r[1]).strip().lower())
    if len(r) >= 3 and r[2]:
        link_url = str(r[2]).strip()
        m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', link_url)
        if m:
            linked_file_ids.add(m.group(1))

all_drive_files = inbox_files + proc_files
missing_files = []
for f in all_drive_files:
    fid = f['id']
    fname = f['name'].strip().lower()
    if fid not in linked_file_ids and fname not in linked_file_names:
        missing_files.append(f)

print(f"\n5. Số file trên Drive CHƯA có trong Google Sheet: {len(missing_files)}")
for mf in missing_files:
    print(f"  - [Chưa có trên Sheet] Tên file: {mf['name']} | ID: {mf['id']}")
