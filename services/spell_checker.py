"""
Vietnamese Spell Checker & Text Validator Service for Procurement Invoices.
Checks for OCR misrecognitions, invalid diacritics, broken telex patterns, and formatting issues.
"""
import re
from typing import List, Dict, Any, Tuple

# Common Vietnamese vowels with tones
VIETNAMESE_VOWELS = "aáàảãạăắằẳẵặâấầẩẫậeéèẻẽẹêếềểễệiíìỉĩịoóòỏõọôốồổỗộơớờởỡợuúùủũụưứừửữựyýỳỷỹỵ"

# Suspicious broken OCR or double telex patterns
SUSPICIOUS_PATTERNS = [
    (r"\b[a-zA-Z]*[dD]{2}[a-zA-Z]*\b", "Lỗi lặp ký tự Telex 'dd' (Đ)"),
    (r"\b[a-zA-Z]*[aA]{2}[a-zA-Z]*\b", "Lỗi lặp ký tự Telex 'aa' (Â)"),
    (r"\b[a-zA-Z]*[oO]{2}[a-zA-Z]*\b", "Lỗi lặp ký tự Telex 'oo' (Ô)"),
    (r"\b[a-zA-Z]*[eE]{2}[a-zA-Z]*\b", "Lỗi lặp ký tự Telex 'ee' (Ê)"),
    (r"[~`^!@#$%&*+=\[\]{}|\\<>?]", "Có ký tự lạ hoặc lỗi font OCR"),
    (r"\b\d+[a-zA-Z]+\d+\b", "Ký tự chữ số lẫn lộn bất thường trong từ"),
]

def check_vietnamese_spelling(text: str) -> Dict[str, Any]:
    """
    Check if a given text string contains suspicious spelling or OCR errors.
    Returns a dict with 'has_error', 'issues', and 'cleaned_text'.
    """
    if not text or not isinstance(text, str):
        return {"has_error": False, "issues": [], "cleaned_text": ""}

    text = text.strip()
    issues: List[str] = []

    # Check for suspicious regex patterns (excluding special codes like tracking numbers)
    words = text.split()
    for word in words:
        clean_word = re.sub(r"[^\w\s]", "", word)
        if len(clean_word) >= 2:
            # Skip all-uppercase abbreviations (e.g. TNHH, TP, TPHCM, MTV, CP, SPX)
            if clean_word.isupper():
                continue

            # Check 3+ repeated characters (e.g. sooo)
            if re.search(r"(.)\1{2,}", clean_word, re.IGNORECASE):
                issues.append(f"Từ '{word}' lặp ký tự bất thường 3 lần liên tiếp.")
            # Check double end consonants in Vietnamese (e.g. Hồngg, ănn, đẹpp)
            elif re.search(r"(bb|cc|dd|ff|gg|hh|jj|kk|ll|mm|nn|pp|qq|rr|ss|tt|vv|ww|xx|yy|zz)$", clean_word, re.IGNORECASE):
                issues.append(f"Từ '{word}' lặp phụ âm cuối bất thường.")
            # Check telex double letter artifacts
            elif re.search(r"(dd|aa|ee|oo|ww)", clean_word, re.IGNORECASE):
                issues.append(f"Từ '{word}' có dấu hiệu lỗi bộ gõ Telex.")

    return {
        "has_error": len(issues) > 0,
        "issues": issues,
        "original_text": text
    }


def validate_receipt_fields(data: Dict[str, Any]) -> List[str]:
    """
    Validate all key text fields of a receipt for spelling and OCR anomalies.
    """
    warnings: List[str] = []
    
    fields_to_check = [
        ("Tên công ty / Cửa hàng", data.get("merchant_name")),
        ("Địa chỉ", data.get("merchant_address")),
        ("Tên người nhận", data.get("customer_name")),
        ("Địa chỉ người nhận", data.get("customer_address")),
    ]

    for label, val in fields_to_check:
        if val and isinstance(val, str):
            res = check_vietnamese_spelling(val)
            if res["has_error"]:
                for issue in res["issues"]:
                    warnings.append(f"[{label}] {issue}")

    # Check line items
    for idx, item in enumerate(data.get("line_items", [])):
        item_name = item.get("item_name", "")
        if item_name:
            res = check_vietnamese_spelling(item_name)
            if res["has_error"]:
                for issue in res["issues"]:
                    warnings.append(f"[Mặt hàng #{idx+1}] {issue}")

    return warnings
