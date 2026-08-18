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
    from googleapiclient.http import MediaIoBaseDownload
    import gspread
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    logger.warning("Google API Client libraries not fully installed.")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]

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
            return files
        except Exception as e:
            logger.error(f"Error querying Google Drive folder: {e}")
            raise

    def download_file_bytes(self, file_id: str, use_cache: bool = True) -> Tuple[bytes, str]:
        """
        Download a file from Google Drive by ID with high-speed local disk caching.
        Returns: (file_bytes, filename)
        """
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

        target_tab = sheet_name or os.getenv("GOOGLE_SHEET_NAME", "Trang tính1")

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

    def get_max_indexes_by_category(self, spreadsheet_id: Optional[str] = None, sheet_name: Optional[str] = None) -> Dict[int, int]:
        """
        Scan existing sheet rows to find the maximum index for each category (DT1, DT2, DT3, DT4).
        Returns a dict: {1: max_idx, 2: max_idx, 3: max_idx, 4: max_idx}
        """
        max_idx = {1: 0, 2: 0, 3: 0, 4: 0}
        try:
            rows = self.get_sheet_data(spreadsheet_id, sheet_name)
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
            logger.warning(f"Could not scan sheet for max indexes, starting from 0: {e}")

        return max_idx

    def append_rows_to_sheet(
        self,
        rows: List[List[Any]],
        spreadsheet_id: Optional[str] = None,
        sheet_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Append processed transaction rows to the Google Sheet.
        If sheet is empty, automatically inserts the 13-column header row.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        target_tab = sheet_name or os.getenv("GOOGLE_SHEET_NAME", "Sheet1")

        headers = [
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

        try:
            # Check if sheet has headers
            existing = self.get_sheet_data(target_sheet_id, target_tab)
            values_to_append = []
            if not existing or len(existing) == 0:
                values_to_append.append(headers)

            values_to_append.extend(rows)

            body = {"values": values_to_append}
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()

            return {
                "success": True,
                "updated_rows": result.get("updates", {}).get("updatedRows", len(values_to_append)),
                "updated_range": result.get("updates", {}).get("updatedRange", "")
            }
        except Exception as e:
            logger.error(f"Error appending rows to Google Sheet: {e}")
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
        target_tab = sheet_name or os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")

        try:
            self.sheets_service.spreadsheets().values().clear(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!A1:Z1000"
            ).execute()

            if clean_rows:
                body = {"values": clean_rows}
                result = self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=target_sheet_id,
                    range=f"{target_tab}!A1",
                    valueInputOption="USER_ENTERED",
                    body=body
                ).execute()
                return {"success": True, "written_rows": len(clean_rows)}
            return {"success": True, "written_rows": 0}
        except Exception as e:
            logger.error(f"Error overwriting sheet {target_tab}: {e}")
            return {"success": False, "error": str(e)}

    def deduplicate_all_sheets(self) -> Dict[str, Any]:
        """
        Scan and purge duplicate rows from both Bang_Ke_Hoa_Don and Links_Hoa_Don.
        """
        main_tab = os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")
        links_tab = os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don")

        main_rows = self.get_sheet_data(sheet_name=main_tab)
        links_rows = self.get_sheet_data(sheet_name=links_tab)

        # Deduplicate main sheet
        seen_dt = set()
        seen_orders = set()
        clean_main = []
        removed_main_count = 0

        for idx, r in enumerate(main_rows):
            if not r:
                continue
            if idx == 0 and str(r[0]).strip().lower() in ["mã đối tượng", "dt_code", "dt code", "mã đt"]:
                clean_main.append(r)
                continue

            dt_code = str(r[0]).strip().upper()
            order_id = str(r[5]).strip().upper() if len(r) > 5 else ""

            # Check if duplicate
            if dt_code and dt_code in seen_dt:
                removed_main_count += 1
                continue
            if order_id and order_id in seen_orders:
                removed_main_count += 1
                continue

            if dt_code:
                seen_dt.add(dt_code)
            if order_id:
                seen_orders.add(order_id)
            clean_main.append(r)

        # Deduplicate links sheet
        seen_link_dt = set()
        clean_links = []
        removed_links_count = 0

        for idx, l in enumerate(links_rows):
            if not l:
                continue
            if idx == 0 and str(l[0]).strip().lower() in ["mã đối tượng", "dt_code", "dt code", "mã đt"]:
                clean_links.append(l)
                continue

            dt_code = str(l[0]).strip().upper()
            if dt_code and dt_code in seen_link_dt:
                removed_links_count += 1
                continue

            if dt_code:
                seen_link_dt.add(dt_code)
            clean_links.append(l)

        # Rewrite clean data
        if removed_main_count > 0:
            self.overwrite_sheet_data(clean_main, sheet_name=main_tab)
        if removed_links_count > 0:
            self.overwrite_sheet_data(clean_links, sheet_name=links_tab)

        logger.info(f"Deduplication complete: Purged {removed_main_count} main rows, {removed_links_count} link rows.")
        return {
            "success": True,
            "removed_main_rows": removed_main_count,
            "removed_links_rows": removed_links_count,
            "remaining_main_rows": len(clean_main),
            "remaining_links_rows": len(clean_links)
        }

    def overwrite_sheet_data(
        self, 
        rows: List[List[Any]] = None, 
        target_sheet_id: Optional[str] = None, 
        target_tab: Optional[str] = None,
        sheet_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Completely clear a sheet tab and write fresh rows.
        """
        if not self.sheets_service:
            self._init_auth()
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        sheet_id = target_sheet_id or os.getenv("GOOGLE_SHEET_ID")
        tab_name = target_tab or sheet_name or os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")

        try:
            # 1. Clear the entire tab
            self.sheets_service.spreadsheets().values().clear(
                spreadsheetId=sheet_id,
                range=f"{tab_name}!A1:Z5000",
                body={}
            ).execute()

            # 2. Write new rows if provided
            if rows and len(rows) > 0:
                body = {"values": rows}
                self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=f"{tab_name}!A1",
                    valueInputOption="USER_ENTERED",
                    body=body
                ).execute()

            return {"success": True, "message": f"Đã ghi đè {len(rows) if rows else 0} dòng vào {tab_name}."}
        except Exception as e:
            logger.error(f"Error overwriting sheet {tab_name}: {e}")
            return {"success": False, "error": str(e)}

    def append_links_to_sheet(
        self, 
        rows: List[List[Any]], 
        spreadsheet_id: Optional[str] = None, 
        sheet_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Append link rows (Mã đối tượng, Tên file, Link Drive) to a secondary Google Sheet.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        target_tab = sheet_name or os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don")

        headers = ["Mã đối tượng", "Tên file", "Link Drive", "Xác nhận"]

        try:
            # Create a dedicated attempt to fetch or append
            existing = self.get_sheet_data(target_sheet_id, target_tab)
            values_to_append = []
            if not existing or len(existing) == 0:
                values_to_append.append(headers)

            values_to_append.extend(rows)

            body = {"values": values_to_append}
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()

            return {
                "success": True,
                "updated_rows": result.get("updates", {}).get("updatedRows", len(values_to_append)),
                "updated_range": result.get("updates", {}).get("updatedRange", "")
            }
        except Exception as e:
            logger.error(f"Error appending link rows to Google Sheet: {e}")
            return {"success": False, "error": str(e)}

    def confirm_receipt_in_links(self, dt_code: str, status: str = "x") -> Dict[str, Any]:
        """
        Mark 'x' (or clear) in Column D (Xác nhận) in Links_Hoa_Don corresponding to dt_code.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")
        target_tab = os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don")

        try:
            all_rows = self.get_sheet_data(target_sheet_id, target_tab)
            if not all_rows:
                return {"success": False, "error": f"Bảng {target_tab} đang trống."}

            # Ensure Header D1 is 'Xác nhận'
            if len(all_rows[0]) < 4 or str(all_rows[0][3]).strip() != "Xác nhận":
                self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=target_sheet_id,
                    range=f"{target_tab}!D1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [["Xác nhận"]]}
                ).execute()

            # Find matching row for dt_code
            target_row_idx = None
            for i, row in enumerate(all_rows):
                if row and str(row[0]).strip().upper() == dt_code.strip().upper():
                    target_row_idx = i + 1  # 1-indexed for Sheets
                    break

            if target_row_idx is None:
                return {"success": False, "error": f"Không tìm thấy mã {dt_code} trong {target_tab}"}

            val_to_set = "x" if status.lower() == "x" else ""
            # Update cell D{target_row_idx}
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=target_sheet_id,
                range=f"{target_tab}!D{target_row_idx}",
                valueInputOption="USER_ENTERED",
                body={"values": [[val_to_set]]}
            ).execute()

            action_desc = "Đã xác nhận (đánh dấu 'x')" if val_to_set == "x" else "Đã từ chối (xóa dấu 'x')"
            return {"success": True, "status": val_to_set, "message": f"{action_desc} cho hóa đơn {dt_code}."}
        except Exception as e:
            logger.error(f"Error confirming receipt in {target_tab}: {e}")
            return {"success": False, "error": str(e)}

    # Alias for backwards compatibility
    confirm_receipt_in_sheet = confirm_receipt_in_links

    def update_sheet_row(self, dt_code: str, updated_row: List[Any]) -> Dict[str, Any]:
        """
        Find all rows matching dt_code in Bang_Ke_Hoa_Don and update their content.
        Returns the number of rows updated.
        """
        if not self.sheets_service:
            raise RuntimeError("Google Sheets service is not connected.")

        target_sheet_id = os.getenv("GOOGLE_SHEET_ID")
        target_tab = os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")

        try:
            all_rows = self.get_sheet_data(target_sheet_id, target_tab)
            updated_count = 0
            batch_updates = []

            for i, row in enumerate(all_rows):
                # Row 0 is header, actual data starts at row 1 (sheet row 2)
                if len(row) == 0:
                    continue
                if str(row[0]).strip().upper() == dt_code.upper():
                    # Sheet is 1-indexed, and row 0 in array = row 1 in sheet (header)
                    # So array index i => sheet row i+1
                    sheet_row_number = i + 1  # 1-indexed sheet row
                    range_to_update = f"{target_tab}!A{sheet_row_number}:N{sheet_row_number}"
                    batch_updates.append({
                        "range": range_to_update,
                        "values": [updated_row]
                    })
                    updated_count += 1

            if not batch_updates:
                return {"success": False, "error": f"Không tìm thấy dòng nào với mã {dt_code}"}

            body = {
                "valueInputOption": "USER_ENTERED",
                "data": batch_updates
            }
            self.sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=target_sheet_id,
                body=body
            ).execute()

            return {"success": True, "updated_rows": updated_count}
        except Exception as e:
            logger.error(f"Error updating sheet row for {dt_code}: {e}")
            return {"success": False, "error": str(e)}


    def get_historical_records(self) -> List[Dict[str, Any]]:
        """
        Fetch all records from Bang_Ke_Hoa_Don and Links_Hoa_Don, then group them by DT_CODE.
        Returns a list of structured records ready to be displayed in the UI.
        """
        try:
            main_sheet_id = os.getenv("GOOGLE_SHEET_ID")
            main_tab = os.getenv("GOOGLE_SHEET_NAME", "Bang_Ke_Hoa_Don")
            links_tab = os.getenv("GOOGLE_SHEET_LINKS_NAME", "Links_Hoa_Don")

            # Fetch both sheets
            main_rows = self.get_sheet_data(main_sheet_id, main_tab)
            link_rows = self.get_sheet_data(main_sheet_id, links_tab)

            # Build a dictionary of links: DT_CODE -> { file_name, link, confirmed }
            links_dict = {}
            for r in link_rows:
                if len(r) >= 3:
                    dt_code = str(r[0]).strip().upper()
                    is_conf = len(r) > 3 and str(r[3]).strip().lower() == "x"
                    links_dict[dt_code] = {
                        "file_name": str(r[1]).strip(),
                        "link": str(r[2]).strip(),
                        "confirmed": is_conf
                    }

            # Group main rows by DT_CODE
            records_dict = {}
            for r in main_rows:
                if len(r) == 0:
                    continue
                dt_code = str(r[0]).strip().upper()
                if not dt_code.startswith("DT"):
                    continue

                if dt_code not in records_dict:
                    file_info = links_dict.get(dt_code, {})
                    records_dict[dt_code] = {
                        "dt_code": dt_code,
                        "datetime": r[1] if len(r) > 1 else "",
                        "merchant": r[2] if len(r) > 2 else "",
                        "address_seller": r[3] if len(r) > 3 else "",
                        "address_buyer": r[4] if len(r) > 4 else "",
                        "order_id": r[5] if len(r) > 5 else "",
                        "items": [],
                        "total_amount": 0,
                        "customer": r[12] if len(r) > 12 else "",
                        "notes": r[13] if len(r) > 13 else "",
                        "file_info": file_info,
                        "confirmed": file_info.get("confirmed", False)
                    }
                
                # Append item
                item_name = r[6] if len(r) > 6 else ""
                qty = r[7] if len(r) > 7 else "1"
                price = r[8] if len(r) > 8 else "0"
                vat_rate = r[9] if len(r) > 9 else "0"
                vat_amount = r[10] if len(r) > 10 else "0"
                row_total = r[11] if len(r) > 11 else "0"

                records_dict[dt_code]["items"].append({
                    "item_name": item_name,
                    "quantity": qty,
                    "price": price,
                    "vat_rate": vat_rate,
                    "vat_amount": vat_amount,
                    "row_total": row_total
                })

            return list(records_dict.values())
        except Exception as e:
            logger.error(f"Error fetching historical records: {e}")
            return []

    def get_audit_reconciliation(self) -> Dict[str, Any]:
        """
        Reconciliation between all Google Drive files and Google Sheet rows.
        """
        inbox_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        proc_id = os.getenv("GOOGLE_DRIVE_PROCESSED_FOLDER_ID")

        inbox_files = self.list_images_in_folder(inbox_id) if inbox_id else []
        proc_files = self.list_images_in_folder(proc_id) if proc_id else []
        sheet_rows = self.get_sheet_data()
        links_rows = self.get_sheet_data(sheet_name="Links_Hoa_Don")

        linked_file_ids = set()
        for r in links_rows[1:]:
            if len(r) >= 3 and r[2]:
                m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", str(r[2]))
                if m:
                    linked_file_ids.add(m.group(1))

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

