# Data Contract — source tables for the dashboard

Authoritative schema source: `.claude/rules/design.md` + `Project_report` §3.
This file is the **data contract** the dashboard service (`dashboard/`) reads
against: for every column it states type, allowed range, nullability, the
source-of-truth writer, and the validation rule the P1 data-quality layer
(`services/data_quality.py`) checks. The dashboard is **read-only** — it never
writes any of these tables.

Legend — Null: `no` = must be present · `derived` = may be empty, computed
downstream · `opt` = legitimately optional.

---

## `Data_Header_V2` — one row per receipt/invoice (14 cols, A→N)

| Col | Field | Type | Range / format | Null | Written by | Validation rule (P1) |
|---|---|---|---|---|---|---|
| A | Mã đối tượng | text | `^DT[1-4]\d{4}$` | no | scan / verify / migration (`format_receipt_to_sheet_rows`, `append_relational_v2`) | matches regex; unique across the tab; group digit ∈ {1,2,3,4} |
| B | Ngày, tháng, năm | datetime text | `'HH:MM:SS DD-MM-YYYY` (leading apostrophe; missing time → `00:00:00`) | no | `normalize_datetime_vn` | parses to a real date; year ∈ [2020, current+1]; month ∈ 1..12 — **invalid dates must be rejected upstream, not filtered at the chart** |
| C | Tên công ty | text | free text, trimmed | no | OCR extract | non-empty; used as the supplier dimension |
| D | Địa chỉ bên bán | text | free text | opt | OCR extract | — |
| E | Địa chỉ bên nhận | text | free text | opt | OCR extract | — |
| F | Mã hóa đơn, chứng từ | text | invoice / receipt / tracking no. | opt | OCR extract | when present, feeds duplicate detection (`clean_dt_code`) |
| G | Tổng tiền hàng (gốc) | number | pure `float`/`int`, `round(x,2)`, ≥ 0, no `đ`/`%`/space | no | `parse_vietnamese_number` + `format_accounting_numbers.py` | numeric; ≥ 0; scale-outlier guard (flag if ≫ dataset p99) |
| H | Chiết khấu thương mại | number | pure numeric, ≥ 0 | derived | business rules | numeric; ≥ 0; ≤ G |
| I | Thuế VAT | number | pure numeric, ≥ 0 | derived | business rules | numeric; ≥ 0 |
| J | Tổng Thanh Toán | number | pure numeric, ≥ 0 | no | business rules | numeric; identity check `J ≈ G − H + I` within `RECON_TOLERANCE_VND` / `_PCT` |
| K | Người mua/nhận hàng | text | free text | opt | OCR extract | — |
| L | Link ảnh đối soát | url | Drive URL | no | scan / verify | non-empty on every row (legacy 13-col rows resolved by whole-row URL scan) |
| M | Ghi chú chung | text | free text (index 12 — **not** the confirm flag) | opt | system / user | — |
| N | Xác nhận | status | `x` or empty | opt | `POST /api/v1/sheets/confirm` (only endpoint that touches N) | value ∈ {`x`, ``} |

Money columns **G–J** must be pure numeric; display format `#,##0;(#,##0)`.

---

## `Data_Lines_V2` — one row per line item (14 cols, A→N)

| Col | Field | Type | Range / format | Null | Written by | Validation rule (P1) |
|---|---|---|---|---|---|---|
| A | Mã đối tượng (join key) | text | `^DT[1-4]\d{4}$` | no | scan / verify / migration | must match an existing `Data_Header_V2!A` (no orphan lines) |
| B | Mã sản phẩm | text | free text | opt | OCR extract | — |
| C | Tên hàng hóa, dịch vụ | text | free text, trimmed | no | OCR extract | non-empty |
| D | Số lượng | number | pure numeric, > 0, format `#,##0.##` | derived | business rules | numeric; > 0 |
| E | Đơn vị tính | text | free text (kg, cái, …) | opt | OCR extract | feeds `unit_hint` classification |
| F | Đơn giá | number | pure numeric, ≥ 0 | derived | `compute_line_figures` | numeric; ≥ 0 |
| G | Chiết khấu mặt hàng | number | pure numeric, ≥ 0 | derived | ADR-0002 auto-detect | numeric; ≥ 0 |
| H | Tỷ lệ chiết khấu (%) | number | pure numeric, 0..100, format `0.##` | derived | ADR-0002 | numeric; 0 ≤ H ≤ 100 |
| I | Thuế suất VAT (%) | number | pure numeric, ∈ {0, 5, 8, 10, …}, format `0.##` | derived | OCR / rules | numeric; 0 ≤ I ≤ 100 |
| J | Tiền thuế VAT | number | pure numeric, ≥ 0 | derived | ADR-0003 auto-compute | numeric; ≥ 0 |
| K | Thành tiền | number | pure numeric, ≥ 0 | no | ADR-0003 | numeric; `Σ K` per code reconciles to Header `J` within tolerance |
| L | Ghi chú mặt hàng | text | free text | opt | system / user | — |
| M | Nhóm hàng | code | ∈ `NL_TP` `HH_BAN` `CCDC_TS` `DV_VC` `DV_SAN` `DV_KHAC` `VP_PHAM` `KHAC` `CAN_SOAT` | no | `classify_line_item` | value ∈ the 9-code set; `CAN_SOAT` volume tracked as a quality metric |
| N | Nguồn phân loại | text | ∈ `manual` `rule:keyword` `rule:category` `unit_hint` `auto_default` `low_conf` | no | `classify_line_item` (`manual` only via verify.html) | value ∈ the 6-value set; `auto_default`+`low_conf` share tracked as data-trust metric |

Columns **F–K** must be pure numeric; DT1 rows are aggregated (few or no detail lines) and reconcile internally on the Header identity.

---

## `Reconciliation_Report_ALL` — engine output (read-only)

| Col | Field | Notes |
|---|---|---|
| A | Mã đối tượng | join key |
| F | Loại Break | `BreakFlag` enum: `MATCHED` · `ROUNDING_BREAK` · `REAL_DISCREPANCY` · `PENDING_REVIEW` · `MISSING_*_BREAK` · `CROSS_PERIOD_DUPLICATE` · `OVERRIDE_BREAK` |
| G | (count / delta fields per engine version) | used for money-weighted exception reporting in P2 |

Produced by `services/reconciliation_engine.py`; tolerances `RECON_TOLERANCE_VND`
(1 000) and `RECON_TOLERANCE_PCT` (0.5%). Written to a dedicated tab — never
mutates Header/Lines.

## `Recon_Audit_Log` — one row per reconciliation run

| Col | Field |
|---|---|
| B | Thời gian chạy (for SORT newest→oldest) |
| C | Kỳ đối soát (`YYYYMM` / `YYYY` / other) |
| E / F / G / H | Tổng HĐ / Khớp / Lệch làm tròn / Lệch thật |

---

## Data-quality metrics the dashboard computes (P1)

| Metric | Definition | Green | Amber | Red |
|---|---|---|---|---|
| Date validity | % Header rows with a parseable date, year in range | ≥ 99.5% | 97–99.5% | < 97% |
| Completeness (core) | % Header rows with A, B, C, J all present | ≥ 99% | 95–99% | < 95% |
| Referential integrity | orphan Lines (no matching Header A) | 0 | 1–5 | > 5 |
| Non-DT1 Header without lines | Header rows (DT2/3/4) with 0 Lines | 0 | 1–3 | > 3 |
| Classification confidence | % Lines with N ∈ {`manual`, `rule:keyword`} | ≥ 70% | 50–70% | < 50% |
| Unclassified backlog | count of Lines with M = `CAN_SOAT` | 0 | 1–30 | > 30 |
| Duplicate rate | flagged duplicates ÷ scanned (tier 1+2) | < 1% | 1–3% | > 3% |
| Reconciliation match rate | `MATCHED` ÷ total reconciled | ≥ 95% | 85–95% | < 85% |
| Scale outliers | Header J values > 100× dataset median | 0 | 1–2 | > 2 |

Thresholds are the P1 starting point — tune during the P1 `Execute → Verify →
Adjust` loop against the real distribution.
