import os
import re
from services.google_service import GoogleSyncService
from services.ocr_engine import UnifiedOCREngine
from services.duplicate_checker import DuplicateChecker

gs = GoogleSyncService()
checker = DuplicateChecker()
sheet_rows = gs.get_sheet_data()
checker.load_from_sheet_rows(sheet_rows)

links_rows = gs.get_sheet_data(sheet_name='Links_Hoa_Don')
linked_file_ids = set()
for r in links_rows[1:]:
    if len(r) >= 3 and r[2]:
        m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', str(r[2]))
        if m: linked_file_ids.add(m.group(1))

# Find 36 unlinked files
proc_id = os.getenv('GOOGLE_DRIVE_PROCESSED_FOLDER_ID')
query = f"'{proc_id}' in parents and trashed = false and (mimeType contains 'image/' or mimeType = 'application/pdf')"

all_files = []
page_token = None
while True:
    res = gs.drive_service.files().list(q=query, fields="nextPageToken, files(id, name)", pageSize=100, pageToken=page_token).execute()
    all_files.extend(res.get('files', []))
    page_token = res.get('nextPageToken')
    if not page_token: break

missing = [f for f in all_files if f['id'] not in linked_file_ids]
print(f"Kiểm tra chi tiết {len(missing)} file chưa có trên Sheet:")

ocr = UnifiedOCREngine()

for idx, f in enumerate(missing[:10], 1):
    fid = f['id']
    fname = f['name']
    try:
        content, _ = gs.download_file_bytes(fid)
        parsed = ocr.process_image(content, mode="cloud")
        order_id = parsed.get("order_id") or parsed.get("tracking_number") or parsed.get("invoice_number") or ""
        is_dup, msg = checker.check_duplicate(order_id)
        print(f"{idx}. {fname} (ID: {fid}):")
        print(f"   -> Trích xuất: Merchant='{parsed.get('merchant_name')}', OrderID='{order_id}', Total={parsed.get('total_amount')}")
        print(f"   -> Kết quả đối soát: is_duplicate={is_dup} ({msg if is_dup else 'File HỢP LỆ mới'})")
    except Exception as e:
        print(f"{idx}. {fname} -> Lỗi OCR: {e}")
