"""
Duplicate Checker Service for Procurement Invoices.
Detects duplicates based on Invoice Number, Order ID, or Tracking Number.
"""
from typing import Set, List, Dict, Any, Tuple, Optional

class DuplicateChecker:
    """Tracks existing invoice/tracking numbers to detect duplicates."""

    def __init__(self, existing_codes: Optional[Set[str]] = None):
        self.seen_codes: Set[str] = set()
        if existing_codes:
            for c in existing_codes:
                if c and str(c).strip():
                    self.seen_codes.add(str(c).strip().upper())

    def add_code(self, code: str):
        if code and str(code).strip():
            self.seen_codes.add(str(code).strip().upper())

    def check_duplicate(self, code: str) -> Tuple[bool, str]:
        """
        Check if a given document code is duplicate.
        Returns: (is_duplicate, message)
        """
        if not code or not str(code).strip():
            return False, ""

        clean_code = str(code).strip().upper()
        if clean_code in self.seen_codes:
            return True, f"CẢNH BÁO: Mã chứng từ/đơn hàng '{code}' ĐÃ TỒN TẠI trong hệ thống (Trùng lặp)!"

        return False, ""

    def load_from_sheet_rows(self, rows: List[List[Any]]):
        """
        Extract existing doc codes from Google Sheet rows (Columns A, E, F, and Note column).
        """
        import re
        for r in rows:
            if not r or len(r) == 0:
                continue
            # Skip header
            if str(r[0]).strip().lower() in ["mã đối tượng", "dt_code", "dt code", "mã đt"]:
                continue

            # Column A: DT Code
            if len(r) >= 1 and r[0]:
                self.add_code(str(r[0]))
            # Column E / F: Order ID / Invoice Number / Document Code
            for col_idx in [4, 5]:
                if len(r) > col_idx and r[col_idx]:
                    val = str(r[col_idx]).strip()
                    if val and len(val) >= 4:
                        self.add_code(val)
            # Note column (last column)
            if len(r) >= 8:
                note_str = str(r[-1])
                match = re.search(r"Mã vận đơn:\s*([A-Za-z0-9_-]+)", note_str)
                if match:
                    self.add_code(match.group(1))
                # Also extract any alphanumeric order id token
                for token in re.findall(r"\b[A-Za-z0-9]{8,30}\b", note_str):
                    self.add_code(token)
