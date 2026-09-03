# DRAFT — Việc A: Phân loại loại-hàng ở cấp dòng (`Data_Lines_V2`)

> **Trạng thái:** ĐÃ DUYỆT danh mục (2026-09-03) — đang code.
> Khi hoàn tất, đổi tên thành `docs/decisions/000X-line-item-grouping.md` (ADR chính thức).
> Người soạn: phiên làm việc trước (context: vừa hoàn tất gỡ bảng phẳng V1).
>
> **Quyết định chủ dự án (2026-09-03):**
>
> 1. Danh mục nhóm: **giữ nguyên 8+1** (`NL_TP`, `HH_BAN`, `CCDC_TS`, `DV_VC`, `DV_SAN`,
>    `DV_KHAC`, `VP_PHAM`, `KHAC`, `CAN_SOAT`).
> 2. Cột N **CÓ** → `Data_Lines_V2` thành **14 cột A→N**. Tên cột N = **"Nguồn phân loại"**.
> 3. Sửa tay nhóm hàng trên `verify.html`: **để sau** (đợt riêng). Đợt này chỉ
>    schema + `classify_line_item` + backfill + báo cáo BI + sửa doc.
> 4. **DT1 = dòng gộp cả đơn → chỉ 3 nhóm**: `DV_SAN` / `DV_VC` (theo từ khóa) hoặc
>    `HH_BAN` (mặc định — tiền hàng hóa mua vào, nguồn `rule:category`). Không cố phân
>    NL_TP/CCDC_TS/... cho dòng gộp DT1 vì lý do kế toán (3 loại chứng từ/chi phí tách
>    bạch được: hàng hóa TK 156, phí vận chuyển TK 641/627, phí sàn TK 641 + hóa đơn
>    VAT riêng của sàn).
> 5. Ngưỡng dừng tinh keyword: **KHAC + CAN_SOAT < 25%** tổng dòng.

---

## Nhật ký triển khai (2026-09-03)

- **Backup trước backfill:** `ops/backups/20260902_192920_*` (Header 752 dòng, Lines 2531
  dòng) + `20260902_192920_README.md` giải thích lý do. Lúc chụp Lines còn 12 cột A→L.
- **Schema:** `STANDARD_LINES_V2_HEADERS` (`services/google_service.py`) += `"Nhóm hàng"`,
  `"Nguồn phân loại"` → 14 phần tử. Mọi chỗ pad Lines nâng 12→14.
- **Hàm mới:** `classify_line_item(name, unit, is_dt1=False)` +
  `LINE_GROUP_KEYWORDS`/`LINE_GROUP_CODES`/`LINE_GROUP_PRIORITY` trong
  `services/business_rules.py` (1 nguồn sự thật keyword).
- **Đường ghi gắn M/N:** `format_receipt_to_relational_v2` (post-loop), `update_relational_record`
  (`google_service.py`, tôn trọng `source == 'manual'`), `_fill_line_group_columns`
  (`server.py`) cho manual-entry + đổi nhóm.
- **Backfill:** `ops/scripts/backfill_line_groups.py` — `--dry-run` in phân bố; bản thật
  ghi `M1:N1` (tiêu đề) + `M2:N2531` (RAW), Exact Row Guard, lấy `sheet_write_lock`.
- **Tinh keyword:** 4 vòng dry-run. KHAC+CAN_SOAT: 39.9% → 28.0% → 26.0% → **24.6%** (đạt <25%).
- **Test:** +3 test trong `tests/test_v2_architecture.py` (`test_line_item_classification`,
  `test_relational_v2_lines_have_group_columns`, `test_dt1_line_item_only_three_groups`).
  Toàn bộ `python -m pytest -q` = 31 passed.
- **Doc đồng bộ:** `.claude/rules/design.md`, `.claude/CLAUDE.md`, `Project_report` §3.3
  (Lines 12→14 cột), business_rules docstring.

---

## Bối cảnh

Hệ thống phân loại đối tượng hiện tại (`detect_business_category` → DT1–DT4) trộn 3 trục
khác nhau: **kênh mua** (DT1 = sàn TMĐT/vận chuyển), **loại hình bán** (DT2 = bán lẻ, cũng
là thùng chứa mặc định), **ngành hàng** (DT3 = thực phẩm), **chất lượng chứng từ**
(DT4 = viết tay/không rõ). Hệ quả: một hóa đơn thường đúng nhiều nhóm cùng lúc và thứ tự
tầng quyết định thắng, không phải bản chất. Chi tiết hạn chế: xem phần "Hạn chế phân loại
hiện tại" ở cuối file.

Chủ dự án muốn **báo cáo chi phí theo bản chất hàng hóa/dịch vụ** (khớp cách hạch toán
TK 152/153/156/627/641/642, khớp câu hỏi "chi phí theo nhóm hàng" mà sếp sẽ hỏi).

**Quyết định:** KHÔNG đổi `DTnXXXX`, KHÔNG đổi DT1–DT4, KHÔNG cấp lại mã. Thay vào đó thêm
**1 cột phân loại loại-hàng ở cấp DÒNG** (`Data_Lines_V2`), vì một hóa đơn có nhiều loại
hàng cùng lúc → phân loại đúng chỗ là ở cấp dòng, không phải cấp hóa đơn.

---

## Sự thật nền (đã xác minh trong code, 2026-09)

- `Data_Lines_V2` thực tế **12 cột A→L** (không phải 11 như `.claude/rules/design.md` và
  `CLAUDE.md` ghi — đó là lỗi doc, cần sửa luôn trong PR này).
  - A Mã đối tượng · B Mã sản phẩm · C Tên hàng hóa, dịch vụ · D Số lượng ·
    E Đơn vị tính · F Đơn giá · G Chiết khấu mặt hàng · H Tỷ lệ CK (%) ·
    I Thuế suất VAT (%) · J Tiền thuế VAT · K Thành tiền · L Ghi chú mặt hàng
  - Headers: `STANDARD_LINES_V2_HEADERS` trong `services/google_service.py` (12 phần tử).
- `Data_Header_V2` = 14 cột A→N (KHÔNG đụng trong Việc A).
- `format_receipt_to_relational_v2` (`services/business_rules.py`) rẽ nhánh theo `category_id`:
  - `category_id == 1` (DT1): gộp toàn bộ line items về **1 dòng tổng hợp** trên Lines
    (dòng ~854). Chi tiết mặt hàng DT1 vốn KHÔNG có trên Lines — hạn chế sẵn có.
  - `category_id == 3` (DT3): lọc bỏ dòng chứa `(loại bỏ)` / `(loai bo)` (dòng ~800).
  - DT2/DT4 + `elif line_items`: ghi chi tiết từng mặt hàng.
  - Có nhánh tự trích "Phí vận chuyển" từ `notes` → thêm 1 dòng (áp dụng DT2/3/4, dòng ~999).
- `reconciliation_engine.py` so tổng Header vs tổng Lines — KHÔNG phụ thuộc category, nên
  cột mới KHÔNG ảnh hưởng đối soát.
- Cột M của `Data_Lines_V2` hiện đang trống (grid 4943 x 26).
- Dữ liệu hiện tại: ~751 hóa đơn (Header), ~2.531 dòng (Lines).

---

## Critical Invariants áp dụng (bám `.claude/rules/design.md`)

| Ràng buộc | Cách tuân thủ trong Việc A |
|---|---|
| **Append-only writes** | Chỉ ghi cột M (và N nếu có). Backfill dùng **targeted range update `M2:M{N}`**, KHÔNG `clear`, KHÔNG đụng A→L. |
| **Monotonic object codes** | Không đụng cấp mã. |
| **Exact Math Row Guard** | Backfill không thêm/bớt dòng → `new_total_rows == old_total_rows`. Kiểm tra trước khi ghi, abort nếu lệch. |
| **`sheet_write_lock`** | Backfill script + đường ghi mới đều lấy lock. |
| **Cột L link / Cột N Xác nhận Header** (regression §3.16.2) | Không đụng — cột mới nằm sau vùng dữ liệu Lines; Header không đổi. |
| **Không đổi cấu trúc load-bearing khi chưa duyệt** (workflow.md §2) | Đây là THÊM cột, không đổi thứ tự/ý nghĩa A→L, không đổi format số, không đổi mã, không đổi DT1–DT4. Cần chủ dự án duyệt danh mục nhóm bên dưới. |

---

## 1. Danh mục nhóm hàng — CHỜ DUYỆT

Đề xuất 8 nhóm + 1 "cần soát" (trục kế toán chi phí VN). Chủ dự án có thể gộp/tách/đổi tên.

| Mã nhóm | Tên | Bao gồm | ~TK hạch toán |
|---|---|---|---|
| `NL_TP` | Nguyên liệu / thực phẩm tươi | rau củ, thịt, cá, hải sản, trứng, gạo, gia vị, nông sản | 152 / 611 |
| `HH_BAN` | Hàng hóa mua để bán / vật tư tiêu hao | bao bì, túi, hộp, đồ dùng một lần, hàng bán lại | 156 / 152 |
| `CCDC_TS` | Công cụ, dụng cụ & tài sản | xoong nồi, máy móc nhỏ, bàn ghế, thiết bị | 153 / 211 |
| `DV_VC` | Dịch vụ vận chuyển / logistics | phí ship, cước vận chuyển, giao hàng | 641 / 627 |
| `DV_SAN` | Phí sàn TMĐT / hoa hồng | phí Shopee/Lazada/TikTok, phí thanh toán, phí quảng cáo sàn | 641 |
| `DV_KHAC` | Dịch vụ khác | thuê, phần mềm, marketing, sửa chữa, tư vấn, phí ngân hàng | 627 / 642 |
| `VP_PHAM` | Văn phòng phẩm & tiện ích | giấy, bút, mực in, điện, nước, internet | 642 |
| `KHAC` | Khác (đã phân loại, không thuộc trên) | — | — |
| `CAN_SOAT` | Chưa phân loại được / độ tin cậy thấp | hàm không chắc → người soát | — |

**Câu hỏi cần chủ dự án trả lời:**
1. Danh mục trên OK, hay gộp/tách/đổi tên? (VD nhà hàng có thể bỏ `HH_BAN`, hoặc tách
   `NL_TP` thành "tươi sống" vs "khô/đóng gói".)
2. Có thêm cột `Nhóm hàng - nguồn` (cột N của Lines) không? Giá trị: `manual` / `rule:keyword`
   / `unit_hint` / `auto_default` / `low_conf`. **Khuyến nghị: CÓ** (giống `value_source`,
   để lọc dòng máy đoán mà soát định kỳ). Chi phí: 1 cột.
3. Bật sửa tay nhóm hàng trên `verify.html` trong đợt này, hay để sau?

---

## 2. Thay đổi schema

### `Data_Lines_V2`: 12 cột (A→L) → 13 cột (thêm M), hoặc 14 (thêm M + N)

| Col | Field | Ghi chú |
|---|---|---|
| A–L | *(giữ nguyên)* | |
| **M** | **Nhóm hàng** | mã nhóm (`NL_TP`, `DV_VC`, …) |
| **N** | **Nhóm hàng - nguồn** | *(tùy chọn — xem câu hỏi 2)* |

### `Data_Header_V2`: KHÔNG ĐỔI (vẫn 14 cột A→N).

---

## 3. Code — các file chạm

| File | Thay đổi | Rủi ro |
|---|---|---|
| `services/google_service.py` | `STANDARD_LINES_V2_HEADERS` += `"Nhóm hàng"` (+ `"Nhóm hàng - nguồn"`). Chỗ pad dòng Lines tới 12 (trong `update_relational_record` ~dòng 1073: `while len(r_copy) < 12`) → nâng lên 13/14. `append_relational_v2` header-insert. | Thấp — thêm cột cuối |
| `services/business_rules.py` | **Hàm mới** `classify_line_item(item_name: str, unit: str = "") -> tuple[str, str]` → `(group_code, source)`. Dict `LINE_GROUP_KEYWORDS: dict[str, list[str]]` — **1 nguồn sự thật** (đừng lặp vào regex như `CAT*_KEYWORDS` hiện tại). `format_receipt_to_relational_v2`: MỖI `line_row` append `group_code` (+ source) vào cột M (+N). Áp cho **mọi nhánh**: DT1 dòng gộp, DT2/3/4 chi tiết, dòng phí ship auto (gán cứng `DV_VC`). | Vừa — đụng hàm core nhưng chỉ **thêm phần tử cuối** mỗi row, không đổi index A→L |
| `tests/test_business_rules.py` | Cập nhật assertion độ dài row (12→13/14). Thêm `test_line_item_classification` nhiều ca: "Rau muống"→`NL_TP`, "Phí vận chuyển"→`DV_VC`, "Phí sàn Shopee"→`DV_SAN`, "Xoong inox 24cm"→`CCDC_TS`, "Giấy A4"→`VP_PHAM`, tên rỗng→`CAN_SOAT`. | Thấp |
| `tests/test_v2_architecture.py` | Nếu assert số cột Lines → cập nhật. | Thấp |
| **`ops/scripts/backfill_line_groups.py`** (MỚI) | Đọc `Data_Lines_V2`. Với mỗi dòng (bỏ header): tính `classify_line_item(row[2], row[4])`. Ghi kết quả vào `M2:M{N}` (+ `N2:N{N}`) bằng **1 lần `values.update` range đúng**. Exact Row Guard: đọc lại, xác nhận `len` không đổi. `--dry-run` in phân bố nhóm. Lấy `sheet_write_lock` (import từ `server.py`, fallback local lock như `reconciliation_engine.py`). | Vừa — ghi thật lên sheet, nhưng chỉ 1–2 cột, có guard + dry-run + backup trước |
| `frontend/verify.html` + `app.js` | *(Tùy chọn — câu hỏi 3)* Hiển thị cột Nhóm hàng ở bảng chi tiết mặt hàng; cho sửa tay → set nguồn `manual`. | Vừa |
| `.claude/rules/design.md` · `CLAUDE.md` · `Project_report` | Ghi nhận cột mới + trục loại-hàng. **Sửa luôn lỗi doc "11 cột" → "12/13 cột"** cho `Data_Lines_V2`. | Thấp |

---

## 4. Logic `classify_line_item`

```
input:  item_name (Cột C), unit (Cột E)
output: (group_code, source)

1. Chuẩn hóa: lower, gộp khoảng trắng (bỏ dấu là tùy chọn — cân nhắc unidecode).
2. Duyệt LINE_GROUP_KEYWORDS theo THỨ TỰ ƯU TIÊN CỐ ĐỊNH:
   DV_VC → DV_SAN → NL_TP → CCDC_TS → VP_PHAM → DV_KHAC → HH_BAN
   - match từ khóa (word-boundary) → return (group, "rule:keyword")
3. Gợi ý từ đơn vị tính (nếu chưa match):
   - unit ∈ {kg, g, gram, bó, mớ, con, quả, trái, lít, lit, chục, ký} → (NL_TP, "unit_hint")
   - unit ∈ {lần, tháng, gói} → (DV_KHAC, "unit_hint")
4. Không match:
   - item_name rỗng / "đơn hàng tmđt" / "hóa đơn" / "hoá đơn mua hàng" → (CAN_SOAT, "low_conf")
   - ngược lại → (KHAC, "auto_default")
```

### Keyword seed (mở rộng dần qua vòng lặp dry-run)

- `DV_VC`: phí vận chuyển, phí ship, cước, cước vận chuyển, giao hàng, phí giao hàng,
  freight, logistics, phí giao vận, delivery fee, shipping fee
- `DV_SAN`: phí sàn, phí dịch vụ shopee, phí dịch vụ lazada, phí dịch vụ tiktok, hoa hồng,
  phí thanh toán, phí quảng cáo, phí cố định, phí xử lý đơn, phí hạ tầng, commission
- `NL_TP`: rau, củ, quả, trái cây, hoa quả, thịt, heo, bò, gà, cá, tôm, cua, mực, ghẹ,
  trứng, gạo, nếp, gia vị, nước mắm, dầu ăn, hạt nêm, bột ngọt, nấm, hải sản, thủy sản,
  bún, phở, mì, hủ tiếu, đường, muối, sữa tươi, tàu hũ, đậu
- `CCDC_TS`: nồi, chảo, xoong, dao, thớt, tô, chén, đĩa, dĩa, ly, cốc, khay, kệ, bàn, ghế,
  tủ, máy, bếp, quạt, đèn, thiết bị, dụng cụ
- `VP_PHAM`: giấy, bút, viết, mực in, sổ, kẹp, băng keo, băng dính, file, bìa, ghim, phong bì,
  tiền điện, tiền nước, cước internet, hóa đơn điện, hóa đơn nước
- `DV_KHAC`: thuê, cho thuê, phần mềm, license, marketing, quảng cáo, sửa chữa, bảo trì,
  tư vấn, phí ngân hàng, phí chuyển khoản, phí quản lý, dịch vụ vệ sinh
- `HH_BAN`: túi, bao bì, hộp, ly nhựa, ống hút, màng bọc, khăn giấy, nilon, nylon,
  thùng carton, hộp giấy, tem, nhãn

---

## 5. Quy trình triển khai (thứ tự an toàn)

1. **Backup** — `docker exec procurement-server python ops/scripts/backup_sheet_state.py`
   (script này đã được sửa để chụp `Data_Header_V2` + `Data_Lines_V2`).
2. **Code + test xanh** — thêm `classify_line_item`, sửa headers/pad, cập nhật test.
   `docker exec procurement-server python -m pytest -q` → phải 100% pass.
3. **Thêm header cột M** — chèn `"Nhóm hàng"` vào `Data_Lines_V2!M1` (script hoặc tay).
4. **Dry-run backfill** — `python ops/scripts/backfill_line_groups.py --dry-run` → xem
   bảng phân bố nhóm. Soi mẫu `CAN_SOAT` + `KHAC`.
5. **Tinh keyword** — lặp bước 4 tới khi `CAN_SOAT` + `KHAC` < ~10–15% tổng dòng.
6. **Backfill thật** — `python ops/scripts/backfill_line_groups.py`. Guard kiểm số dòng,
   ghi `M2:M{N}` (+ `N2:N{N}`).
7. **Kiểm chứng** — `GET /api/v1/sheets/records` vẫn trả ~751 record không lỗi;
   `verify.html` mở bình thường; `/api/v1/reconcile/run` một kỳ chạy OK.
8. **invariant-reviewer** soi diff (agent `invariant-reviewer`).
9. **Báo cáo BI** — Looker Studio: dimension mới = cột M `Nhóm hàng`.
10. **(Sau, tùy chọn)** Bật sửa tay nhóm hàng trên `verify.html` cho nhóm `CAN_SOAT`.

---

## 6. Rủi ro & giảm thiểu

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Backfill ghi lệch cột, đè A→L | Thấp | Range update tường minh `M2:M{N}` + Exact Row Guard + backup + `--dry-run` |
| `append_relational_v2` pad sai độ dài sau khi +cột → lệch dòng MỚI | Vừa | Test độ dài row; chạy 1 scan thử, kiểm dòng mới trên sheet |
| Phân loại cấp dòng vẫn "1 hóa đơn nhiều nhóm" | Cố hữu | ĐÚNG ở cấp dòng — báo cáo group theo dòng; tổng vẫn khớp Header |
| DT1 gộp 1 dòng → nhóm dòng gộp mơ hồ | Vừa | Với DT1: cột M = `DV_SAN` nếu là phí sàn, ngược lại `CAN_SOAT`. Chi tiết hàng DT1 vốn không có trên Lines (hạn chế sẵn) |
| Keyword phủ sót hàng địa phương | Vừa | Vòng lặp dry-run; cột `nguồn` giúp lọc `auto_default`/`low_conf` soát định kỳ |
| Doc "11 cột" càng lệch | Thấp | Sửa design.md + CLAUDE.md + Project_report trong cùng PR |

---

## 7. KHÔNG làm trong Việc A (tránh phình scope)

- KHÔNG đổi `DTnXXXX`, KHÔNG đổi DT1–DT4, KHÔNG cấp lại mã.
- KHÔNG thêm confidence per-field cho OCR.
- KHÔNG tách Sheet "OCR thô ↔ data mart".
- KHÔNG đụng `reconciliation_engine` (cột M không ảnh hưởng đối soát Header vs Lines).
- KHÔNG phân loại cấp **hóa đơn** theo loại hàng (trục khác — "Việc B", cần ADR riêng +
  kế toán duyệt, có thể kéo theo migration mã).

---

## Phụ lục — Hạn chế phân loại DT1–DT4 hiện tại (để tham khảo khi bàn Việc B sau này)

1. **Trục không nhất quán** — DT1 = kênh, DT2 = loại hình bán + fallback, DT3 = ngành hàng,
   DT4 = chất lượng chứng từ. Hóa đơn đa nhãn → thứ tự tầng thắng, không phải bản chất.
   VD: Cty TNHH thực phẩm bán trên Shopee → DT3 (mất thông tin kênh); Grab có MST → DT2
   (không phải DT1, dù chính sách nói "DT1 = vận chuyển").
2. **DT2 vừa là nhóm vừa là fallback** — không phân biệt "siêu thị thật" với "không biết
   xếp đâu" → ô nhiễm báo cáo.
3. **Keyword cứng, 2 nguồn sự thật** — `CAT*_KEYWORDS` gần như code chết; logic thật nằm ở
   regex trong hàm. Token quá rộng (`"mart"`, `"store"`, `"shop"`, `"tiệm"`).
4. **Regex MST quét `full_text`** — `has_valid_tax_id` khi `seller_tax_id` rỗng sẽ quét
   toàn bộ text tìm 10 chữ số → SĐT / mã đơn / tracking bị nhận nhầm là MST.
5. **Không confidence, không đường thoát** — luôn trả 1 số 1–4, sai được ghi như đúng;
   đổi nhóm sau đó = cấp mã `DTnXXXX` mới (đắt, regression §3.9/§3.20.3).
6. **Bỏ qua tín hiệu mạnh** — không tra MST, không dùng mẫu số/ký hiệu hóa đơn, không dùng
   mã CQT (`e_invoice_code`).
7. **Tin `category_hint` từ OCR gần như tuyệt đối** — model đoán sai hint → sai luôn.
8. **DT4 định nghĩa bằng phủ định** — trộn "viết tay hợp lệ" (cần Bảng kê 01/TNDN) với
   "OCR thất bại" (cần nhập tay lại).
9. **Sai nhóm kéo theo sai format** — DT1 sai → mất chi tiết mặt hàng; DT3 sai → lọc nhầm
   `(loại bỏ)` hoặc rác lọt vào.
10. **Không test vùng biên** — chỉ test các ca "sạch", không test ca xung đột đa nhãn.
