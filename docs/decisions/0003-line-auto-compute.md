# ADR-0003: Line Auto-Compute - Tu Dong Tinh Lai So Lieu Dong Mat Hang

**Date:** 2026-08-24
**Status:** Accepted
**Version:** v2.9.0

## Boi canh

Khi OCR doc hoa don tu mot so nha cung cap (dac biet MM Mega Market), cot Chiet khau (CK) trong Data_Lines_V2 co the chua gia tri chiet khau don vi (giam tren 1 san pham) thay vi chiet khau tong dong.

Vi du: hoa don DT20057 co SL=50, Don gia=55.000, CK=17.000 (don vi) nhung Thanh tien ghi la 2.733.000 (sai) thay vi 1.900.000 (dung).

He thong OCR ghi Thanh tien = SL x Gia - CK = 50x55.000 - 17.000 = 2.733.000 thay vi phai tinh CK_total = 17.000 x 50 = 850.000 -> Thanh tien = 1.900.000.

## Quyet dinh

Bo sung ham compute_line_figures() duoc goi trong buoc Normalize, truoc khi engine cong don lines_total:

1. Phan loai chiet khau (Disc-Type Classifier):
   - Neu co disc_rate% -> dung gross x disc_rate (do chinh xac cao nhat).
   - Neu disc_raw < price x 0.99 VA qty > 1 -> day la CK don vi -> nhan disc_raw x qty.
   - Con lai -> CK tong dong -> dung disc_raw truc tiep.

2. Tinh lai Thanh tien theo chuoi:
   net = gross - disc_total -> vat = net x vat_rate -> total = net + vat

3. Kiem tra nhat quan voi du lieu goc:
   - Neu |computed - row_total| <= 1.000 -> tin vao row_total (tranh sai so lam tron).
   - Neu lech > 1.000 -> dung computed_total va dat co was_corrected = True.

4. Khong ghi de du lieu goc tren Sheet - moi dieu chinh chi ton tai trong bo nho khi Engine chay.

## He qua

- Tu dong sua sai lech CK don vi, khong can sua tay tren Sheet.
- Log canh bao ro rang khi phat hien dieu chinh.
- Toan bo 11 unit tests pass 100%.
- Gioi han: Heuristic disc_raw < price x 0.99 co the sai neu CK tong dong nho hon don gia. Can bo sung cot disc_type tuong minh trong tuong lai neu gap truong hop dac thu.

## Cac tep thay doi

- services/reconciliation_engine.py: Them compute_line_figures(), cap nhat NormalizedEntry, cap nhat normalize_all_entries()
- test_reconciliation.py: Them test_unit_discount_auto_compute, test_order_discount_no_multiply
- Project_report: Nang cap len v2.9.0
- .agents/skills/Reconciliation_DTn/SKILL.md: Cap nhat mo ta buoc Normalize
