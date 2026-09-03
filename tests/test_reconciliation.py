import unittest
from services.reconciliation_engine import (
    NormalizedEntry,
    MatchResult,
    BreakFlag,
    match_entry,
    normalize_all_entries,
    identify_repeated_companies,
    compute_line_figures
)

class TestReconciliation(unittest.TestCase):

    def test_exact_match(self):
        # DT20001: Header = Lines -> MATCHED
        entry = NormalizedEntry(
            dt_code="DT20001",
            dt_group=2,
            company="WinMart",
            period="202608",
            header_raw=100000.0,
            header_total=110000.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=2,
            invoice_number="HD-001",
            has_header=True,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.MATCHED)
        self.assertEqual(res.discrepancy, 0.0)

    def test_rounding_break(self):
        # DT20002: diff = 500đ -> ROUNDING_BREAK (since 500 <= 1000 and 500/100500 = 0.0049 <= 0.005)
        entry = NormalizedEntry(
            dt_code="DT20002",
            dt_group=2,
            company="WinMart",
            period="202608",
            header_raw=100000.0,
            header_total=110500.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=1,
            invoice_number="HD-002",
            has_header=True,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.ROUNDING_BREAK)
        self.assertEqual(res.discrepancy, 500.0)

    def test_real_discrepancy(self):
        # DT20003: diff = 5000đ -> REAL_DISCREPANCY
        entry = NormalizedEntry(
            dt_code="DT20003",
            dt_group=2,
            company="WinMart",
            period="202608",
            header_raw=100000.0,
            header_total=115000.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=1,
            invoice_number="HD-003",
            has_header=True,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.REAL_DISCREPANCY)
        self.assertEqual(res.discrepancy, 5000.0)

    def test_missing_header(self):
        entry = NormalizedEntry(
            dt_code="DT20004",
            dt_group=2,
            company="Missing Header",
            period="UNKNOWN",
            header_raw=0.0,
            header_total=0.0,
            header_discount=0.0,
            header_vat=0.0,
            lines_raw=50000.0,
            lines_vat=5000.0,
            lines_total=55000.0,
            lines_count=1,
            invoice_number="",
            has_header=False,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.MISSING_HEADER_BREAK)

    def test_missing_lines_dt2(self):
        entry = NormalizedEntry(
            dt_code="DT20005",
            dt_group=2,
            company="WinMart",
            period="202608",
            header_raw=50000.0,
            header_total=55000.0,
            header_discount=0.0,
            header_vat=5000.0,
            lines_raw=0.0,
            lines_vat=0.0,
            lines_total=0.0,
            lines_count=0,
            invoice_number="HD-005",
            has_header=True,
            has_lines=False
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.MISSING_LINES_BREAK)

    def test_dt1_internal_match(self):
        # Category 1 without Lines: matches internal header math
        # expected = raw (100k) - disc (10k) + vat (10k) = 100k
        entry = NormalizedEntry(
            dt_code="DT10001",
            dt_group=1,
            company="Shopee",
            period="202608",
            header_raw=100000.0,
            header_total=100000.0,
            header_discount=10000.0,
            header_vat=10000.0,
            lines_raw=0.0,
            lines_vat=0.0,
            lines_total=0.0,
            lines_count=0,
            invoice_number="SP-001",
            has_header=True,
            has_lines=False
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        self.assertEqual(res.flag, BreakFlag.MATCHED)

    def test_repeat_pattern_flag(self):
        # CoopMart has discrepancy under tolerance (500đ) but is in repeated_companies
        entry = NormalizedEntry(
            dt_code="DT20006",
            dt_group=2,
            company="CoopMart",
            period="202608",
            header_raw=100000.0,
            header_total=110500.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=1,
            invoice_number="HD-006",
            has_header=True,
            has_lines=True
        )
        repeated = {"CoopMart"}
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005, repeated_companies=repeated)
        self.assertEqual(res.flag, BreakFlag.PENDING_REVIEW)
        self.assertIn("Sai lệch lặp lại từ cùng nhà cung cấp", res.message)

    def test_cross_period_duplicate(self):
        entry = NormalizedEntry(
            dt_code="DT20007",
            dt_group=2,
            company="WinMart",
            period="202608",
            header_raw=100000.0,
            header_total=110000.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=1,
            invoice_number="HD-999",
            has_header=True,
            has_lines=True
        )
        # Duplicate entry exists in period 202607
        other_entry = NormalizedEntry(
            dt_code="DT20001",
            dt_group=2,
            company="WinMart",
            period="202607",
            header_raw=100000.0,
            header_total=110000.0,
            header_discount=0.0,
            header_vat=10000.0,
            lines_raw=100000.0,
            lines_vat=10000.0,
            lines_total=110000.0,
            lines_count=1,
            invoice_number="HD-999",
            has_header=True,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005, all_invoices=[entry, other_entry])
        self.assertEqual(res.flag, BreakFlag.CROSS_PERIOD_DUPLICATE)

    def test_pre_deducted_discount(self):
        # Case representing MM Mega Market (DT20041/DT20047/DT20004)
        # where lines_total is already net of discount, matching header_total,
        # but header still declares the discount.
        entry = NormalizedEntry(
            dt_code="DT20008",
            dt_group=2,
            company="MM Mega Market",
            period="202608",
            header_raw=100000.0,
            header_total=90000.0,      # Header final is 90k
            header_discount=10000.0,    # discount is 10k
            header_vat=10000.0,
            lines_raw=90000.0,
            lines_vat=10000.0,
            lines_total=90000.0,        # lines sum is already 90k (net)
            lines_count=1,
            invoice_number="HD-008",
            has_header=True,
            has_lines=True
        )
        res = match_entry(entry, tolerance_vnd=1000, tolerance_pct=0.005)
        # Should detect that lines_total matches header_total directly
        # and treat it as MATCHED instead of double deducting to standard_expected (80k)
        self.assertEqual(res.flag, BreakFlag.MATCHED)
        self.assertEqual(res.expected_total, 90000.0)

    # ------------------------------------------------------------------ #
    # Tests for compute_line_figures (Line Auto-Compute)
    # ------------------------------------------------------------------ #

    def test_unit_discount_auto_compute(self):
        """
        Quy ước v2.14+: disc_raw luôn là Tổng CK Mặt Hàng cho cả dòng (không tự nhân SL).
        SL=50, Giá=55.000, Tổng CK MH=850.000.
        disc_total = 850.000 (dùng trực tiếp).
        Thành tiền = 50 × 55.000 - 850.000 = 1.900.000.
        """
        gross, disc_total, computed_total, was_corrected, note = compute_line_figures(
            qty=50.0,
            price=55000.0,
            disc_raw=850000.0,
            disc_rate=0.0,
            vat_rate=0.0,
            vat_amt=0.0,
            row_total=1900000.0,
            dt_code="DT20057"
        )
        self.assertEqual(gross, 2750000.0)           # 50 × 55.000
        self.assertEqual(disc_total, 850000.0)        # Tổng CK MH dùng trực tiếp
        self.assertEqual(computed_total, 1900000.0)   # 2.750.000 - 850.000
        self.assertFalse(was_corrected)               # khớp row_total gốc

    def test_order_discount_no_multiply(self):
        """
        Chiết khấu tổng đơn hàng: CK = 500.000 cho toàn bộ dòng (giá = 200.000, SL = 5).
        disc_raw (500.000) >= price × 0.99 (198.000) → không nhân thêm qty.
        Thành tiền = 5 × 200.000 - 500.000 = 500.000.
        """
        gross, disc_total, computed_total, was_corrected, note = compute_line_figures(
            qty=5.0,
            price=200000.0,
            disc_raw=500000.0,  # lớn hơn price → chắc chắn là CK tổng dòng
            disc_rate=0.0,
            vat_rate=0.0,
            vat_amt=0.0,
            row_total=500000.0,
            dt_code="DT20099"
        )
        self.assertEqual(gross, 1000000.0)        # 5 × 200.000
        self.assertEqual(disc_total, 500000.0)    # không nhân: dùng nguyên disc_raw
        self.assertEqual(computed_total, 500000.0)# 1.000.000 - 500.000
        self.assertFalse(was_corrected)           # khớp với row_total gốc

if __name__ == "__main__":
    unittest.main()
