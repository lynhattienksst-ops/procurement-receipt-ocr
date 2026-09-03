"""
Google Drive & Google Sheets Integration Service for Procurement.
Supports downloading invoice images from Google Drive folders
and reading/writing structured transaction rows into Google Sheets.
"""
import os
import io
import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("google-service")

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    import gspread
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    logger.warning("Google API Client libraries not fully installed.")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]

STANDARD_HEADER_V2_HEADERS = [
    "Mã đối tượng",
    "Ngày, tháng, năm",
    "Tên công ty",
    "Địa chỉ bên bán",
    "Địa chỉ bên nhận",
    "Mã hóa đơn, chứng từ",
    "Tổng tiền hàng (gốc)",
    "Chiết khấu thương mại",
    "Thuế VAT",
    "Tổng Thanh Toán",
    "Người mua/nhận hàng",
    "Link ảnh đối soát",
    "Ghi chú chung",
    "Xác nhận"
]

STANDARD_LINES_V2_HEADERS = [
    "Mã đối tượng",
    "Mã sản phẩm",
    "Tên hàng hóa, dịch vụ",
    "Số lượng",
    "Đơn vị tính",
    "Đơn giá",
    "Chiết khấu mặt hàng",
    "Tỷ lệ chiết khấu (%)",
    "Thuế suất VAT (%)",
    "Tiền thuế VAT",
    "Thành tiền",
    "Ghi chú mặt hàng",
    "Nhóm hàng",
    "Nguồn phân loại"
]

def clean_dt_code(val: Any) -> str:
    """
    Clean and normalize DT Code or Document Code string.
    Strips leading/trailing single quotes ('), double quotes ("), whitespace, and converts to uppercase.
    """
    if val is None:
        return ""
    s = str(val).strip()
    while s.startswith("'") or s.startswith('"'):
        s = s[1:].strip()
    while s.endswith("'") or s.endswith('"'):
        s = s[:-1].strip()
    return s.upper()

class GoogleSyncService:
    def __init__(self, service_account_path: Optional[str] = None):
        self.service_account_path = service_account_path or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
        self.credentials = None
        self.drive_service = None
        self.sheets_service = None
        self.gspread_client = None

        self._init_auth()

    def _init_auth(self):
        if not GOOGLE_LIBS_AVAILABLE:
            return

        candidate_paths = [
            self.service_account_path,
            "service_account.json",
            "service_account.json.json",
            "/app/service_account.json",
            "/app/service_account.json.json"
        ]

        found_path = None
        for p in candidate_paths:
            if p and os.path.exists(p):
                found_path = p
                break

        if found_path:
            try:
                self.credentials = service_account.Credentials.from_service_account_file(
                    found_path, scopes=SCOPES
                )
                self.drive_service = build("drive", "v3", credentials=self.credentials)
                self.sheets_service = build("sheets", "v4", credentials=self.credentials)
                self.gspread_client = gspread.authorize(self.credentials)
                logger.info(f"Google Service Account initialized successfully from: {found_path}")
            except Exception as e:
                logger.error(f"Failed to authenticate with Google Service Account: {e}")
        else:
            logger.info("Google Service Account file not found in candidate paths.")

    def is_connected(self) -> bool:
        if self.credentials is None:
            self._init_auth()
        return self.credentials is not None and self.drive_service is not None and self.sheets_service is not None

    # =========================================================================
    # GOOGLE DRIVE METHODS
    # =========================================================================
    def list_images_in_folder(self, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List image files inside a Google Drive folder.
        """
        from dotenv import load_dotenv
        load_dotenv(override=True)

        if not self.is_connected():
            self._init_auth()
        if not self.drive_service:
            raise RuntimeError("Google Drive service is not connected. Please check service_account.json.")

        target_folder = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        
        # If not configured, auto-discover shared folder
        if not target_folder:
            try:
                folders = self.drive_service.files().list(
                    q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    fields="files(id, name)"
                ).execute().get("files", [])
                if folders:
                    target_folder = folders[0]["id"]
                    logger.info(f"Auto-discovered Drive folder: {folders[0]['name']} ({target_folder})")
            except Exception as e:
                logger.warning(f"Folder auto-discovery failed: {e}")

        if not target_folder:
            raise ValueError("GOOGLE_DRIVE_FOLDER_ID is not configured in .env and no folder was found.")

        query = f"'{target_folder}' in parents and trashed = false and (mimeType contains 'image/' or mimeType = 'application/pdf')"
        try:
            files = []
            page_token = None
            while True:
                results = self.drive_service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, size, createdTime)",
                    pageSize=100,
                    pageToken=page_token
                ).execute()
                files.extend(results.get("files", []))
                page_token = results.get("nextPageToken")
                if not page_token:
                    break
            return self.sort_files_by_priority(files)
        except Exception as e:
            logger.error(f"Error querying Google Drive folder: {e}")
            raise

    @staticmethod
    def sort_files_by_priority(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort files by scan priority:
        1. Single-page Image files (JPEG, JPG, PNG, WEBP...) scanned first (Priority 0).
        2. Multi-page / PDF files (application/pdf) scanned after all images (Priority 1).
        Within each group, sorted by createdTime and name.
        """
        def get_file_priority_key(f: Dict[str, Any]):
            mime = (f.get("mimeType") or "").lower()
            name = (f.get("name") or "").lower()
            created = f.get("createdTime") or ""

            if "image/" in mime or name.endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')):
                prio = 0
            elif mime == "application/pdf" or name.endswith('.pdf'):
                prio = 1
            else:
                prio = 2
            return (prio, created, name)

        return sorted(files, key=get_file_priority_key)


    def download_file_bytes(self, file_id: str, use_cache: bool = True) -> Tuple[bytes, str]:
        """
        Download a file from Google Drive by ID with high-speed local disk caching.
        Returns: (file_bytes, filename)
        """
        if str(file_id).startswith("local_"):
            download_dir = os.getenv("DOWNLOAD_DIR", "downloaded_images")
            for f in os.listdir(download_dir):
                if f.startswith(file_id):
                    with open(os.path.join(download_dir, f), "rb") as fh:
                        return fh.read(), f
            raise FileNotFoundError(f"Local file {file_id} not found")

        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "images")
        os.makedirs(cache_dir, exist_ok=True)
        
        cached_img_path = os.path.join(cache_dir, f"{file_id}.bin")
        cached_meta_path = os.path.join(cache_dir, f"{file_id}.meta")

        if use_cache and os.path.exists(cached_img_path) and os.path.exists(cached_meta_path):
            try:
                with open(cached_meta_path, "r", encoding="utf-8") as f:
                    filename = f.read().strip()
                with open(cached_img_path, "rb") as f:
                    content = f.read()
                if content:
                    return content, filename
            except Exception as e:
                logger.warning(f"Cache read error for {file_id}: {e}")

        if not self.drive_service:
            self._init_auth()
        if not self.drive_service:
            raise RuntimeError("Google Drive service is not connected.")

        try:
            file_meta = self.drive_service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
            filename = file_meta.get("name", f"{file_id}.jpg")

            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

            content = fh.getvalue()

            # Save to disk cache
            try:
                with open(cached_img_path, "wb") as f:
                    f.write(content)
                with open(cached_meta_path, "w", encoding="utf-8") as f:
                    f.write(filename)
            except Exception as cache_err:
                logger.warning(f"Failed to write image cache for {file_id}: {cache_err}")

            return content, filename
        except Exception as e:
            logger.error(f"Error downloading file {file_id} from Drive: {e}")
            raise

    def move_file_to_folder(self, file_id: str, new_folder_id: str) -> bool:
        """
        Move a file to a new folder by adding the new folder to its parents
        and removing the old parents.
        """
        if not self.drive_service:
            logger.error("Drive service not connected.")
            return False
            
        try:
            # Retrieve the existing parents to remove
            file = self.drive_service.files().get(fileId=file_id, fields='parents').execute()
            previous_parents = ",".join(file.get('parents', []))
            
            # Move the file to the new folder
            file = self.drive_service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=previous_parents,
                fields='id, parents'
            ).execute()
            logger.info(f"Successfully moved file {file_id} to folder {new_folder_id}")
            return True
        except Exception as e:
            logger.error(f"Error moving file {file_id} to {new_folder_id}: {e}")
            return False

    def upload_file_to_drive(self, file_bytes: bytes, filename: str, mime_type: str, folder_id: str) -> Optional[str]:
        """
        Uploads a file directly to a Google Drive folder.
        Returns the new file ID if successful, else None.
        """
        if not self.drive_service:
            logger.error("Drive service not connected.")
            return None
        
        try:
            file_metadata = {
                'name': filename,
                'parents': [folder_id]
            }
            media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            
            logger.info(f"Successfully uploaded file {filename} to folder {folder_id} with ID {file.get('id')}")
            return file.get('id')
        except Exception as e:
            logger.error(f"Error uploading file {filename} to folder {folder_id}: {e}")
            return None

    # =========================================================================
    # GOOGLE SHEETS METHODS
    # =========================================================================
    def get_sheet_data(self, spreadsheet_id: Optional[str] = None, sheet_name: Optional[str] = None) -> List[List[Any]]:
        """
        Fetch all rows from the specified Google Sheet.
        """
        from dotenv import load_dotenv
        load_dotenv(override=True)

        if not self.is_connected():
            self._init_auth()
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        
        # If not set, auto-discover spreadsheet
        if not target_sheet_id and self.drive_service:
            try:
                sheets = self.drive_service.files().list(
                    q="mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false",
                    fields="files(id, name)"
                ).execute().get("files", [])
                if sheets:
                    target_sheet_id = sheets[0]["id"]
                    logger.info(f"Auto-discovered Google Sheet: {sheets[0]['name']} ({target_sheet_id})")
            except Exception as e:
                logger.warning(f"Sheet auto-discovery failed: {e}")

        target_tab = sheet_name or os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")

        if not target_sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is not configured in .env and no sheet was found.")

        try:
            range_name = f"{target_tab}!A:N"
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=target_sheet_id,
                range=range_name
            ).execute()
            return result.get("values", [])
        except Exception as e:
            logger.error(f"Error reading Google Sheet {target_sheet_id}: {e}")
            raise

    def ensure_tab_exists(self, tab_name: str, spreadsheet_id: Optional[str] = None) -> bool:
        """
        Check if a sheet tab exists, if not, create it.
        """
        if not self.sheets_service:
            return False
        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        try:
            sheet_metadata = self.sheets_service.spreadsheets().get(spreadsheetId=target_sheet_id).execute()
            sheets = sheet_metadata.get('sheets', [])
            existing_tabs = [s['properties']['title'] for s in sheets]
            if tab_name not in existing_tabs:
                body = {
                    'requests': [{
                        'addSheet': {
                            'properties': {
                                'title': tab_name
                            }
                        }
                    }]
                }
                self.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=target_sheet_id,
                    body=body
                ).execute()
                logger.info(f"Created new Google Sheet tab: {tab_name}")
            return True
        except Exception as e:
            logger.error(f"Error ensuring tab {tab_name} exists: {e}")
            return False

    def get_max_indexes_by_category(self, spreadsheet_id: Optional[str] = None, sheet_name: Optional[str] = None) -> Dict[int, int]:
        """
        Scan existing sheet rows in Data_Header_V2 to find the maximum index for each category (DT1, DT2, DT3, DT4).
        """
        max_idx = {1: 0, 2: 0, 3: 0, 4: 0}
        target_header_tab = sheet_name or os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")

        try:
            rows = self.get_sheet_data(spreadsheet_id, target_header_tab)
            for r in rows:
                if not r or len(r) == 0:
                    continue
                code_str = str(r[0]).strip().upper()
                for cat in [1, 2, 3, 4]:
                    prefix = f"DT{cat}"
                    if code_str.startswith(prefix):
                        num_part = code_str[len(prefix):]
                        try:
                            val = int(num_part)
                            if val > max_idx[cat]:
                                max_idx[cat] = val
                        except ValueError:
                            pass
        except Exception as e:
            logger.warning(f"Could not scan tab '{target_header_tab}' for max indexes: {e}")

        return max_idx

    def append_relational_v2(
        self,
        header_rows: List[List[Any]],
        line_rows: List[List[Any]],
        spreadsheet_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Append relational V2 records into Data_Header_V2 and Data_Lines_V2.
        Automatically inserts headers if sheets are newly created.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

        header_headers = STANDARD_HEADER_V2_HEADERS
        lines_headers = STANDARD_LINES_V2_HEADERS

        res_summary = {"headers_added": 0, "lines_added": 0}

        try:
            # 1. Process Header Rows
            if header_rows:
                self.ensure_tab_exists(header_tab, target_sheet_id)
                existing_h = self.get_sheet_data(target_sheet_id, header_tab)
                h_to_append = []
                if not existing_h or len(existing_h) == 0:
                    h_to_append.append(header_headers)
                h_to_append.extend(header_rows)

                body_h = {"values": h_to_append}
                self.sheets_service.spreadsheets().values().append(
                    spreadsheetId=target_sheet_id,
                    range=f"{header_tab}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body=body_h
                ).execute()
                res_summary["headers_added"] = len(header_rows)

            # 2. Process Line Rows
            if line_rows:
                self.ensure_tab_exists(lines_tab, target_sheet_id)
                existing_l = self.get_sheet_data(target_sheet_id, lines_tab)
                l_to_append = []
                if not existing_l or len(existing_l) == 0:
                    l_to_append.append(lines_headers)
                l_to_append.extend(line_rows)

                body_l = {"values": l_to_append}
                self.sheets_service.spreadsheets().values().append(
                    spreadsheetId=target_sheet_id,
                    range=f"{lines_tab}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body=body_l
                ).execute()
                res_summary["lines_added"] = len(line_rows)

            return {"success": True, "details": res_summary}
        except Exception as e:
            logger.error(f"Error appending relational V2 records to Google Sheet: {e}")
            raise

    def overwrite_relational_v2(
        self,
        header_rows: List[List[Any]],
        line_rows: List[List[Any]],
        spreadsheet_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Clear and rewrite Data_Header_V2 and Data_Lines_V2 with clean, exact unrounded data.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

        header_headers = STANDARD_HEADER_V2_HEADERS
        lines_headers = STANDARD_LINES_V2_HEADERS

        try:
            self.ensure_tab_exists(header_tab, target_sheet_id)
            self.ensure_tab_exists(lines_tab, target_sheet_id)

            # Clear existing data
            self.sheets_service.spreadsheets().values().clear(
                spreadsheetId=target_sheet_id,
                range=f"{header_tab}!A1:Z10000"
            ).execute()

            self.sheets_service.spreadsheets().values().clear(
                spreadsheetId=target_sheet_id,
                range=f"{lines_tab}!A1:Z10000"
            ).execute()

            # Write Headers
            all_h = [header_headers] + header_rows
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=target_sheet_id,
                range=f"{header_tab}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": all_h}
            ).execute()

            # Write Lines
            all_l = [lines_headers] + line_rows
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=target_sheet_id,
                range=f"{lines_tab}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": all_l}
            ).execute()

            logger.info(f"Overwrote {len(header_rows)} headers and {len(line_rows)} lines into V2 sheets.")
            return {"success": True, "headers_written": len(header_rows), "lines_written": len(line_rows)}
        except Exception as e:
            logger.error(f"Error overwriting relational V2 records: {e}")
            raise

    def overwrite_sheet_data(
        self,
        clean_rows: List[List[Any]],
        spreadsheet_id: Optional[str] = None,
        sheet_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Clear and rewrite sheet tab with clean rows.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")
        target_tab = sheet_name or header_tab

        # Chuẩn bị tập dữ liệu ghi xuống, tự động chèn tiêu đề nếu thiếu
        final_rows = list(clean_rows) if clean_rows is not None else []

        if final_rows:
            # Lấy dòng đầu tiên kiểm tra
            first_row = [str(x).strip() for x in final_rows[0] if x is not None]
            has_valid_header = False
            if first_row and len(first_row) > 0:
                if first_row[0] in ["Mã đối tượng", "dt_code", "dt code", "mã đt"]:
                    has_valid_header = True

            if not has_valid_header:
                if target_tab == header_tab:
                    final_rows.insert(0, STANDARD_HEADER_V2_HEADERS)
                elif target_tab == lines_tab:
                    final_rows.insert(0, STANDARD_LINES_V2_HEADERS)
        else:
            if target_tab == header_tab:
                final_rows = [STANDARD_HEADER_V2_HEADERS]
            elif target_tab == lines_tab:
                final_rows = [STANDARD_LINES_V2_HEADERS]

        try:
            self.sheets_service.spreadsheets().values().clear(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!A1:Z10000"
            ).execute()

            if final_rows:
                body = {"values": final_rows}
                result = self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=target_sheet_id,
                    range=f"{target_tab}!A1",
                    valueInputOption="USER_ENTERED",
                    body=body
                ).execute()
                return {"success": True, "written_rows": len(final_rows)}
            return {"success": True, "written_rows": 0}
        except Exception as e:
            logger.error(f"Error overwriting sheet {target_tab}: {e}")
            return {"success": False, "error": str(e)}

    def confirm_receipt_in_links(self, dt_code: str, status: str = "x") -> Dict[str, Any]:
        """
        Mark 'x' (or clear) in Column N (Xác nhận kế toán) in Data_Header_V2 corresponding to dt_code.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")
        target_tab = "Data_Header_V2"

        try:
            all_rows = self.get_sheet_data(target_sheet_id, target_tab)
            if not all_rows or len(all_rows) <= 1:
                return {"success": False, "error": f"{target_tab} rỗng — không thể xác nhận hóa đơn {dt_code}"}

            target_row_idx = None
            target_dt_clean = clean_dt_code(dt_code)
            for i, row in enumerate(all_rows):
                if row and len(row) > 0 and clean_dt_code(row[0]) == target_dt_clean:
                    target_row_idx = i + 1  # 1-indexed for Sheets
                    break

            if target_row_idx is None:
                return {"success": False, "error": f"Không tìm thấy mã {dt_code} trong {target_tab}"}

            val_to_set = "x" if status.lower() == "x" else ""
            target_col = "N"  # Data_Header_V2 cột Xác nhận

            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!{target_col}{target_row_idx}",
                valueInputOption="USER_ENTERED",
                body={"values": [[val_to_set]]}
            ).execute()

            action_desc = "Đã duyệt đạt (đánh dấu 'x')" if val_to_set == "x" else "Đã bỏ duyệt (xóa dấu 'x')"
            return {"success": True, "status": val_to_set, "message": f"{action_desc} cho hóa đơn {dt_code} trong {target_tab}!"}
        except Exception as e:
            logger.error(f"Error confirming receipt in {target_tab}: {e}")
            return {"success": False, "error": str(e)}

    # Alias for backwards compatibility
    confirm_receipt_in_sheet = confirm_receipt_in_links

    # =========================================================================
    # NHÀ MÁY XỬ LÝ HÓA ĐƠN ĐA DÂY CHUYỀN (FACTORY PIPELINES DT1 - DT4)
    # =========================================================================

    def process_dt1_pipeline(self, dt_code: str, row: List[Any], drive_link: Optional[str] = None) -> Dict[str, Any]:
        """
        Dây chuyền DT1: Sàn TMĐT & Dịch vụ Vận chuyển.
        Chuẩn hóa 1 dòng Header và 1 dòng Lines tóm tắt, bảo toàn COD, mã đơn và link Drive.
        """
        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        target_dt_clean = clean_dt_code(dt_code)

        # Bảo tồn link Drive và trạng thái xác nhận 'x'
        all_headers = self.get_sheet_data(target_sheet_id, header_tab) or []
        existing_link = drive_link or ""
        existing_confirmed = ""
        for r in all_headers:
            if r and len(r) > 0 and clean_dt_code(r[0]) == target_dt_clean:
                if not existing_link:
                    existing_link = r[11] if len(r) > 11 else ""
                existing_confirmed = r[13] if len(r) > 13 else ""
                break

        upd = list(row)
        while len(upd) < 14:
            upd.append("")

        dt_code_val = target_dt_clean
        from services.business_rules import normalize_datetime_vn
        date_val = normalize_datetime_vn(str(upd[1]).strip(), prefix_quote=True)
        company_val = str(upd[2]).strip()
        seller_addr = str(upd[3]).strip()
        buyer_addr = str(upd[4]).strip()
        order_id = str(upd[5]).strip()
        if order_id.startswith('0') and len(order_id) > 1:
            order_id = "'" + order_id
        description = str(upd[6]).strip()
        quantity = str(upd[7]).strip()
        unit_price = str(upd[8]).strip()
        vat_rate = str(upd[9]).strip()
        vat_amount = str(upd[10]).strip()
        total_amount = str(upd[11]).strip()
        buyer_name = str(upd[12]).strip()
        notes = str(upd[13]).strip()

        from services.business_rules import parse_vietnamese_number
        qty_num = parse_vietnamese_number(quantity) or 1
        price_num = parse_vietnamese_number(unit_price)
        vat_num = parse_vietnamese_number(vat_amount)
        tot_num = parse_vietnamese_number(total_amount)
        raw_total = qty_num * price_num if qty_num > 0 and price_num > 0 else (tot_num - vat_num)

        header_row = [
            dt_code_val,
            date_val,
            company_val,
            seller_addr,
            buyer_addr,
            order_id,
            raw_total,
            0,  # Chiết khấu
            vat_num,
            tot_num,
            buyer_name,
            existing_link,
            notes,
            existing_confirmed
        ]

        # DT1 Line Item luôn đồng bộ chính xác 1 dòng tóm tắt
        line_rows = [[
            dt_code_val,
            "",
            description or "Đơn hàng TMĐT",
            qty_num,
            "Đơn",
            price_num,
            0,
            0,
            vat_rate or "0%",
            vat_num,
            tot_num,
            notes
        ]]

        return self.update_relational_record(target_dt_clean, header_row, line_rows)

    def process_dt2_pipeline(self, dt_code: str, header_row: List[Any], line_rows: List[List[Any]]) -> Dict[str, Any]:
        """
        Dây chuyền DT2: Siêu Thị & Bán Lẻ Chung Quy.
        Kiểm soát thuế VAT, bảo toàn đa dòng mặt hàng.
        """
        return self.update_relational_record(dt_code, header_row, line_rows)

    def process_dt3_pipeline(self, dt_code: str, header_row: List[Any], line_rows: List[List[Any]]) -> Dict[str, Any]:
        """
        Dây chuyền DT3: Cung Ứng Thực Phẩm & Nông Sản.
        Tự động kích hoạt bộ lọc loại bỏ hoàn toàn các mặt hàng có thẻ '(loại bỏ)'.
        """
        filtered_lines = []
        for lr in line_rows:
            item_name = str(lr[2] if len(lr) > 2 else "").lower()
            if "(loại bỏ)" in item_name or "(loai bo)" in item_name:
                logger.info(f"DT3 Pipeline: Filtered out item '{item_name}' from {dt_code}")
                continue
            filtered_lines.append(lr)

        return self.update_relational_record(dt_code, header_row, filtered_lines)

    def process_dt4_pipeline(self, dt_code: str, header_row: List[Any], line_rows: List[List[Any]]) -> Dict[str, Any]:
        """
        Dây chuyền DT4: Hóa Đơn Viết Tay & Tự Do.
        Linh hoạt theo cơ chế 'điền nếu có', đánh dấu hóa đơn viết tay.
        """
        return self.update_relational_record(dt_code, header_row, line_rows)

    def dispatch_pipeline(self, dt_code: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Trạm Điều Phối Nhà Máy (Factory Dispatcher Router).
        Phân luồng chính xác theo tiền tố DT1 - DT4.
        """
        dt_prefix = str(dt_code or "").strip().upper()[:3]
        header_row = payload.get("header_row")
        line_rows = payload.get("line_rows")
        row = payload.get("row")
        drive_link = payload.get("drive_link")

        if dt_prefix == "DT1" and row is not None:
            return self.process_dt1_pipeline(dt_code, row, drive_link)
        elif dt_prefix == "DT3" and header_row is not None and line_rows is not None:
            return self.process_dt3_pipeline(dt_code, header_row, line_rows)
        elif dt_prefix == "DT4" and header_row is not None and line_rows is not None:
            return self.process_dt4_pipeline(dt_code, header_row, line_rows)
        elif header_row is not None and line_rows is not None:
            return self.process_dt2_pipeline(dt_code, header_row, line_rows)
        elif row is not None:
            return self.process_dt1_pipeline(dt_code, row, drive_link)
        else:
            raise ValueError(f"Payload không hợp lệ cho mã {dt_code}.")

    def update_sheet_row(self, dt_code: str, updated_row: List[Any]) -> Dict[str, Any]:
        """
        Tương thích ngược: Điều phối qua Nhà Máy Dây Chuyền DT1.
        """
        return self.process_dt1_pipeline(dt_code, updated_row)

    def delete_sheet_record(self, dt_code: str, spreadsheet_id: Optional[str] = None, delete_drive_file: bool = True) -> Dict[str, Any]:
        """
        Permanently delete a record and all its associated rows from Data_Header_V2 and
        Data_Lines_V2, and optionally its image file(s) from Google Drive.
        """
        if not self.sheets_service:
            self._init_auth()
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        dt_target = clean_dt_code(dt_code)
        if not dt_target:
            return {"success": False, "error": "Thiếu mã đối tượng dt_code"}

        try:
            file_ids_to_delete = []

            # 1. Delete from Data_Header_V2
            header_rows = self.get_sheet_data(target_sheet_id, "Data_Header_V2")
            deleted_h = 0
            if header_rows and len(header_rows) > 0:
                new_headers = []
                for idx, r in enumerate(header_rows):
                    if not r:
                        continue
                    row_dt = clean_dt_code(r[0]) if len(r) > 0 else ""
                    if idx > 0 and row_dt == dt_target:
                        deleted_h += 1
                        if len(r) > 11:
                            link = str(r[11]).strip()
                            if link:
                                match = re.search(r'd/([a-zA-Z0-9_-]+)|id=([a-zA-Z0-9_-]+)', link)
                                if match:
                                    file_id = match.group(1) or match.group(2)
                                    if file_id and not file_id.startswith('local_'):
                                        file_ids_to_delete.append(file_id)
                    else:
                        new_headers.append(r)
                if deleted_h > 0:
                    self.overwrite_sheet_data(new_headers, spreadsheet_id=target_sheet_id, sheet_name="Data_Header_V2")

            # 2. Delete from Data_Lines_V2
            lines_rows = self.get_sheet_data(target_sheet_id, "Data_Lines_V2")
            deleted_l = 0
            if lines_rows and len(lines_rows) > 0:
                new_lines = []
                for idx, r in enumerate(lines_rows):
                    if not r:
                        continue
                    row_dt = clean_dt_code(r[0]) if len(r) > 0 else ""
                    if idx > 0 and row_dt == dt_target:
                        deleted_l += 1
                    else:
                        new_lines.append(r)
                if deleted_l > 0:
                    self.overwrite_sheet_data(new_lines, spreadsheet_id=target_sheet_id, sheet_name="Data_Lines_V2")


            # 5. Delete associated image files from Google Drive
            deleted_drive_files = 0
            if delete_drive_file:
                processed_folder_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")
                inbox_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
                
                for fid in set(file_ids_to_delete):
                    try:
                        self.drive_service.files().delete(fileId=fid).execute()
                        deleted_drive_files += 1
                        logger.info(f"Deleted file {fid} from Drive.")
                    except Exception as e:
                        logger.warning(f"Permanent delete failed for {fid}, detaching from folders: {e}")
                        try:
                            remove_parents = []
                            if processed_folder_id: remove_parents.append(processed_folder_id)
                            if inbox_folder_id: remove_parents.append(inbox_folder_id)
                            if remove_parents:
                                self.drive_service.files().update(
                                    fileId=fid,
                                    removeParents=",".join(remove_parents)
                                ).execute()
                                deleted_drive_files += 1
                        except Exception as ex2:
                            logger.error(f"Failed to detach file {fid}: {ex2}")

            return {
                "success": True,
                "deleted_header_rows": deleted_h,
                "deleted_lines_rows": deleted_l,
                "deleted_drive_files": deleted_drive_files,
                "message": f"Đã xóa thành công hóa đơn {dt_target} khỏi hệ thống (Data_Header_V2 & Data_Lines_V2) và xóa {deleted_drive_files} ảnh khỏi Google Drive!"
            }
        except Exception as e:
            logger.error(f"Error deleting sheet record {dt_target}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_historical_records(self) -> List[Dict[str, Any]]:
        """
        Fetch all records from Data_Header_V2 and Data_Lines_V2, grouped by DT_CODE.
        Image links are resolved from Data_Header_V2 column L (index 11), with a
        whole-row URL scan as a safety net for legacy 13-column rows.
        """
        try:
            main_sheet_id = os.getenv("GOOGLE_SHEET_ID")
            header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
            lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

            header_rows = self.get_sheet_data(main_sheet_id, header_tab) or []
            lines_rows = self.get_sheet_data(main_sheet_id, lines_tab) or []

            # Legacy Links_Hoa_Don fallback removed: every Data_Header_V2 row now
            # carries its own image link in column L.
            links_dict: Dict[str, Dict[str, Any]] = {}

            # If V2 is populated, build relational records
            if header_rows and len(header_rows) > 1:
                # Group lines by dt_code
                lines_by_dt: Dict[str, List[Dict[str, Any]]] = {}
                for r in lines_rows[1:]:
                    if not r or len(r) == 0:
                        continue
                    dt_code = clean_dt_code(r[0])
                    if not dt_code.startswith("DT"):
                        continue
                    if dt_code not in lines_by_dt:
                        lines_by_dt[dt_code] = []
                    lines_by_dt[dt_code].append({
                        "product_code": str(r[1]) if len(r) > 1 else "",
                        "item_name": str(r[2]) if len(r) > 2 else "",
                        "quantity": str(r[3]) if len(r) > 3 else "1",
                        "measurement_unit": str(r[4]) if len(r) > 4 else "",
                        "price": str(r[5]) if len(r) > 5 else "0",
                        "discount": str(r[6]) if len(r) > 6 else "0",
                        "discount_rate": str(r[7]) if len(r) > 7 else "0%",
                        "vat_rate": str(r[8]) if len(r) > 8 else "0%",
                        "vat_amount": str(r[9]) if len(r) > 9 else "0",
                        "row_total": str(r[10]) if len(r) > 10 else "0",
                        "notes": str(r[11]) if len(r) > 11 else ""
                    })

                records = []
                for r in header_rows[1:]:
                    if not r or len(r) == 0:
                        continue
                    dt_code = clean_dt_code(r[0])
                    if not dt_code.startswith("DT"):
                        continue

                    # Trích xuất link Drive đa tầng an toàn (14 cột, 13 cột, quét toàn dòng và fallback links_dict)
                    link_url = ""
                    if len(r) > 11 and ("drive.google.com" in str(r[11]) or str(r[11]).startswith("http")):
                        link_url = str(r[11]).strip()
                    elif len(r) > 10 and ("drive.google.com" in str(r[10]) or str(r[10]).startswith("http")):
                        link_url = str(r[10]).strip()
                    else:
                        for cell in r:
                            c_str = str(cell).strip()
                            if "drive.google.com" in c_str or c_str.startswith("http"):
                                link_url = c_str
                                break

                    fallback_info = links_dict.get(dt_code, {})
                    if not link_url:
                        link_url = fallback_info.get("link", "")

                    is_confirmed = (len(r) > 13 and str(r[13]).strip().lower() == "x") or fallback_info.get("confirmed", False)
                    items = lines_by_dt.get(dt_code, [])
                    header_notes = str(r[12]) if len(r) > 12 else (str(r[11]) if len(r) > 11 and not str(r[11]).startswith("http") else "")

                    # === Inject phí vận chuyển từ notes nếu chưa có trong line items ===
                    if header_notes:
                        shipping_keywords = ["phí vận chuyển", "phí ship", "cước vận chuyển",
                                             "shipping fee", "freight", "delivery fee",
                                             "phí giao hàng", "chi phí vận chuyển"]
                        already_has_shipping = any(
                            any(kw in str(it.get("item_name") or "").lower() for kw in shipping_keywords)
                            for it in items
                        )
                        if not already_has_shipping:
                            import re as _re
                            ship_match = _re.search(
                                r"(?:ph[ií]\s*(?:v[aậ]n\s*chuy[eể]n|ship|giao\s*h[àa]ng)"
                                r"|c[uướ][oở]c\s*v[aậ]n\s*chuy[eể]n"
                                r"|chi\s*ph[ií]\s*v[aậ]n\s*chuy[eể]n"
                                r"|shipping\s*fee|freight|delivery\s*fee)"
                                r"[^\d]*(\d[\d\.,]*)(?:\s*(?:vnd|vn[đd]|đ|d))?",
                                header_notes,
                                _re.IGNORECASE
                            )
                            if ship_match:
                                raw_num = ship_match.group(1).replace(".", "").replace(",", ".").strip()
                                try:
                                    ship_fee = float(raw_num)
                                    if ship_fee > 0:
                                        items = list(items) + [{
                                             "product_code": "",
                                             "item_name": "Phí vận chuyển",
                                             "quantity": "1",
                                             "measurement_unit": "Lần",
                                             "price": str(int(ship_fee) if ship_fee.is_integer() else ship_fee),
                                             "discount": "0",
                                             "discount_rate": "0",
                                             "vat_rate": "0",
                                             "vat_amount": "0",
                                             "row_total": str(int(ship_fee) if ship_fee.is_integer() else ship_fee),
                                             "notes": ""
                                        }]
                                except (ValueError, TypeError):
                                    pass

                    records.append({
                        "dt_code": dt_code,
                        "datetime": r[1] if len(r) > 1 else "",
                        "merchant": r[2] if len(r) > 2 else "",
                        "address_seller": r[3] if len(r) > 3 else "",
                        "address_buyer": r[4] if len(r) > 4 else "",
                        "order_id": r[5] if len(r) > 5 else "",
                        "total_raw_amount": r[6] if len(r) > 6 else "0",
                        "total_discount_amount": r[7] if len(r) > 7 else "0",
                        "total_vat_amount": r[8] if len(r) > 8 else "0",
                        "total_amount": r[9] if len(r) > 9 else "0",
                        "customer": r[10] if len(r) > 10 else "",
                        "drive_link": link_url,
                        "notes": header_notes,
                        "file_info": {
                            "link": link_url,
                            "confirmed": is_confirmed
                        },
                        "items": items,
                        "confirmed": is_confirmed
                    })
                return records

            return []
        except Exception as e:
            logger.error(f"Error fetching historical records: {e}")
            return []

    def update_relational_record(self, dt_code: str, header_row: List[Any], line_rows: List[List[Any]]) -> Dict[str, Any]:
        """
        Update a record in Data_Header_V2 and replace/update its lines in Data_Lines_V2.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        lines_tab = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")

        dt_target = clean_dt_code(dt_code)

        from services.business_rules import parse_vietnamese_number

        def _to_clean_num(val):
            if val is None or val == "":
                return 0
            try:
                s = str(val).replace("%", "").strip()
                num = parse_vietnamese_number(s)
                return int(num) if num.is_integer() else round(num, 2)
            except Exception:
                return val

        # Clean Header Row columns B, G, H, I, J
        from services.business_rules import normalize_datetime_vn
        cleaned_header = list(header_row)
        while len(cleaned_header) < 14:
            cleaned_header.append("")
        cleaned_header[0] = dt_target  # Chốt chặn tuyệt đối: Khóa chính Header bắt buộc là dt_target
        if cleaned_header[1]:
            cleaned_header[1] = normalize_datetime_vn(cleaned_header[1], prefix_quote=True)
        cleaned_header[6] = _to_clean_num(cleaned_header[6])
        cleaned_header[7] = _to_clean_num(cleaned_header[7])
        cleaned_header[8] = _to_clean_num(cleaned_header[8])
        cleaned_header[9] = _to_clean_num(cleaned_header[9])
        header_row = cleaned_header

        # Clean Line Rows columns D, E, F, G, H, I, J, K (UoM at Index 4 is kept as string)
        from services.business_rules import classify_line_item
        is_dt1 = dt_target.startswith("DT1")
        cleaned_line_rows = []
        for r in line_rows:
            r_copy = list(r)
            while len(r_copy) < 14:
                r_copy.append("")
            r_copy[0] = dt_target  # Chốt chặn tuyệt đối: Khóa ngoại Lines bắt buộc là dt_target
            r_copy[3] = _to_clean_num(r_copy[3])  # Số lượng
            r_copy[4] = str(r_copy[4] or "").strip() # Đơn vị tính
            r_copy[5] = _to_clean_num(r_copy[5])  # Đơn giá
            r_copy[6] = _to_clean_num(r_copy[6])  # Chiết khấu mặt hàng
            r_copy[7] = _to_clean_num(r_copy[7])  # Tỷ lệ CK (%)
            r_copy[8] = _to_clean_num(r_copy[8])  # Thuế suất VAT (%)
            r_copy[9] = _to_clean_num(r_copy[9])  # Tiền thuế VAT
            r_copy[10] = _to_clean_num(r_copy[10]) # Thành tiền
            # Cột M/N: Nhóm hàng + nguồn. Tôn trọng nhóm người dùng sửa tay (source == 'manual').
            existing_src = str(r_copy[13] or "").strip().lower()
            if str(r_copy[12] or "").strip() and existing_src == "manual":
                pass  # giữ nguyên giá trị sửa tay
            else:
                g_code, g_src = classify_line_item(r_copy[2], r_copy[4], is_dt1=is_dt1)
                r_copy[12] = g_code
                r_copy[13] = g_src
            cleaned_line_rows.append(r_copy)
        line_rows = cleaned_line_rows

        try:
            # 1. Update Header Row (Targeted range update A{sheet_row}:N{sheet_row})
            all_headers = self.get_sheet_data(target_sheet_id, header_tab) or []
            header_updated = False
            for idx, r in enumerate(all_headers):
                if r and len(r) > 0 and clean_dt_code(r[0]) == dt_target:
                    sheet_row = idx + 1
                    # Preserve Link Drive (Column L / Index 11) if not passed in header_row
                    if not str(header_row[11]).strip() and len(r) > 11 and str(r[11]).strip():
                        header_row[11] = r[11]
                    # Preserve Confirmed status (Column N / Index 13) if not passed in header_row
                    if not str(header_row[13]).strip() and len(r) > 13 and str(r[13]).strip():
                        header_row[13] = r[13]

                    self.sheets_service.spreadsheets().values().update(
                        spreadsheetId=target_sheet_id,
                        range=f"{header_tab}!A{sheet_row}:N{sheet_row}",
                        valueInputOption="USER_ENTERED",
                        body={"values": [header_row]}
                    ).execute()
                    header_updated = True
                    break

            if not header_updated:
                # Append missing header row
                self.sheets_service.spreadsheets().values().append(
                    spreadsheetId=target_sheet_id,
                    range=f"{header_tab}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [header_row]}
                ).execute()

            # 2. Update Lines Rows: Deterministic Partitioning (L_other + L_new)
            all_lines = self.get_sheet_data(target_sheet_id, lines_tab) or []
            if not all_lines:
                header_line = STANDARD_LINES_V2_HEADERS
                other_lines = []
            else:
                header_line = all_lines[0]
                other_lines = [r for r in all_lines[1:] if r and len(r) > 0 and clean_dt_code(r[0]) != dt_target]

            # Rebuild clean contiguous table: Header + All other invoices' lines + New lines of this target
            new_lines = [header_line] + other_lines + line_rows

            # Write updated values directly
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=target_sheet_id,
                range=f"{lines_tab}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": new_lines}
            ).execute()

            # If the new table is smaller than old table, clear only the remaining bottom rows
            if len(new_lines) < len(all_lines):
                clear_start = len(new_lines) + 1
                clear_end = len(all_lines) + 20
                self.sheets_service.spreadsheets().values().clear(
                    spreadsheetId=target_sheet_id,
                    range=f"{lines_tab}!A{clear_start}:N{clear_end}"
                ).execute()

            logger.info(f"Updated relational record {dt_target}: Header updated safely, {len(line_rows)} lines stored (Total lines: {len(new_lines)}).")
            return {
                "success": True, 
                "dt_code": dt_target, 
                "lines_count": len(line_rows),
                "header_row": header_row,
                "line_rows": line_rows
            }
        except Exception as e:
            logger.error(f"Error updating relational record {dt_target}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
            raise

    def get_audit_reconciliation(self) -> Dict[str, Any]:
        """
        Reconciliation between all Google Drive files and Google Sheet rows.
        """
        inbox_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        proc_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

        inbox_files = self.list_images_in_folder(inbox_id) if inbox_id else []
        proc_files = self.list_images_in_folder(proc_id) if proc_id else []
        header_tab = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
        sheet_rows = self.get_sheet_data(sheet_name=header_tab)

        # Drive file <-> invoice mapping comes from the Data_Header_V2 image link
        # column (L, index 11); scan the whole row as a safety net for legacy widths.
        linked_file_ids = set()
        for r in (sheet_rows[1:] if sheet_rows else []):
            for cell in r:
                m = re.search(r"/file/d/([a-zA-Z0-9_-]+)|[?&]id=([a-zA-Z0-9_-]+)", str(cell))
                if m:
                    linked_file_ids.add(m.group(1) or m.group(2))
                    break

        all_drive_files = inbox_files + proc_files
        mapped_count = len(linked_file_ids)
        duplicate_or_skipped = [f for f in all_drive_files if f["id"] not in linked_file_ids]

        return {
            "total_drive_files": len(all_drive_files),
            "inbox_files_count": len(inbox_files),
            "processed_files_count": len(proc_files),
            "sheet_unique_records_count": len(sheet_rows) - 1 if sheet_rows else 0,
            "mapped_files_count": mapped_count,
            "skipped_duplicates_count": len(duplicate_or_skipped),
            "skipped_files": duplicate_or_skipped[:50]
        }

