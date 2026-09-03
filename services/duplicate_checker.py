"""
Duplicate Checker Service for Procurement Invoices.
Detects duplicates based on Invoice Number, Order ID, or Tracking Number.
"""
from typing import Set, List, Dict, Any, Tuple, Optional

def clean_dt_code(val: Any) -> str:
    """Clean and normalize DT code or order ID string (strips quotes, whitespace, uppercase)."""
    if val is None:
        return ""
    s = str(val).strip()
    while s.startswith("'") or s.startswith('"'):
        s = s[1:].strip()
    while s.endswith("'") or s.endswith('"'):
        s = s[:-1].strip()
    return s.upper()

class DuplicateChecker:
    """Tracks existing invoice/tracking numbers to detect duplicates."""

    def __init__(self, existing_codes: Optional[Set[str]] = None):
        self.seen_codes: Set[str] = set()
        if existing_codes:
            for c in existing_codes:
                cleaned = clean_dt_code(c)
                if cleaned:
                    self.seen_codes.add(cleaned)

    def add_code(self, code: str):
        cleaned = clean_dt_code(code)
        if cleaned:
            self.seen_codes.add(cleaned)

    def check_duplicate(self, code: str) -> Tuple[bool, str]:
        """
        Check if a given document code is duplicate.
        Returns: (is_duplicate, message)
        """
        cleaned = clean_dt_code(code)
        if not cleaned:
            return False, ""

        if cleaned in self.seen_codes:
            return True, f"CẢNH BÁO: Mã chứng từ/đơn hàng '{code}' ĐÃ TỒN TẠI trong hệ thống (Trùng lặp)!"

        return False, ""

    def load_from_header_v2_rows(self, rows: List[List[Any]]):
        """
        Extract existing doc codes and DT codes from Data_Header_V2 rows (13-column format).
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

            # Column F (index 5): Mã hóa đơn, chứng từ / Mã đơn hàng
            if len(r) > 5 and r[5]:
                val = str(r[5]).strip()
                if val and len(val) >= 4:
                    self.add_code(val)

            # Column M (index 12) or any note column: Ghi chú chung
            note_candidates = []
            if len(r) > 12 and r[12]:
                note_candidates.append(str(r[12]))
            if len(r) > 13 and r[13] and r[13] != "x":
                note_candidates.append(str(r[13]))
            if len(r) >= 7 and not note_candidates:
                note_candidates.append(str(r[-1]))

            for note_str in note_candidates:
                match = re.search(r"Mã vận đơn:\s*([A-Za-z0-9_-]+)", note_str)
                if match:
                    self.add_code(match.group(1))
                for token in re.findall(r"\b[A-Za-z0-9]{8,30}\b", note_str):
                    self.add_code(token)

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

