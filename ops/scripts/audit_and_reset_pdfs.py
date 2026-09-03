import os
import sys
import re

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.google_service import GoogleSyncService

def main():
    service = GoogleSyncService()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    inbox_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

    print("[*] === AUDIT VÀ RESET TOÀN BỘ FILE PDF & DỮ LIỆU BẢNG TÍNH ===")
    print(f"  - Folder Chưa Xử Lý (Inbox): {inbox_folder_id}")
    print(f"  - Folder Đã Xử Lý (Processed): {processed_folder_id}")

    # 1. Tìm tất cả file PDF trong Google Drive
    print("\n[*] Đang tìm kiếm toàn bộ tệp PDF trên Google Drive...")
    query = "trashed = false and (mimeType = 'application/pdf' or name contains '.pdf')"
    all_pdf_files = []
    page_token = None
    while True:
        res = service.drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, parents, size)",
            pageSize=1000,
            pageToken=page_token
        ).execute()
        all_pdf_files.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break

    print(f"[+] Tìm thấy {len(all_pdf_files)} file PDF trong toàn bộ Google Drive:")
    pdf_ids = set()
    for f in all_pdf_files:
        pdf_ids.add(f["id"])
        print(f"  - ID: {f['id']} | Tên: {f['name']} | Parents: {f.get('parents')}")

    # 2. Đọc dữ liệu từ Google Sheets
    print("\n[*] Đang kiểm tra dữ liệu trên Google Sheets...")
    headers_rows = service.get_sheet_data(spreadsheet_id, "Data_Header_V2") or []
    lines_rows = service.get_sheet_data(spreadsheet_id, "Data_Lines_V2") or []

    pdf_dt_codes = set()
    
    clean_headers = []
    if headers_rows:
        clean_headers.append(headers_rows[0])
        for r in headers_rows[1:]:
            dt_code = str(r[0] if len(r) > 0 else "").strip()
            link = str(r[11] if len(r) > 11 else "").strip()
            doc_code = str(r[5] if len(r) > 5 else "").lower()
            notes = str(r[12] if len(r) > 12 else "").lower()

            # Trích xuất file_id từ link
            file_id_in_link = None
            m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", link)
            if m:
                file_id_in_link = m.group(1)

            is_pdf_row = (
                (file_id_in_link and file_id_in_link in pdf_ids) or
                ".pdf" in link.lower() or
                ".pdf" in doc_code or
                ".pdf" in notes
            )

            if is_pdf_row and dt_code:
                pdf_dt_codes.add(dt_code)
                print(f"  ❌ Phát hiện dòng PDF cần xóa: {dt_code} (Link ID: {file_id_in_link})")
            else:
                clean_headers.append(r)

    clean_lines = []
    if lines_rows:
        clean_lines.append(lines_rows[0])
        for r in lines_rows[1:]:
            dt_code = str(r[0] if len(r) > 0 else "").strip()
            if dt_code in pdf_dt_codes:
                continue
            clean_lines.append(r)

    rem_h = len(headers_rows) - len(clean_headers)
    rem_l = len(lines_rows) - len(clean_lines)

    print(f"\n[📊] Tổng kết dữ liệu PDF cần xóa khỏi Sheet:")
    print(f"  - Headers: {rem_h} dòng")
    print(f"  - Lines: {rem_l} dòng")

    # 3. Ghi đè lại dữ liệu sạch lên Google Sheet
    if rem_h > 0:
        res_h = service.overwrite_sheet_data(clean_headers, spreadsheet_id=spreadsheet_id, sheet_name="Data_Header_V2")
        print(f"  -> Ghi lại Data_Header_V2: {res_h.get('success')}")
    if rem_l > 0:
        res_l = service.overwrite_sheet_data(clean_lines, spreadsheet_id=spreadsheet_id, sheet_name="Data_Lines_V2")
        print(f"  -> Ghi lại Data_Lines_V2: {res_l.get('success')}")

    # 4. Di chuyển toàn bộ file PDF không nằm trong Inbox về Inbox (Chua_Xu_Ly)
    print("\n[*] Đang kiểm tra vị trí file PDF và di chuyển về folder Chưa Xử Lý...")
    moved_count = 0
    for f in all_pdf_files:
        fid = f["id"]
        fname = f["name"]
        parents = f.get("parents") or []

        if inbox_folder_id not in parents:
            print(f"  🚚 Đang di chuyển {fname} (ID: {fid}) về folder Chưa Xử Lý ({inbox_folder_id})...")
            try:
                res_m = service.move_file_to_folder(fid, inbox_folder_id)
                if res_m:
                    moved_count += 1
                    print(f"    ✅ Thành công!")
                else:
                    print(f"    ❌ Thất bại!")
            except Exception as e:
                print(f"    ⚠️ Lỗi khi di chuyển {fname}: {e}")
        else:
            print(f"  ℹ️ File {fname} đã nằm sẵn trong folder Chưa Xử Lý.")

    print(f"\n[🎉] HOÀN TẤT RESET FILE PDF & SHEET DỮ LIỆU!")
    print(f"  - Số dòng PDF đã xóa: {rem_h} header, {rem_l} lines")
    print(f"  - Số file PDF đã di chuyển về folder Chưa Xử Lý: {moved_count}/{len(all_pdf_files)}")

if __name__ == "__main__":
    main()
