import os
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2 import service_account
from googleapiclient.discovery import build
from services.google_service import GoogleSyncService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fast-mover")

def get_drive_client():
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def main():
    gs = GoogleSyncService()
    inbox_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    proc_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

    proc_files = gs.list_images_in_folder(proc_id)
    logger.info(f"Tìm thấy {len(proc_files)} file cần chuyển từ Da_Xu_Ly về Inbox.")

    if not proc_files:
        logger.info("Thư mục Da_Xu_Ly đã trống. Tất cả file đã ở trong Inbox!")
        return

    def move_single(file_item):
        fid = file_item["id"]
        try:
            drive = get_drive_client()
            drive.files().update(
                fileId=fid,
                addParents=inbox_id,
                removeParents=proc_id,
                fields="id, parents"
            ).execute()
            return True, fid
        except Exception as e:
            return False, f"{fid}: {e}"

    success_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(move_single, f): f for f in proc_files}
        for idx, fut in enumerate(as_completed(futures), start=1):
            ok, res = fut.result()
            if ok:
                success_count += 1
            if idx % 25 == 0 or idx == len(proc_files):
                logger.info(f"Tiến độ: Đã chuyển {idx}/{len(proc_files)} file về Inbox...")

    logger.info(f"HOÀN THÀNH: {success_count}/{len(proc_files)} file đã chuyển về Inbox thành công!")

if __name__ == "__main__":
    main()
