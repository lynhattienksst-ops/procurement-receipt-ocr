import os
import re
import sys
import logging
from services.google_service import GoogleSyncService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reset-system")

def reset_google_sheets(gs: GoogleSyncService):
    logger.info("=== BƯỚC 1: XÓA DỮ LIỆU GOOGLE SHEETS & ĐẶT LẠI HEADER CHUẨN ===")
    
    # 1. Header chuẩn cho Bang_Ke_Hoa_Don (14 cột theo Project_report)
    main_header = [
        "Mã Đối Tượng",
        "Ngày Tháng",
        "Đơn Vị Bán Hàng",
        "Địa Chỉ Người Bán",
        "Địa Chỉ Người Mua",
        "Số Hóa Đơn / Mã Đơn",
        "Tên Hàng Hóa / Dịch Vụ",
        "Số Lượng",
        "Đơn Giá",
        "Thuế Suất VAT",
        "Tiền Thuế VAT",
        "Tổng Tiền Hóa Đơn",
        "Người mua/nhận hàng",
        "Ghi chú"
    ]
    
    # 2. Header chuẩn cho Links_Hoa_Don
    links_header = [
        "Mã Đối Tượng",
        "Tên File",
        "Link Ảnh",
        "Xác nhận"
    ]

    # Xóa và ghi lại Bang_Ke_Hoa_Don
    res1 = gs.overwrite_sheet_data(
        target_sheet_id=os.getenv("GOOGLE_SHEET_ID"),
        target_tab=os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don"),
        rows=[main_header]
    )
    logger.info(f"Đã đặt lại Bang_Ke_Hoa_Don: {res1.get('message', 'Thành công')}")

    # Xóa và ghi lại Links_Hoa_Don
    res2 = gs.overwrite_sheet_data(
        target_sheet_id=os.getenv("GOOGLE_SHEET_ID"),
        target_tab=os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don"),
        rows=[links_header]
    )
    logger.info(f"Đã đặt lại Links_Hoa_Don: {res2.get('message', 'Thành công')}")

def move_files_back_to_inbox(gs: GoogleSyncService):
    logger.info("=== BƯỚC 2: CHUYỂN TẤT CẢ FILE TỪ ĐÃ XỬ LÝ VỀ THƯ MỤC INBOX ===")
    inbox_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    proc_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

    if not inbox_id or not proc_id:
        logger.error("Chưa cấu hình GOOGLE_DRIVE_FOLDER_ID hoặc GOOGLE_DRIVE_PROCESSED_FOLDER_ID trong .env")
        return

    # Lấy danh sách toàn bộ file trong thư mục Đã Xử Lý
    proc_files = gs.list_images_in_folder(proc_id)
    logger.info(f"Tìm thấy {len(proc_files)} file trong thư mục Đã Xử Lý ({proc_id}).")

    moved_count = 0
    for idx, f in enumerate(proc_files, start=1):
        file_id = f["id"]
        file_name = f["name"]
        try:
            gs.move_file_to_folder(file_id, inbox_id)
            moved_count += 1
            if idx % 20 == 0 or idx == len(proc_files):
                logger.info(f"Đã chuyển {idx}/{len(proc_files)} file về Inbox...")
        except Exception as e:
            logger.error(f"Lỗi khi chuyển file {file_name} ({file_id}): {e}")

    logger.info(f"HOÀN TẤT: Đã chuyển thành công {moved_count}/{len(proc_files)} file về thư mục Inbox.")

def main():
    gs = GoogleSyncService()
    
    # 1. Reset Sheet
    reset_google_sheets(gs)
    
    # 2. Chuyển file về Inbox
    move_files_back_to_inbox(gs)
    
    logger.info("=== HỆ THỐNG ĐÃ ĐƯỢC LÀM SẠCH HOÀN TOÀN VỀ TRẠNG THÁI BAN ĐẦU ===")
    logger.info("Bây giờ bạn có thể kích hoạt Quét Tự Động hoặc nhấn 'Quét Ngay' trên Dashboard.")

if __name__ == "__main__":
    main()
