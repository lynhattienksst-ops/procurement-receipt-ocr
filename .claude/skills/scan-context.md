---
name: scan-context
description: >
  Nạp nhanh toàn bộ bối cảnh vận hành của hệ thống Procurement Receipt OCR trước
  khi làm bất kỳ việc gì với dự án — schema 2 sheet quan hệ V2 (14 cột Header /
  11 cột Lines), quy tắc mã đối tượng DTnXXXX, 4 nhóm DT1–DT4, các bất biến dữ
  liệu, và lịch sử thay đổi theo phiên bản. Dùng khi bắt đầu một phiên làm việc
  liên quan đến trích xuất hóa đơn, phân loại, chống trùng lặp, hoặc cấu hình hệ
  thống.
---

# Bối cảnh vận hành — Procurement Receipt OCR & Automation System

> Đã port từ `.agents/skills/Scan_Hoa_Don/SKILL.md` (bản cũ, đường dẫn `Downloads\Test`,
> xưng hô "Antigravity"). Bản này là bản chính, đi kèm repo.

## ⚠️ Bước bắt buộc đầu tiên

**Trước khi thao tác**, đọc file đặc tả chân lý: **`Project_report`** ở gốc repo
(không có phần mở rộng). Nó là spec liên tục cập nhật, phủ §3.1–§3.22.
Xem thêm `.claude/rules/design.md` và `.claude/rules/workflow.md` (bản tóm tắt).

## 1. Mô hình dữ liệu 2 sheet quan hệ V2

Hệ thống hoạt động **độc quyền** trên mô hình 2 sheet. Bảng phẳng V1
(`Bang_Ke_Hoa_Don`, `Links_Hoa_Don`) đã bị **gỡ bỏ hoàn toàn** (§8, 09/2026):
archive `_ARCHIVE_*_20260903` + ẩn, rồi xóa. Không code path nào còn đọc/ghi chúng.

### `Data_Header_V2` — 14 cột (A→N), một dòng mỗi hóa đơn

| Cột | Trường |
|---|---|
| A | Mã đối tượng (`DTnXXXX`) |
| B | Ngày, tháng, năm |
| C | Tên công ty |
| D | Địa chỉ bên bán |
| E | Địa chỉ bên nhận |
| F | Mã hóa đơn, chứng từ |
| G | Tổng tiền hàng (gốc) — số thuần |
| H | Chiết khấu thương mại — số thuần |
| I | Thuế VAT — số thuần |
| J | Tổng Thanh Toán — số thuần |
| K | Người mua/nhận hàng |
| L | Link ảnh đối soát |
| M | Ghi chú chung |
| N | Xác nhận (cột trạng thái duyệt) |

### `Data_Lines_V2` — 11 cột (A→K), một dòng mỗi mặt hàng, join qua cột A

| Cột | Trường |
|---|---|
| A | Mã đối tượng (khóa join) |
| B | Mã sản phẩm |
| C | Tên hàng hóa, dịch vụ |
| D | Số lượng |
| E | Đơn giá — số thuần |
| F | Chiết khấu mặt hàng — số thuần |
| G | Tỷ lệ chiết khấu (%) — số thuần |
| H | Thuế suất VAT (%) — số thuần |
| I | Tiền thuế VAT — số thuần |
| J | Thành tiền — số thuần |
| K | Ghi chú mặt hàng |

Cột E→K của `Data_Lines_V2` bắt buộc là số học thuần túy (`int`/`float`), không
`đ`, không `%`, không khoảng trắng; ô Sheet đặt định dạng `NUMBER` (`#,##0` / `0.##`).
(ADR-001, `Project_report` §3.18)

Link ảnh Drive nằm ở cột L của **mọi** dòng `Data_Header_V2` (giải trực tiếp,
kèm quét URL toàn hàng cho các dòng 13 cột cũ). Tab `Links_Hoa_Don` đã bị gỡ.

## 2. Mã đối tượng `DTnXXXX`

- `DT` cố định + `n ∈ {1,2,3,4}` (nhóm) + `XXXX` số thứ tự 4 chữ số, bắt đầu `0001`.
- Cấp mã **tăng dần liên tục (monotonic)**: `MAX(mã hiện có trong nhóm DTn tại cột A) + 1`,
  tra **trực tiếp từ Sheet**, không từ bộ nhớ tạm.
- Đổi nhóm hóa đơn ⇒ cấp mã **hoàn toàn mới** trong nhóm đích, không giữ đuôi số cũ;
  bảo toàn 100% link ảnh Drive (`delete_drive_file=False`). (§3.9)

## 3. Bốn nhóm đối tượng

| Nhóm | Phạm vi | Đặc thù trình bày |
|---|---|---|
| DT1 | Sàn TMĐT & dịch vụ vận chuyển | 1 dòng tổng hợp mỗi đơn, thường khuyết Lines |
| DT2 | Siêu thị & bán lẻ (tiện lợi, điện máy, hiệu thuốc…) | đa dòng mặt hàng |
| DT3 | Doanh nghiệp cung ứng thực phẩm | lọc bỏ mặt hàng gắn thẻ `(loại bỏ)` |
| DT4 | Giấy viết tay & không rõ danh tính | đa dòng, dữ liệu OCR yếu |

## 4. Bất biến dữ liệu (không vi phạm — xem `.claude/rules/design.md`)

1. **Append-Only** — không ghi đè/xóa dòng cũ trên `Data_Header_V2` / `Data_Lines_V2`.
2. **Monotonic mã đối tượng** — như mục 2.
3. **Exact Math Row Guard** (§3.15) — trước khi ghi `Data_Lines_V2`:
   `new_total == old_total − old_rows_of_this_dt + new_rows_of_this_dt`, sai thì rollback.
4. **Phân đoạn dòng xác định** (§3.16): `L_final = [Header] + L_other + L_new`;
   cập nhật Header đúng dải `A{row}:N{row}`, không đụng hóa đơn khác.
5. **`sheet_write_lock`** — chỉ luồng ghi Sheet lấy khóa; luồng đọc/OCR chạy song song.
6. **Luồng xác nhận cô lập** (§3.19): `POST /api/v1/sheets/confirm` chỉ sửa cột N Header,
   không đụng Lines; `update_relational_record` không `clear A1:Z10000`.

## 5. Lịch sử phiên bản đáng nhớ (tóm tắt `Project_report` §3.4–§3.22)

- **v2.4.1** — ghép hóa đơn PDF theo tên file: `pdf_base_key` (`filename`/`drive_file_id`)
  gom mọi trang về 1 mã `DTnXXXX` dù trang 2,3 khuyết số hóa đơn. Ưu tiên quét JPEG/PNG trước PDF.
- **v2.6.0** — tải song song (`Semaphore(MAX_CONCURRENT_DOWNLOADS)`=5), OCR song song
  (`Semaphore(MAX_CONCURRENT_OCR)`=3), granular locking.
- **v2.7.0** — mở khóa toàn bộ ô số cho nhập tay; cờ `data-manual-vat`/`data-manual-total`;
  dung sai làm tròn ≤ 1.000 VNĐ trong `checkMathDiscrepancy()`.
- **v2.9.1** — bảo tồn đa dòng DT2/DT3/DT4; Length Guard chống mất dòng.
- **v2.10.0** — cô lập 100% quan hệ V2; sửa lệch index Duplicate Checker ở cột M.
- **v2.11.0** — 4 pipeline độc lập (`process_dt1_pipeline`…`process_dt4_pipeline`)
  điều phối qua `dispatch_pipeline`; Exact Math Row Guard.
- **v2.12.0** — UI đối soát kiểu Excel (`.split-resizer`, `.col-resizer`, toolbar ảnh nổi).
- **v2.13.3–2.13.4** — 3 chế độ xem (`setVerifyViewerMode`: empty/image/pdf);
  token `activeViewerRequestId` chống race condition ảnh ma; phân giải link Drive đa tầng.

## 6. Lệnh vận hành

```powershell
# Khởi chạy (đường dẫn thật)
cd "c:\Users\lynha\Downloads\Cladue code\Scan_Hoa_Don"
docker compose up -d --build
# Dashboard: http://localhost:8080

# Kiểm thử (toàn bộ tests/ chạy dưới pytest)
docker exec procurement-server python -m pytest -q

# Trước thao tác rủi ro: snapshot Sheet (ghi ra ops/backups/, tự giữ 3 bản mới nhất)
docker exec procurement-server python ops/scripts/backup_sheet_state.py

# Quét lại PDF: xóa dữ liệu PDF cũ + đưa file về folder Chua_Xu_Ly
docker exec procurement-server python ops/scripts/reset_processed_pdfs.py
```

## 7. Quy tắc hành động

1. Luôn đọc `Project_report` trước.
2. Xác định rõ yêu cầu (sửa lỗi / thêm tính năng / triển khai / kiểm thử).
3. Tuân thủ cơ chế ghi 2 sheet và các bất biến ở mục 4.
4. **Không tự ý** đổi cấu trúc mã đối tượng, format cột, hay logic phân loại khi
   chưa có yêu cầu rõ ràng từ người dùng.
