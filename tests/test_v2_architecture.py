"""
Unit Test Suite for Pure 2-Sheet Architecture (VAT, Trade Discounts, DT1-DT4 Categorization, Currency Formatting, and Duplicate Guard).
"""
import unittest
from services.business_rules import (
    detect_business_category,
    check_vat_alert,
    compute_item_vat_and_pricing,
    format_receipt_to_relational_v2,
    format_vietnamese_currency,
    classify_line_item,
    LINE_GROUP_CODES,
    REMOVAL_TAG
)
from services.duplicate_checker import DuplicateChecker

class TestV2Architecture(unittest.TestCase):

    def test_currency_formatting(self):
        self.assertEqual(format_vietnamese_currency(387997), "387.997,00 đ")
        self.assertEqual(format_vietnamese_currency(320000), "320.000,00 đ")
        self.assertEqual(format_vietnamese_currency(0), "0,00 đ")
        self.assertEqual(format_vietnamese_currency(1250000.5), "1.250.000,50 đ")

    def test_category_1_ecommerce(self):
        data = {
            "merchant_name": "SPX Express - Shop Nông Sản Đà Lạt",
            "tracking_number": "SPXVN064620286071",
            "order_id": "2601287Q55X4RX",
            "total_amount": 150000,
            "line_items": [
                {"item_name": "Dâu tây sấy giòn", "item_quantity": 2, "item_price": 50000, "measurement_unit": "gói"},
                {"item_name": "Nho khô Ninh Thuận", "item_quantity": 1, "item_price": 50000, "measurement_unit": "hộp"}
            ],
            "customer_name": "Nguyễn Văn A"
        }
        cat_id, cat_name = detect_business_category(data)
        self.assertEqual(cat_id, 1)

        header, lines, meta = format_receipt_to_relational_v2(data, category_id=1, current_index=1, drive_link="https://drive.link/1")
        self.assertEqual(header[0], "DT10001")
        self.assertEqual(header[5], "2601287Q55X4RX") # Order ID
        self.assertEqual(header[9], 150000) # Final Payment (Pure numeric)
        self.assertEqual(header[11], "https://drive.link/1") # Link
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0], "DT10001")
        self.assertIn("Dâu tây sấy giòn", lines[0][2])
        self.assertIn("Nho khô Ninh Thuận", lines[0][2])
        self.assertEqual(lines[0][3], 3) # Total Qty = 2 + 1 = 3
        self.assertEqual(lines[0][10], 150000) # Total amount 150000

    def test_category_2_supermarket_with_discount_and_vat(self):
        data = {
            "merchant_name": "WinMart Ba Đình",
            "invoice_number": "HD-109283",
            "line_items": [
                {
                    "product_code": "SP001",
                    "item_name": "Sữa chua Vinamilk",
                    "item_quantity": 2,
                    "measurement_unit": "hũ",
                    "item_price": 40000,
                    "discount_amount": 5000,
                    "vat_rate": 8.0,
                    "vat_amount": 6000
                },
                {
                    "product_code": "SP002",
                    "item_name": "Bánh mì sandwich",
                    "item_quantity": 1,
                    "measurement_unit": "bịch",
                    "item_price": 70000,
                    "discount_rate": 10.0,
                    "vat_rate": 10.0
                }
            ],
            "customer_name": "Trần Thị B"
        }
        cat_id, cat_name = detect_business_category(data)
        self.assertEqual(cat_id, 2)

        header, lines, meta = format_receipt_to_relational_v2(data, category_id=2, current_index=5)
        self.assertEqual(header[0], "DT20005")
        self.assertEqual(len(lines), 2)
        
        # Item 1 Check
        self.assertEqual(lines[0][0], "DT20005")
        self.assertEqual(lines[0][1], "SP001")
        self.assertEqual(lines[0][4], "hũ") # ĐVT
        self.assertEqual(lines[0][5], 40000) # Unit price (shifted to index 5)
        self.assertEqual(lines[0][6], 5000) # Discount (shifted to index 6)
        self.assertEqual(lines[0][7], 6.25) # Discount rate (shifted to index 7)
        self.assertEqual(lines[0][8], 8) # VAT rate (shifted to index 8)
        self.assertEqual(lines[0][9], 6000) # VAT Amount (shifted to index 9)
        self.assertEqual(lines[0][10], 81000) # Total (shifted to index 10)
        
        # Item 2 Check: 70,000 * 10% discount = 7,000 discount
        # Line 2 subtotal = 70,000 - 7,000 = 63,000. VAT 10% = 6,300. Total = 69,300
        self.assertEqual(lines[1][1], "SP002")
        self.assertEqual(lines[1][4], "bịch") # ĐVT
        self.assertEqual(lines[1][5], 70000) # Unit price
        self.assertEqual(lines[1][6], 7000) # 10% of 70,000
        self.assertEqual(lines[1][7], 10) # Discount rate
        self.assertEqual(lines[1][8], 10) # VAT rate
        self.assertEqual(lines[1][9], 6300) # 10% of 63,000
        self.assertEqual(lines[1][10], 69300) # Total (shifted to index 10)

    def test_category_3_removal_tag_filter(self):
        data = {
            "merchant_name": "Công ty Cung Ứng Nông Sản Sạch Việt",
            "invoice_number": "NS-9988",
            "line_items": [
                {"item_name": "Thịt bò Úc tươi", "item_quantity": 5, "item_price": 200000},
                {"item_name": "Rau cải ngọt (loại bỏ)", "item_quantity": 10, "item_price": 15000},
                {"item_name": "Thịt gà thả vườn", "item_quantity": 3, "item_price": 100000}
            ]
        }
        cat_id, cat_name = detect_business_category(data)
        self.assertEqual(cat_id, 3)

        header, lines, meta = format_receipt_to_relational_v2(data, category_id=3, current_index=12)
        # Should filter out the second item containing '(loại bỏ)'
        self.assertEqual(len(lines), 2)
        item_names = [l[2] for l in lines]
        self.assertIn("Thịt bò Úc tươi", item_names)
        self.assertIn("Thịt gà thả vườn", item_names)
        self.assertNotIn("Rau cải ngọt (loại bỏ)", item_names)

        # Header Total should only sum valid items: (5*200k) + (3*100k) = 1,300,000
        self.assertEqual(header[6], 1300000) # Total Raw (Pure numeric)
        self.assertEqual(header[9], 1300000) # Total Payment (Pure numeric)

    def test_line_item_classification(self):
        # Keyword matches
        self.assertEqual(classify_line_item("Rau muống")[0], "NL_TP")
        self.assertEqual(classify_line_item("Thịt heo ba chỉ")[0], "NL_TP")
        self.assertEqual(classify_line_item("Phí vận chuyển")[0], "DV_VC")
        self.assertEqual(classify_line_item("Phí sàn Shopee tháng 8")[0], "DV_SAN")
        self.assertEqual(classify_line_item("Hoa hồng đơn hàng")[0], "DV_SAN")
        self.assertEqual(classify_line_item("Xoong inox 24cm")[0], "CCDC_TS")
        self.assertEqual(classify_line_item("Giấy A4 Double A")[0], "VP_PHAM")
        self.assertEqual(classify_line_item("Túi nilon 2kg")[0], "HH_BAN")
        self.assertEqual(classify_line_item("Phí tư vấn kế toán")[0], "DV_KHAC")

        # Priority: "phí vận chuyển" wins over generic goods token
        self.assertEqual(classify_line_item("Phí vận chuyển rau củ")[0], "DV_VC")

        # source labels
        self.assertEqual(classify_line_item("Rau muống")[1], "rule:keyword")
        self.assertEqual(classify_line_item("Cá lóc", unit="kg")[1], "rule:keyword")  # keyword first
        self.assertEqual(classify_line_item("Món lạ không rõ", unit="bó")[1], "unit_hint")
        self.assertEqual(classify_line_item("Món lạ không rõ", unit="bó")[0], "NL_TP")

        # Unknown → KHAC / auto_default
        code, src = classify_line_item("Mặt hàng XYZ độc lạ")
        self.assertEqual(code, "KHAC")
        self.assertEqual(src, "auto_default")

        # Empty / placeholder → CAN_SOAT / low_conf
        self.assertEqual(classify_line_item("")[0], "CAN_SOAT")
        self.assertEqual(classify_line_item("Đơn hàng TMĐT")[0], "CAN_SOAT")
        self.assertEqual(classify_line_item("Hóa đơn mua hàng")[1], "low_conf")

        # All returned codes are valid
        for name in ["Rau muống", "Phí sàn", "", "Random thing"]:
            self.assertIn(classify_line_item(name)[0], LINE_GROUP_CODES)

    def test_relational_v2_lines_have_group_columns(self):
        # DT2: per-item classification lands in columns M (12) and N (13)
        data = {
            "merchant_name": "WinMart Ba Đình",
            "invoice_number": "HD-1",
            "line_items": [
                {"item_name": "Rau muống", "item_quantity": 2, "item_price": 10000, "measurement_unit": "bó"},
                {"item_name": "Túi nilon", "item_quantity": 1, "item_price": 5000},
            ],
        }
        header, lines, meta = format_receipt_to_relational_v2(data, category_id=2, current_index=1)
        self.assertEqual(len(header), 14)
        for lr in lines:
            self.assertEqual(len(lr), 14)
        self.assertEqual(lines[0][12], "NL_TP")
        self.assertEqual(lines[0][13], "rule:keyword")
        self.assertEqual(lines[1][12], "HH_BAN")

        # DT1 summary line: mixed-goods order → default HH_BAN (source rule:category)
        dt1 = {
            "merchant_name": "SPX Express",
            "order_id": "X1",
            "total_amount": 100000,
            "line_items": [{"item_name": "Dâu sấy", "item_quantity": 1, "item_price": 100000}],
        }
        _, l1, _ = format_receipt_to_relational_v2(dt1, category_id=1, current_index=1)
        self.assertEqual(len(l1), 1)
        self.assertEqual(len(l1[0]), 14)
        self.assertEqual(l1[0][12], "HH_BAN")
        self.assertEqual(l1[0][13], "rule:category")

    def test_dt1_line_item_only_three_groups(self):
        # DT1: only DV_SAN / DV_VC (by keyword) or HH_BAN (default). Never NL_TP/CCDC_TS/etc.
        self.assertEqual(classify_line_item("Phí cố định Shopee tháng 1", is_dt1=True),
                         ("DV_SAN", "rule:keyword"))
        self.assertEqual(classify_line_item("Phí vận chuyển đơn ABC", is_dt1=True),
                         ("DV_VC", "rule:keyword"))
        # A produce name that WOULD be NL_TP for a normal line stays HH_BAN for DT1
        self.assertEqual(classify_line_item("- Thịt bò khô (SL: 2)- Rau củ sấy (SL: 1)", is_dt1=True),
                         ("HH_BAN", "rule:category"))
        # Empty name → still HH_BAN for DT1 (not CAN_SOAT)
        self.assertEqual(classify_line_item("", is_dt1=True), ("HH_BAN", "rule:category"))

    def test_duplicate_checker_v2(self):
        checker = DuplicateChecker()
        checker.add_code("DT20001")
        checker.add_code("HD-99999")
        checker.add_code("SPXVN064620286071")

        is_dup, msg = checker.check_duplicate("DT20001")
        self.assertTrue(is_dup)

        is_dup, msg = checker.check_duplicate("hd-99999") # Case insensitive
        self.assertTrue(is_dup)

        is_dup, msg = checker.check_duplicate("HD-NEW-12345")
        self.assertFalse(is_dup)

if __name__ == "__main__":
    unittest.main()
