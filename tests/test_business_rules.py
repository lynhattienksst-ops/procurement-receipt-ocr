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

# Add repo root to path (this file lives in tests/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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

    # Test Data_Header_V2 14-column format with confirmed 'x' in Col N
    header_v2_rows = [
        ["Mã đối tượng", "Ngày", "Tên cty", "Đ/c bán", "Đ/c nhận", "Mã HĐ", "Tiền gốc", "CK", "VAT", "Tổng", "Người nhận", "Link", "Ghi chú", "Xác nhận"],
        ["DT10002", "2026-01-28", "Shopee", "HCM", "HN", "ORD_SHP_9988", 200000, 0, 0, 200000, "Nguyễn B", "http://drive", "Mã vận đơn: SPXVN998877", "x"],
        ["DT20079", "2026-02-01", "WinMart", "HN", "", "HD-WIN-20079", 150000, 0, 15000, 165000, "", "http://drive", "SĐT: 0912345678", ""]
    ]
    checker_v2 = DuplicateChecker()
    checker_v2.load_from_header_v2_rows(header_v2_rows)
    assert checker_v2.check_duplicate("DT10002")[0] is True
    assert checker_v2.check_duplicate("ORD_SHP_9988")[0] is True
    assert checker_v2.check_duplicate("SPXVN998877")[0] is True
    assert checker_v2.check_duplicate("DT20079")[0] is True
    assert checker_v2.check_duplicate("HD-WIN-20079")[0] is True
    assert checker_v2.check_duplicate("DT99999")[0] is False

    print("  -> Passed Duplicate Checker (V1 & V2 Relational) test!")

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

def test_file_sorting_priority():
    print("[*] Testing File Sorting Priority (JPEG/PNG first, PDF second)...")
    from services.google_service import GoogleSyncService
    
    mixed_files = [
        {"id": "f1", "name": "invoice_multipage.pdf", "mimeType": "application/pdf", "createdTime": "2026-08-20T10:00:00Z"},
        {"id": "f2", "name": "receipt_photo1.jpg", "mimeType": "image/jpeg", "createdTime": "2026-08-20T10:01:00Z"},
        {"id": "f3", "name": "bill_scan2.png", "mimeType": "image/png", "createdTime": "2026-08-20T10:02:00Z"},
        {"id": "f4", "name": "contract_multi.pdf", "mimeType": "application/pdf", "createdTime": "2026-08-20T10:03:00Z"},
        {"id": "f5", "name": "photo_receipt3.jpeg", "mimeType": "image/jpeg", "createdTime": "2026-08-20T10:04:00Z"}
    ]
    
    sorted_files = GoogleSyncService.sort_files_by_priority(mixed_files)
    assert len(sorted_files) == 5, f"Expected 5 files, got {len(sorted_files)}"
    
    # First 3 files must be images (priority 0)
    image_names = [f["name"] for f in sorted_files[:3]]
    assert "receipt_photo1.jpg" in image_names
    assert "bill_scan2.png" in image_names
    assert "photo_receipt3.jpeg" in image_names
    
    # Last 2 files must be PDFs (priority 1)
    pdf_names = [f["name"] for f in sorted_files[3:]]
    assert "invoice_multipage.pdf" in pdf_names
    assert "contract_multi.pdf" in pdf_names
    
def test_pdf_filename_merging():
    print("[*] Testing PDF Filename Merging Key Extraction...")
    from server import extract_pdf_base_key
    
    key1 = extract_pdf_base_key("Scanned_20260821-1437.pdf", "file_1")
    key2 = extract_pdf_base_key("Scanned_20260821-1437_p2.pdf", "file_2")
    key3 = extract_pdf_base_key("Scanned_20260821-1437_page3.png", "file_3")
    
    assert key1 == "pdf_name_scanned_20260821-1437", f"Expected pdf_name_scanned_20260821-1437, got {key1}"
    assert key2 == "pdf_name_scanned_20260821-1437", f"Expected pdf_name_scanned_20260821-1437, got {key2}"
    assert key3 == "pdf_name_scanned_20260821-1437", f"Expected pdf_name_scanned_20260821-1437, got {key3}"
    print("  -> Passed PDF Filename Merging Key test (Pages 1, 2, 3 share exact base key)!")

def test_datetime_normalizer():
    print("[*] Testing Vietnamese Accounting DateTime Normalizer...")
    from services.business_rules import normalize_datetime_vn
    
    # Standard ISO Date + Time
    assert normalize_datetime_vn("2026-08-28", "14:30:00") == "'14:30:00 28-08-2026"
    assert normalize_datetime_vn("2026-08-28 14:30:00") == "'14:30:00 28-08-2026"
    
    # Slash format DD/MM/YYYY + Short Time HH:MM
    assert normalize_datetime_vn("28/08/2026", "14:30") == "'14:30:00 28-08-2026"
    
    # Date only (no time) -> defaults to 00:00:00
    assert normalize_datetime_vn("2026-01-28") == "'00:00:00 28-01-2026"
    
    # Already formatted Vietnamese DateTime
    assert normalize_datetime_vn("14:30:00 28-08-2026") == "'14:30:00 28-08-2026"
    
    # Empty string
    assert normalize_datetime_vn("") == ""
    assert normalize_datetime_vn(None) == ""
    print("  -> Passed Vietnamese DateTime Normalizer test!")

def test_relational_v2_datetime_format():
    print("[*] Testing Relational V2 Format Column B DateTime...")
    from services.business_rules import format_receipt_to_relational_v2
    sample = {
        "merchant_name": "Co.opmart",
        "transaction_date": "2026-08-28",
        "transaction_time": "15:45:00",
        "total_amount": 500000,
        "line_items": [{"item_name": "Sữa", "item_price": 50000, "item_quantity": 10}]
    }
    header, lines, meta = format_receipt_to_relational_v2(sample, category_id=2, current_index=1)
    # Header Column B is index 1
    assert header[1] == "'15:45:00 28-08-2026", f"Expected ''15:45:00 28-08-2026', got {header[1]}"
    print("  -> Passed Relational V2 Column B DateTime Format test!")

def test_category_migration_and_key_guard():
    print("[*] Testing Category Migration & Primary Key Anchor Guard...")
    from services.google_service import GoogleSyncService, clean_dt_code
    gs = GoogleSyncService()
    
    # Test 1: clean_dt_code
    assert clean_dt_code("DT20001") == "DT20001"
    assert clean_dt_code(" dt10005 ") == "DT10005"
    assert clean_dt_code("'DT20001'") == "DT20001"
    
    # Test 2: process_dt1_pipeline rogue ID protection
    # Even if row[0] is set to a rogue 'DT10001', the pipeline must anchor strictly to the target DT code
    rogue_row = ["DT10001", "2026-08-28", "Shopee", "Addr S", "Addr B", "ORD123", "Item", 1, 100000, "0%", 0, 100000, "Buyer", "Notes"]
    
    # Mocking update_relational_record to inspect passed arguments
    saved_target = None
    saved_header = None
    saved_lines = None
    def mock_update(target_dt, h_row, l_rows):
        nonlocal saved_target, saved_header, saved_lines
        saved_target = target_dt
        saved_header = h_row
        saved_lines = l_rows
        return {"success": True}
    
    gs.update_relational_record = mock_update
    gs.get_sheet_data = lambda *args, **kwargs: []
    
    gs.process_dt1_pipeline("DT10327", rogue_row)
    assert saved_target == "DT10327", f"Expected DT10327, got {saved_target}"
    assert saved_header[0] == "DT10327", f"Expected header[0] to be DT10327, got {saved_header[0]}"
    assert saved_lines[0][0] == "DT10327", f"Expected lines[0][0] to be DT10327, got {saved_lines[0][0]}"
    print("  -> Passed Category Migration & Primary Key Anchor Guard test!")

def test_historical_records_drive_link_resolution():
    print("[*] Testing Historical Records Drive Link Resolution (V2-only, no Links_Hoa_Don)...")
    from services.google_service import GoogleSyncService
    gs = GoogleSyncService()

    # Legacy Links_Hoa_Don fallback has been removed: image links come only from
    # Data_Header_V2 column L (index 11), with a whole-row URL scan as a safety
    # net for legacy 13-column rows where the URL sits in an earlier column.
    def mock_get_sheet(sheet_id=None, sheet_name=None):
        if sheet_name == "Data_Lines_V2":
            return [
                ["Mã đối tượng", "Mã SP", "Tên hàng", "SL", "ĐVT", "Đơn giá", "CK", "% CK", "% VAT", "Tiền VAT", "Thành tiền", "Ghi chú"],
                ["DT20002", "SKU1", "Áo đồng phục", "1", "Cái", "100000", "0", "0%", "0%", "0", "100000", ""],
                ["DT20279", "SKU2", "Vải may", "1", "Mét", "200000", "0", "0%", "0%", "0", "200000", ""]
            ]
        else:  # Data_Header_V2
            return [
                ["Mã đối tượng", "Ngày", "Công ty", "Địa chỉ bán", "Địa chỉ mua", "Mã HĐ", "Tiền hàng", "CK", "VAT", "Tổng TT", "Người nhận", "Link ảnh", "Ghi chú", "Xác nhận"],
                # Row 1: 14-col with explicit link in column L
                ["DT20002", "2026-08-28", "Cty A", "HN", "HCM", "HD002", "100000", "0", "0", "100000", "Huy", "https://drive.google.com/file/d/LINK_20002/view", "Ghi chú", "x"],
                # Row 2: legacy 13-col row, URL landed in column K (index 10) instead of L
                ["DT20279", "2026-08-28", "Cty B", "DN", "HCM", "HD279", "200000", "0", "0", "200000", "https://drive.google.com/file/d/LINK_20279/view", "Ghi chú", ""]
            ]

    gs.get_sheet_data = mock_get_sheet
    records = gs.get_historical_records()
    assert len(records) == 2, f"Expected 2 records, got {len(records)}"

    rec20002 = next((r for r in records if r["dt_code"] == "DT20002"), None)
    rec20279 = next((r for r in records if r["dt_code"] == "DT20279"), None)

    assert rec20002 is not None and rec20002["drive_link"] == "https://drive.google.com/file/d/LINK_20002/view"
    assert rec20279 is not None and rec20279["drive_link"] == "https://drive.google.com/file/d/LINK_20279/view"
    assert rec20279["drive_link"] != rec20002["drive_link"], "DT20279 must not inherit DT20002 link!"
    print("  -> Passed V2-only Drive Link Resolution test!")

if __name__ == "__main__":
    test_datetime_normalizer()
    test_relational_v2_datetime_format()
    test_category_1_ecommerce()
    test_category_2_supermarket_vat_calc()
    test_category_3_removal_tag()
    test_vat_alert()
    test_duplicate_checker()
    test_spell_checker()
    test_file_sorting_priority()
    test_pdf_filename_merging()
    test_category_migration_and_key_guard()
    test_historical_records_drive_link_resolution()
    print("\n[🎉] ALL UNIT TESTS PASSED SUCCESSFULLY (14-COLUMN SCHEMA, V2 RELATIONAL & MIGRATION GUARD)!")




