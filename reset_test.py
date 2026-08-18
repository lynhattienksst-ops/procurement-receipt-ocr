import os
import sys
from dotenv import load_dotenv

sys.path.append('/app')
from services.google_service import GoogleSyncService

def reset_env():
    load_dotenv(override=True)
    google_service = GoogleSyncService()
    
    if not google_service.is_connected():
        google_service._init_auth()
        
    sheet_id = os.getenv('GOOGLE_SHEET_ID')
    main_tab = os.getenv('GOOGLE_SHEET_NAME', 'Bang_Ke_Hoa_Don')
    links_tab = os.getenv('GOOGLE_SHEET_LINKS_NAME', 'Links_Hoa_Don')
    
    headers_main = [
        "Mã đối tượng",
        "Ngày, tháng, năm",
        "Tên công ty",
        "Địa chỉ bên bán",
        "Địa chỉ bên nhận",
        "Mã hóa đơn, chứng từ",
        "Tên hàng hóa, dịch vụ",
        "Số lượng",
        "Đơn giá",
        "% VAT",
        "VAT",
        "Thành tiền",
        "Người mua/nhận hàng",
        "Ghi chú"
    ]
    headers_links = [
        "Mã đối tượng",
        "Tên file",
        "Link Drive",
        "Xác nhận"
    ]
    
    try:
        google_service.sheets_service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=f'{main_tab}!A1:Z1000',
            body={}
        ).execute()
        google_service.sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f'{main_tab}!A1:N1',
            valueInputOption='USER_ENTERED',
            body={'values': [headers_main]}
        ).execute()
        print(f'[+] Initialized header for sheet: {main_tab}')
        
        try:
            google_service.sheets_service.spreadsheets().values().clear(
                spreadsheetId=sheet_id,
                range=f'{links_tab}!A1:Z1000',
                body={}
            ).execute()
            google_service.sheets_service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f'{links_tab}!A1:D1',
                valueInputOption='USER_ENTERED',
                body={'values': [headers_links]}
            ).execute()
            print(f'[+] Initialized header for sheet: {links_tab}')
        except Exception as e:
            print(f'[-] Error initializing links header: {e}')
            
    except Exception as e:
        print(f'Error clearing sheets: {e}')

    processed_folder = os.getenv('GOOGLE_DRIVE_PROCESSED_FOLDER_ID')
    input_folder = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
    
    if processed_folder and input_folder:
        try:
            query = f"'{processed_folder}' in parents and trashed = false"
            results = google_service.drive_service.files().list(
                q=query,
                fields='files(id, name)'
            ).execute()
            files = results.get('files', [])
            print(f'[*] Found {len(files)} files in processed folder.')
            for f in files:
                google_service.move_file_to_folder(f['id'], input_folder)
            print(f'[+] Successfully moved files back to input folder.')
        except Exception as e:
            print(f'[-] Error moving files: {e}')

if __name__ == '__main__':
    reset_env()
