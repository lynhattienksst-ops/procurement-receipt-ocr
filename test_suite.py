"""
Test Suite for Procurement Receipt OCR & Rules Engine (13-Column Schema with VAT Calculation)
Verifies:
1. Classification into DT1, DT2, DT3, DT4
2. Category 1 formatting (13 columns, Col F/H/I/J empty, Col K total, Col M note with tracking & items)
3. Category 2 VAT Calculation (Unit price before tax, % VAT, VAT amount, Line Total, same DT20001 code)
4. Category 3 removal of '(loại bỏ)' tagged items and VAT calculation
5. VAT detection and manual review alert
6. Duplicate detection
7. Vietnamese spell check
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from services.business_rules import detect_business_category, format_receipt_to_sheet_rows, check_vat_alert
from services.duplicate_checker import DuplicateChecker
from services.spell_checker import check_vietnamese_spelling, validate_receipt_fields

def test_category_1_ecommerce():
    print("[*] Testing Category 1 (E-commerce / Delivery)...")
    sample_cat1 = {
        "merchant_name": "SPX Express",
        "merchant_address": "798 Phạm Văn Đồng, TP. Thủ Đức, TP. Hồ Chí Minh",
        "customer_name": "Nguyễn Văn A",
        "order_id": "2601287Q55X4RX",
        "tracking_number": "SPXVN064620286071",
        "transaction_date": "2026-01-28",
        "transaction_time": "09:10:00",
        "total_amount": 283659,
        "line_items": [
            {"item_name": "Dâu Sấy Thăng Hoa", "item_quantity": 2, "item_price": 100000},
            {"item_name": "Nho xanh khô", "item_quantity": 1, "item_price": 50000}
        ]
    }
    cat_id, cat_name = detect_business_category(sample_cat1)
    assert cat_id == 1, f"Expected 1, got {cat_id}"
    
    rows, meta = format_receipt_to_sheet_rows(sample_cat1, cat_id, 1)
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    row = rows[0]
    # 14-Column Structure:
    # [DT10001, Ngày tháng, Tên sàn, Địa chỉ bên bán, Địa chỉ bên nhận, Mã đơn hàng, Tên hàng, SL, Đơn giá, % VAT, VAT, Thành tiền, Người nhận, Ghi chú/Vận đơn]
    assert len(row) == 14, f"Expected 14 columns, got {len(row)}"
    assert row[0] == "DT10001", f"Col A should be DT10001, got {row[0]}"
    assert "SPX Express" in row[2] or "Shopee" in row[2]
    assert row[5] == "2601287Q55X4RX", f"Col F should be Order ID, got {row[5]}"
    assert row[11] == 283659, f"Col L (Thành tiền) should be 283659, got {row[11]}"
    assert row[12] == "Nguyễn Văn A", f"Col M should be Customer Name, got {row[12]}"
    assert "Mã vận đơn: SPXVN064620286071" in row[13], f"Col N should contain tracking no, got {row[13]}"
    print("  -> Passed Category 1 (14 Columns, DT10001)!")

def test_category_2_supermarket_vat_calc():
    print("[*] Testing Category 2 (Supermarket VAT calculation)...")
    sample_cat2 = {
        "object_code": "DT20001",
        "merchant_name": "Siêu thị WinMart",
        "merchant_address": "123 Cầu Giấy, Hà Nội",
        "customer_name": "Chị Lan (Bếp)",
        "invoice_number": "HD-WIN-001",
        "transaction_date": "2026-08-15",
        "subtotal_amount": 100000,
        "tax_rate": 0.10,
        "tax_amount": 10000,
        "total_amount": 110000,
        "is_price_inclusive_of_vat": False,
        "line_items": [
            {"item_name": "Sữa chua Vinamilk", "item_quantity": 2, "item_price": 30000},
            {"item_name": "Bánh mì sandwich", "item_quantity": 1, "item_price": 40000}
        ]
    }
    cat_id, _ = detect_business_category(sample_cat2)
    assert cat_id == 2, f"Expected 2, got {cat_id}"

    rows, meta = format_receipt_to_sheet_rows(sample_cat2, cat_id, 1)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    
    # Both rows must have same code DT20001
    assert rows[0][0] == "DT20001"
    assert rows[1][0] == "DT20001"
    
    # Check Row 1 (Sữa chua: SL 2, Giá chưa thuế 30000, 10% VAT -> VAT 6000, Thành tiền 66000)
    r1 = rows[0]
    assert len(r1) == 14, f"Expected 14 columns, got {len(r1)}"
    assert r1[6] == "Sữa chua Vinamilk"
    assert r1[7] == 2
    assert r1[8] == 30000.0  # Đơn giá chưa VAT
    assert r1[9] == "10%"    # % VAT
    assert r1[10] == 6000.0   # Tiền VAT
    assert r1[11] == 66000.0 # Thành tiền
    assert r1[12] == "Chị Lan (Bếp)"

    # Check Row 2 (Bánh mì: SL 1, Giá chưa thuế 40000, 10% VAT -> VAT 4000, Thành tiền 44000)
    r2 = rows[1]
    assert r2[6] == "Bánh mì sandwich"
    assert r2[7] == 1
    assert r2[8] == 40000.0
    assert r2[9] == "10%"
    assert r2[10] == 4000.0
    assert r2[11] == 44000.0
    print("  -> Passed Category 2 VAT Calculation!")

def test_category_3_removal_tag():
    print("[*] Testing Category 3 (Food Supply with '(loại bỏ)' filter)...")
    sample_cat3 = {
        "object_code": "DT30005",
        "merchant_name": "Công ty Nông Sản & Thực Phẩm Sạch Việt",
        "merchant_address": "Chợ đầu mối Thủ Đức",
        "customer_name": "Anh Nam (Kho)",
        "invoice_number": "HD-2026-0099",
        "transaction_date": "2026-08-14",
        "tax_rate": 0.08,
        "tax_amount": 36800,
        "subtotal_amount": 460000,
        "total_amount": 496800,
        "line_items": [
            {"item_name": "Thịt heo sạch 5kg", "item_quantity": 5, "item_price": 80000},
            {"item_name": "Rau muống (loại bỏ)", "item_quantity": 2, "item_price": 20000},
            {"item_name": "Cà chua tươi", "item_quantity": 3, "item_price": 20000}
        ]
    }
    cat_id, _ = detect_business_category(sample_cat3)
    assert cat_id == 3, f"Expected 3, got {cat_id}"

    rows, meta = format_receipt_to_sheet_rows(sample_cat3, cat_id, 5)
    # The item with '(loại bỏ)' must be excluded!
    assert len(rows) == 2, f"Expected 2 rows after filtering, got {len(rows)}"
    item_names = [r[6] for r in rows]
    assert "Thịt heo sạch 5kg" in item_names
    assert "Cà chua tươi" in item_names
    assert "Rau muống (loại bỏ)" not in item_names
    assert rows[0][0] == "DT30005"
    assert rows[1][0] == "DT30005"
    assert rows[0][9] == "8%"
    print("  -> Passed Category 3 with '(loại bỏ)' filter and VAT!")

def test_vat_alert():
    print("[*] Testing VAT Alert detection...")
    sample_vat = {
        "merchant_name": "Siêu thị WinMart",
        "total_amount": 220000,
        "tax_amount": 20000
    }
    has_vat, vat_amt, vat_alert = check_vat_alert(sample_vat)
    assert has_vat is True
    assert vat_amt == 20000
    assert "CẢNH BÁO" in vat_alert
    print("  -> Passed VAT Alert test!")

def test_duplicate_checker():
    print("[*] Testing Duplicate Checker...")
    checker = DuplicateChecker()
    sheet_rows = [
        ["DT10001", "2026-01-28", "Shopee", "HCM", "2601287Q55X4RX", "", "4", "283659", "", "", "283659", "Nguyễn Văn A", "SPXVN064620286071"]
    ]
    checker.load_from_sheet_rows(sheet_rows)
    
    # Test duplicate detection
    is_dup, msg = checker.check_duplicate("2601287Q55X4RX")
    assert is_dup is True
    assert "TRÙNG LẶP" in msg or "TỒN TẠI" in msg

    # Test tracking number duplicate detection
    is_dup2, _ = checker.check_duplicate("SPXVN064620286071")
    assert is_dup2 is True

    # Test new code
    is_dup_new, _ = checker.check_duplicate("NEW_ORDER_9999")
    assert is_dup_new is False
    print("  -> Passed Duplicate Checker test!")

def test_spell_checker():
    print("[*] Testing Vietnamese Spell Checker...")
    sample_valid = {
        "merchant_name": "Công ty TNHH Thực Phẩm Sạch",
        "line_items": [{"item_name": "Bánh mì bơ tỏi đặc biệt"}]
    }
    res_ok = validate_receipt_fields(sample_valid)
    assert len(res_ok) == 0

    sample_err = {
        "merchant_name": "Cônng tyy Hồngg Hà",
        "line_items": [{"item_name": "Thịt heo xào xả ớtt"}]
    }
    res_err = validate_receipt_fields(sample_err)
    assert len(res_err) > 0
    print("  -> Passed Spell Checker test!")

if __name__ == "__main__":
    test_category_1_ecommerce()
    test_category_2_supermarket_vat_calc()
    test_category_3_removal_tag()
    test_vat_alert()
    test_duplicate_checker()
    test_spell_checker()
    print("\n[🎉] ALL UNIT TESTS PASSED SUCCESSFULLY (13-COLUMN VAT SCHEMA)!")
