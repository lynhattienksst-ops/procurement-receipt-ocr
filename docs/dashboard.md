# Dashboard — tab `Dashboard` trong Google Sheet

Bảng điều khiển phân tích nằm ngay trong spreadsheet đang chạy, tab **`Dashboard`**.
Dựng bằng script `ops/scripts/build_dashboard_tab.py`. Số liệu **động** — tab chỉ chứa
công thức (`QUERY` / `SUMIF` / `SORTN`...) tham chiếu `Data_Header_V2` / `Data_Lines_V2`
/ `Reconciliation_Report_ALL` / `Recon_Audit_Log`, nên scan thêm hóa đơn hoặc chạy
đối soát xong thì Dashboard tự cập nhật. Chạy lại script chỉ để **dựng lại bố cục / biểu đồ**.

Thiết kế theo triết lý dashboard hàng đầu (NN/g · Tableau · Cleveland & McGill · Tufte)
và tỷ lệ khung NGANG một-màn-hình (kiểu marketing dashboard template).

## Chạy

```bash
docker exec procurement-server python ops/scripts/build_dashboard_tab.py --dry-run  # xem bố cục, không ghi
docker exec procurement-server python ops/scripts/build_dashboard_tab.py            # dựng / dựng lại
```

An toàn (giữ Critical Invariants — xem `.claude/rules/design.md`):

- Chỉ đụng tab `Dashboard` + vùng ẩn phụ trợ của nó (cột `AA:AK`). **Không** đọc-để-ghi,
  clear, hay sửa bảng gốc / tab nào khác.
- Idempotent: chạy lần 2 → RESET định dạng toàn vùng (`values.clear` không xóa format cũ),
  clear `Dashboard!A1:AL170`, xóa mọi chart + banding + merge cũ, rồi ghi lại.
- Lấy `sheet_write_lock` (import từ `server.py`) quanh mọi thao tác ghi.
- Tự đọc `locale` spreadsheet để chọn dấu phân cách công thức (`;` cho `vi_VN`).

## Bố cục (khung ngang)

| Vùng | Hàng | Nội dung |
|---|---|---|
| Tiêu đề | 1–2 | Tên bảng + ghi chú |
| **KPI** | 4–6 | 6 thẻ số lớn căn trái (F-pattern): Tổng thanh toán · Tiền hàng gốc · Thuế VAT · Chiết khấu · Số HĐ · Số NCC |
| **PHÂN TÍCH** | 8 | Nhãn khu vực. Biểu đồ overlay: |
| | | TRÁI (cột A): **LINE** *Xu hướng chi theo tháng* + **BAR** *Top 12 nhà cung cấp* |
| | | PHẢI (cột H): **BAR** *Chi theo nhóm đối tượng* · *Cơ cấu chi theo nhóm hàng* · *Tính chính xác phân loại (số dòng)* · *Phân bố Loại Break* |
| **SỐ LIỆU CHI TIẾT** | 56 | Nhãn khu vực. 8 bảng nguồn, **mỗi bảng có tên khớp tên biểu đồ**: |
| | | Hàng 1 (r59–81): Xu hướng chi theo tháng (A:C) · Cơ cấu nhóm hàng (E:G) · Tính chính xác phân loại (I:K) · Phân bố Loại Break (M:N) |
| | | Hàng 2 (r83–114): Chi theo nhóm đối tượng (A:B) · Top 12 NCC (A:B) · Danh sách CAN_SOAT (E:G) · Lịch sử chạy đối soát (I:M) |
| **CÁCH ĐỌC** | 116 | Nhãn khu vực. 6 khối chú giải dạng LIST (văn phong viết, cho cấp quản lý), 2 cột: TRÁI cột A:F · PHẢI cột H:N. Mỗi khối: tiêu đề đậm + mỗi yếu tố 1 dòng. Giữ mã tiếng Anh như trên biểu đồ (DT1–DT4, `rule:keyword`, `MATCHED`...). |
| Vùng ẩn | AA:AD | `_thang` (YYYY-MM từ cột B, chỉ 2024-01..2026-12) · `_tong_tt` (J) · `_vat` (I) · `_nhom_dt` (LEFT(A,3)) |
| Vùng ẩn | AF:AK | Cho bảng "Lịch sử chạy đối soát": `_ky` chuẩn hóa (6 chữ số → "Tháng MM/YYYY"; 4 chữ số → "Cả năm YYYY"; khác → giữ nguyên) · `_thoigian` (cột B, để SORT) · `_tonghd`/`_khop`/`_lech_lt`/`_lech_tt` (E/F/G/H) |

**Biểu đồ**: 1 LINE (xu hướng thời gian) + 5 BAR ngang (mọi so sánh giữa nhóm; sắp theo
giá trị, trục từ 0). **KHÔNG pie chart** (Cleveland & McGill). 1 màu nhấn navy, ẩn lưới
ô, kẻ mảnh, zebra nhẹ.

## Lỗi đã xử lý (từng vòng self-screenshot)

- `values.clear` không xóa định dạng cũ → thêm bước RESET định dạng toàn vùng.
- Vùng chết cột D–H → thu lưới, dời chú giải + biểu đồ.
- Chú giải lệch (ô hẹp, chiều cao < text wrap, mép biểu đồ đè) → gộp ô + mục "CÁCH ĐỌC" riêng ở đáy.
- KPI tràn cột → rút nhãn, font 13pt, cột 128px.
- Biểu đồ Top 12 "ăn" mất dòng đầu → thêm header đúng.
- LINE hút nhầm rows TẦNG 2 → thu nguồn về đúng phạm vi.
- Tháng rác `2025-20` → lọc `MM` 01..12 ở công thức phụ trợ.
- Cột "Kỳ" của "Lịch sử chạy đối soát": định dạng lẫn lộn (`202603` vs `2026`) + trùng
  dòng (mỗi kỳ chạy nhiều lần) → vùng phụ trợ AF:AK chuẩn hóa Kỳ + `SORT` theo thời gian
  + `SORTN` chế độ 2 (giữ lần chạy mới nhất mỗi kỳ).

## Muốn dùng Looker Studio?

Trỏ Looker Studio vào **tab `Dashboard`** này như một data source — các bảng đã pivot sẵn.
Hoặc kết nối thẳng bảng gốc và tự dựng (cần trường tính `Ngày`, `Nhóm đối tượng`,
`Nhóm hàng (nhãn)` — công thức xem git history của file này).

## Phụ lục — ánh xạ cột nguồn

### `Data_Header_V2` (A→N)
A Mã đối tượng · B Ngày (`'HH:MM:SS DD-MM-YYYY`) · C Tên công ty · D Địa chỉ bên bán ·
E Địa chỉ bên nhận · F Mã hóa đơn · G Tổng tiền hàng gốc · H Chiết khấu · I Thuế VAT ·
J Tổng Thanh Toán · K Người mua/nhận · L Link ảnh · M Ghi chú · N Xác nhận (`x`).

### `Data_Lines_V2` (A→N)
A Mã đối tượng · B Mã SP · C Tên hàng hóa · D Số lượng · E Đơn vị tính · F Đơn giá ·
G Chiết khấu MH · H Tỷ lệ CK % · I Thuế suất VAT % · J Tiền thuế VAT · K Thành tiền ·
L Ghi chú MH · M Nhóm hàng (`NL_TP` `HH_BAN` `CCDC_TS` `DV_VC` `DV_SAN` `DV_KHAC`
`VP_PHAM` `KHAC` `CAN_SOAT`) · N Nguồn phân loại.

### `Reconciliation_Report_ALL`

Cột F "Loại Break" (`MATCHED` / `ROUNDING_BREAK` / `REAL_DISCREPANCY` / `PENDING_REVIEW` / ...).

### `Recon_Audit_Log`

C Kỳ đối soát · B Thời gian chạy · E/F/G/H Tổng HĐ / Khớp / Lệch làm tròn / Lệch thật.
