import os
import sys
import re
from dotenv import load_dotenv
load_dotenv()

# Thêm thư mục cha vào sys.path để import services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.google_service import GoogleSyncService

def main():
    service = GoogleSyncService()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    inbox_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID") # folder Chưa Xử Lý
    processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID") # folder Đã Xử Lý

    print("[*] === BẮT ĐẦU QUY TRÌNH RESET & DỌN DẸP DỮ LIỆU FILE PDF ===")
    
    # 1. Đọc dữ liệu các sheet hiện tại (chỉ còn schema V2)
    headers_rows = service.get_sheet_data(spreadsheet_id, "Data_Header_V2") or []
    lines_rows = service.get_sheet_data(spreadsheet_id, "Data_Lines_V2") or []

    pdf_dt_codes = set()
    pdf_drive_ids = set()

    # Nhận diện các dòng PDF trong Data_Header_V2
    clean_headers = []
    if headers_rows:
        clean_headers.append(headers_rows[0]) # Giữ nguyên dòng tiêu đề
        for r in headers_rows[1:]:
            link = str(r[11] if len(r) > 11 else "").lower()
            code = str(r[0] if len(r) > 0 else "").strip()
            doc_code = str(r[5] if len(r) > 5 else "").lower()
            notes = str(r[12] if len(r) > 12 else "").lower()

            is_pdf = ".pdf" in link or ".pdf" in doc_code or ".pdf" in notes
            if is_pdf and code:
                pdf_dt_codes.add(code)
                m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", link)
                if m:
                    pdf_drive_ids.add(m.group(1))
            else:
                clean_headers.append(r)

    # Lọc Data_Lines_V2
    clean_lines = []
    if lines_rows:
        clean_lines.append(lines_rows[0]) # Giữ tiêu đề
        for r in lines_rows[1:]:
            code = str(r[0] if len(r) > 0 else "").strip()
            if code in pdf_dt_codes:
                continue
            clean_lines.append(r)

    removed_header_count = len(headers_rows) - len(clean_headers)
    removed_lines_count = len(lines_rows) - len(clean_lines)

    print(f"[+] Tìm thấy {len(pdf_dt_codes)} mã hóa đơn PDF cần xóa khỏi Sheet:")
    print(f"    - Headers: Xóa {removed_header_count} dòng")
    print(f"    - Lines: Xóa {removed_lines_count} dòng")

    # 2. Ghi đè lại sheet
    if removed_header_count > 0:
        res_h = service.overwrite_sheet_data(clean_headers, spreadsheet_id=spreadsheet_id, sheet_name="Data_Header_V2")
        print(f"  -> Ghi lại Data_Header_V2: {res_h.get('success')}")
    if removed_lines_count > 0:
        res_l = service.overwrite_sheet_data(clean_lines, spreadsheet_id=spreadsheet_id, sheet_name="Data_Lines_V2")
        print(f"  -> Ghi lại Data_Lines_V2: {res_l.get('success')}")

    # 3. Quét và di chuyển các file PDF từ folder Đã Xử Lý về folder Chưa Xử Lý
    print("\n[*] Đang quét các tệp PDF trong thư mục Đã Xử Lý (Google Drive)...")
    if not processed_folder_id:
        print("[!] GOOGLE_DRIVE_PROCESSED_FOLDER_ID chưa được cấu hình.")
        return

    query = f"'{processed_folder_id}' in parents and trashed = false and (mimeType = 'application/pdf' or name contains '.pdf')"
    pdf_files_to_move = []
    page_token = None
    while True:
        res = service.drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, size)",
            pageSize=1000,
            pageToken=page_token
        ).execute()
        pdf_files_to_move.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break

    print(f"[+] Phát hiện {len(pdf_files_to_move)} tệp PDF trong thư mục Đã Xử Lý cần di chuyển về Chưa Xử Lý.")
    success_move = 0
    for idx, f in enumerate(pdf_files_to_move, 1):
        fid = f["id"]
        fname = f["name"]
        try:
            res_move = service.move_file_to_folder(fid, inbox_folder_id)
            if res_move:
                success_move += 1
                print(f"  [{idx:02d}/{len(pdf_files_to_move)}] ✅ Đã di chuyển: {fname} (ID: {fid})")
            else:
                print(f"  [{idx:02d}/{len(pdf_files_to_move)}] ❌ Thất bại khi chuyển: {fname}")
        except Exception as e:
            print(f"  [{idx:02d}/{len(pdf_files_to_move)}] ⚠️ Lỗi: {fname}: {e}")

    print(f"\n[🎉] HOÀN TẤT RESET FILE PDF! Đã chuyển thành công {success_move}/{len(pdf_files_to_move)} file PDF về folder Chưa Xử Lý.\n")

if __name__ == "__main__":
    main()
