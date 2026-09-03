# Regression ledger — "đừng phá lại lỗi cũ"

Bảng cô đọng các lỗi đã sửa và cơ chế bảo vệ, rút từ `Project_report` §3.5–§3.22
và `docs/decisions/*`. **Đây là nguồn tra cứu nhanh**; bản kể chi tiết vẫn ở
`Project_report`. Khi sửa code chạm các vùng dưới, phải giữ nguyên guard tương ứng.

Ký hiệu cột: **Triệu chứng** → **Nguyên nhân gốc** → **Guard / Bất biến phải giữ** → **Ref**.

## Ghi Sheet — mất / ghi đè dữ liệu

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| `DT10001/DT10002/DT20079/DT40067` bị nhân đôi (duplicate) | Ghi song song (dual export) vừa `Data_*_V2` vừa `Bang_Ke_Hoa_Don`; fallback đọc lịch sử từ bảng phẳng gây lệch mã | **Chỉ ghi/đọc `Data_Header_V2` + `Data_Lines_V2`.** `get_max_indexes_by_category` / `get_historical_records` không đọc `Bang_Ke_Hoa_Don`. Không có nhánh fallback bảng phẳng | §3.14 |
| Tái xuất hiện tham chiếu bảng phẳng V1 | 2 tab `Bang_Ke_Hoa_Don` / `Links_Hoa_Don` đã bị **gỡ bỏ hoàn toàn** (2026-09-03): archive `_ARCHIVE_*_20260903` + ẩn, rồi xóa. Gỡ endpoint `/sheets/deduplicate` + `/sheets/migrate-v2`, hàm `append_rows_to_sheet` / `append_links_to_sheet` / `deduplicate_all_sheets`, hằng `STANDARD_FLAT_V1_HEADERS`, biến `.env` `GOOGLE_SHEET_NAME` / `GOOGLE_SHEET_LINKS_NAME` | **Không** thêm lại tab phẳng, endpoint, hàm hay biến env kể trên. `overwrite_sheet_data` chỉ nhận `Data_Header_V2` / `Data_Lines_V2`. Un-archive khẩn: `ops/scripts/archive_flat_v1_tabs.py --revert` | §3.14 |
| Cập nhật hóa đơn đa dòng (DT2/3/4) bị nén về 1 dòng | `update_sheet_row` tự ép danh sách mặt hàng về 1 | `update_sheet_row` tra cứu & bảo tồn toàn bộ `line_rows` hiện có trên `Data_Lines_V2`; DT1 mới giữ quy tắc 1 dòng | §3.13.1 |
| Mất dòng khi cập nhật (`clear A1:Z10000`) | `update_relational_record` xóa trắng sheet trước khi ghi lại | Bỏ hẳn `clear(...)`. Ghi đè mảng trực tiếp (`update A1`), chỉ dọn dòng thừa ở đuôi. **Length Guard**: ngắt + báo lỗi nếu số dòng giảm đột ngột > 20 mà không chủ đích | §3.13.3 |
| Nút "Cập nhật lên Sheet" (verify.html) làm hỏng dòng hóa đơn khác | Ghi lại toàn bảng không phân đoạn | **Exact Math Row Guard**: `new_total == old_total − old_rows_of_dt + new_rows_of_dt`, sai → rollback. **Phân đoạn xác định**: `L_final = [Header] + L_other + L_new` | §3.15.2, §3.16.1 |
| Thêm/bớt mặt hàng gây xung đột đếm dòng | Cập nhật Header đụng cả vùng Lines | Header chỉ cập nhật đúng dải `A{sheet_row}:N{sheet_row}`. Luôn bảo toàn Cột L (link Drive) và Cột N (cờ duyệt 'x') | §3.16.2 |
| Đổi "Loại Form" ghi đè hóa đơn cũ (đè lên mã đang có) | Payload client mang mã giả; giá trị tĩnh `DT10001` trong `verify.html` | **Primary Key Anchor Guard**: Cột A của `header_row` và mọi `line_rows` luôn `= clean_dt_code(dt_code)`; không tin mã từ payload. Test: `test_category_migration_and_key_guard` | §3.20.1, §3.20.4 |

## Cấp mã đối tượng `DTnXXXX`

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| Đổi nhóm hóa đơn (`DTX→DTY`) giữ nguyên đuôi số cũ → đè mã nhóm đích | Không tra MAX nhóm đích | `PUT /api/v1/sheets/record`: `new_dt_code = DTY(MAX+1)` tra **live từ Sheet**; xóa bản ghi cũ (`delete_drive_file=False`); `append_relational_v2` bản ghi mới. Bảo tồn `drive_link` + `confirmed_status` | §3.9, §3.20.3 |
| Mã không liên tục / trùng khi quét song song | Cấp mã từ state in-memory | Chỉ luồng ghi Sheet lấy `sheet_write_lock`; cấp mã đọc trực tiếp cột A | §3.7.2, §3.14 |

## Chống trùng lặp (Duplicate Guard)

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| Không phát hiện trùng Order ID / Tracking của DT1 khi hóa đơn đã duyệt 'x' | Đọc nhầm Cột M (Index 12, Ghi chú) thành Cột N (Xác nhận) → lệch index | Ghi chú chung = Cột M = index 12. Nạp đủ Order ID + Tracking vào bộ lọc trùng kể cả khi đã duyệt | §3.14.2 |

## Đối soát sổ sách (Reconciliation)

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| Trừ chiết khấu 2 lần (siêu thị MM Mega Market…) | Chiết khấu đã trừ ở dòng nhưng Header vẫn ghi tổng CK | **Line Net Discount Heuristic**: nếu `sum(Lines.Thành_tiền)` khớp Header hơn công thức chuẩn (trong dung sai) → dùng `Expected = sum(Lines)` không trừ CK Header lần 2 | §3.12.4, ADR-0002 |
| Chiết khấu đơn vị bị tính như CK tổng dòng | Không phân loại CK | `compute_line_figures()`: `disc_raw < đơn_giá×0.99` **và** `SL>1` → CK đơn vị → `×SL`; ngược lại CK tổng dòng. Ưu tiên `disc_rate%`. Chỉ in-memory | §3.12.5, ADR-0003 |
| Hóa đơn làm tròn vài trăm đồng bị gắn cờ lỗi | Ngưỡng dung sai cứng | Hợp lệ khi **đồng thời** `|Δ| ≤ RECON_TOLERANCE_VND` (1.000) **và** `≤ RECON_TOLERANCE_PCT` (0,5%). NCC lệch lặp ≥ 2 lần → hủy bỏ-qua, gắn `PENDING_REVIEW` | §3.11.3, §3.12.6 |
| DT1 khuyết Lines không đối soát được | Không có dòng chi tiết | DT1 đối soát nội bộ Header: `Tổng_TT == Tiền_hàng − CK + VAT` | §3.12.6 (DT1) |
| Đối soát chạy trúng lúc engine OCR đang ghi | Không kiểm tra khóa | Engine kiểm `sheet_write_lock` trước khi chạy, chờ tối đa 10s rồi báo bận. Báo cáo ghi ra tab riêng (`Reconciliation_Report_*`, `Recon_Audit_Log`), read-only với bảng gốc | §3.12.1, §3.12.8 |
| Phân loại sai kỳ sau khi đổi format ngày Cột B | `parse_period()` không nhận `HH:MM:SS DD-MM-YYYY` | `parse_period()` regex đa năng trích `YYYY` từ chuỗi chuẩn mới | §3.18.3 |

## Định dạng & kiểu dữ liệu

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| `=SUM()` / Pivot lỗi trên cột tiền | Lưu chuỗi `"150.000,00 đ"`, `"10%"`, `"387.997"`, `"1,00"`, `"0,"` (kiểu VN) | Cột tiền Header G–J và Lines F,G,J,K + số lượng Lines D = số thuần (`int`/`float`), `round(x,2)`, không `đ`/`%`/khoảng trắng. `ops/scripts/format_accounting_numbers.py` chuẩn hóa 1 lần (targeted range write, KHÔNG `clear`, Exact Row Guard, `--dry-run`). Number format: tiền `#,##0;(#,##0)` (âm trong ngoặc), % `0.##`, SL `#,##0.##`. Đã chạy 2026-09-03 (backup `20260902_194146_*`) | ADR-001, §3.16.3 |
| Google Sheet ép kiểu chuỗi ngày → sai hiển thị | Không có tiền tố | Cột B chuẩn `'HH:MM:SS DD-MM-YYYY'`, khuyết giờ điền `00:00:00`; gắn tiền tố `'`. Chuẩn hóa qua `normalize_datetime_vn` (`services/business_rules.py`) | §3.18.1, §3.18.2 |
| Migrate format ngày làm hỏng cột khác | Ghi rộng | Chỉ đụng `Data_Header_V2!B2:B{N}`; snapshot trước khi chạy (`ops/migrations/migrate_header_datetime_format.py`) | §3.18.4 |

## Xóa hóa đơn / chuyển nhóm

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| `NameError: deleted_m` khi xóa trên verify.html | Trường legacy `deleted_legacy_main/links` trong return của `delete_sheet_record` | Return chuẩn V2: `success`, `deleted_header_rows`, `deleted_lines_rows`, `deleted_drive_files`, `message` | §3.19.1, §3.19.2 |
| Mất ảnh Drive khi đổi nhóm | Xóa file Drive lúc `changeCategory` | `delete_drive_file=False` khi chuyển nhóm; chỉ xóa ảnh khi user bấm "Xóa Khỏi Sheet" có chủ đích | §3.19.3 |

## Frontend — xem hóa đơn

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| Chọn `DT20279` nhưng hiện ảnh `DT20002` (ảnh ma) | Race condition phản hồi bất đồng bộ đến trễ | `activeViewerRequestId` tăng mỗi lần chọn; `fetch HEAD` / `onload` / `onerror` phải check `currentRequestId === activeViewerRequestId && currentSelectedDt === dtCode` trước khi cập nhật DOM. Wipe `preview.src=''` ngay khi chọn | §3.22.1, §3.22.2 |
| Khung PDF bị co ép 50%, chồng giao diện | Flex row chia đôi; state toolbar rò rỉ | `setVerifyViewerMode`: 3 chế độ `empty`/`image`/`pdf` loại trừ nhau; mặc định `d-none` cho `#imageToolbar` + `#imageViewport` | §3.21.1, §3.21.2 |
| Link ảnh khuyết sau migrate / với format 13 cột cũ | Chỉ đọc 1 vị trí cột | `get_historical_records` phân giải: Cột L (idx 11) → Cột K (idx 10) → quét URL trong hàng. **Tầng tra `Links_Hoa_Don` đã gỡ** (2026-09-03) — mọi dòng Header_V2 nay đều tự mang link ở Cột L. Test `test_historical_records_drive_link_resolution` bỏ mock `Links_Hoa_Don` | §3.22.3 |
| Sửa `app.js` nhưng trình duyệt xài bản cache cũ | Query `?v=` hardcode, phải chạy `init_frontend.py` thủ công | `nginx.conf` gửi `Cache-Control: no-cache` cho `*.js/*.css/*.html` → luôn revalidate (304 nếu không đổi). Không cần bump version tay | (thay §cũ) |

## Nhập số thủ công

| Triệu chứng | Nguyên nhân gốc | Guard phải giữ | Ref |
|---|---|---|---|
| Số kế toán gõ tay bị hàm tự tính ghi đè | Không có cờ | Cờ `data-manual-vat`, `data-manual-total` (dòng), `dataset.manual` (Header) → tôn trọng tuyệt đối số user nhập. Đổi dropdown `%VAT` → tự giải phóng cờ | §3.11.2 |

## Nguyên tắc chung (file & luồng) — §3.6, §3.7

- Không xóa/di dời file runtime: `server.py`, `services/*`, `frontend/*`, `Dockerfile`,
  `docker-compose.yml`, `nginx.conf`, `.env*`, `service_account.json`.
- Ghi Sheet **Append-Only**; cấp mã **Monotonic** (MAX+1 tra live); Duplicate Guard
  trích hash set 1 lần đầu phiên; duyệt 1 chạm (xanh/vàng/đỏ) trước khi ghi.
- Script vận hành tái dùng → `ops/`; **không** để script `audit_*`/`fix_*` ở repo root.
