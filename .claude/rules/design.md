# Design rules — sheet data model, categories, invariants

Authoritative source: `Project_report` §3. This file is a working summary; when they disagree, `Project_report` wins.

## V2 relational sheet data model

The system moved from a flat single-table sheet to a relational two-table model (`Project_report` §3.14):

- **`Data_Header_V2`** — one row per receipt/invoice, **14 columns (A→N)**.
- **`Data_Lines_V2`** — one row per line item, **14 columns (A→N)**. (A→L are the line data; **M "Nhóm hàng"** + **N "Nguồn phân loại"** were added by the line-item-grouping ADR — see `docs/decisions/*-line-item-grouping.md`.)
- Joined by the object code (`DTnXXXX`) in column A of each.
- Image links live in `Data_Header_V2` column L on every row — resolved directly, with a whole-row URL scan as a safety net for legacy 13-column rows.
- **Legacy flat tabs `Bang_Ke_Hoa_Don` / `Links_Hoa_Don` were retired** (Stage-1 archived as `_ARCHIVE_*_20260903` + hidden, then removed). No code path reads or writes them. Do not re-introduce them; `ops/scripts/archive_flat_v1_tabs.py --revert` un-archives if ever needed.

Canonical column headers are defined in `services/google_service.py` as `STANDARD_HEADER_V2_HEADERS`. `tests/test_business_rules.py` asserts exact column counts and positions — keep it green (`python -m pytest -q`). Schema is pure-numeric V2 (`docs/decisions/ADR-001-pure-numeric-v2-schema.md`).

> Note: `GEMINI.md` and the workspace `AGENTS.md` historically said "10 columns" — that is **outdated**. The live schema is 14 / 14 as below.

### `Data_Header_V2` — 14 columns

| Col | Field | Type |
|---|---|---|
| A | Mã đối tượng (`DTnXXXX`) | text |
| B | Ngày, tháng, năm | datetime (normalized, §3.18) |
| C | Tên công ty | text |
| D | Địa chỉ bên bán | text |
| E | Địa chỉ bên nhận | text |
| F | Mã hóa đơn, chứng từ | text |
| G | Tổng tiền hàng (gốc) | pure numeric |
| H | Chiết khấu thương mại | pure numeric |
| I | Thuế VAT | pure numeric |
| J | Tổng Thanh Toán | pure numeric |
| K | Người mua/nhận hàng | text |
| L | Link ảnh đối soát | url |
| M | Ghi chú chung | text |
| N | Xác nhận | status (only column touched by `POST /api/v1/sheets/confirm`) |

### `Data_Lines_V2` — 14 columns

| Col | Field | Type |
|---|---|---|
| A | Mã đối tượng (join key) | text |
| B | Mã sản phẩm | text |
| C | Tên hàng hóa, dịch vụ | text |
| D | Số lượng | numeric |
| E | Đơn vị tính | text |
| F | Đơn giá | pure numeric |
| G | Chiết khấu mặt hàng | pure numeric |
| H | Tỷ lệ chiết khấu (%) | pure numeric |
| I | Thuế suất VAT (%) | pure numeric |
| J | Tiền thuế VAT | pure numeric |
| K | Thành tiền | pure numeric |
| L | Ghi chú mặt hàng | text |
| M | Nhóm hàng | code (`NL_TP`/`HH_BAN`/`CCDC_TS`/`DV_VC`/`DV_SAN`/`DV_KHAC`/`VP_PHAM`/`KHAC`/`CAN_SOAT`) |
| N | Nguồn phân loại | text (`manual`/`rule:keyword`/`rule:category`/`unit_hint`/`auto_default`/`low_conf`) |

Columns F→K must be pure numeric (`int`/`float`) — no `đ`, `%`, or whitespace — and the Sheet cell format is set to `NUMBER`. VN accounting display format (set by `ops/scripts/format_accounting_numbers.py`): money cols → `#,##0;(#,##0)` (negatives in parentheses); percent cols (Lines H/I) → `0.##`; quantity (Lines D) → `#,##0.##`. Values are rounded `round(x, 2)`. That script re-coerces any drifted string cell (`"387.997"`, `"1,00"`, `"0,"`) back to a real number via targeted range writes (no `clear`, Exact Row Guard). See `Project_report` §3.18 and ADR-001.

Columns M/N (line-item grouping) are populated by `classify_line_item(name, unit)` in `services/business_rules.py` on every write path (scan, verify.html edit, category migration), and were backfilled onto historical rows by `ops/scripts/backfill_line_groups.py`. See `docs/decisions/*-line-item-grouping.md`. They do **not** affect reconciliation (Header-vs-Lines totals only touch A→K).

## The 4 business-object categories

Object codes are sequential per group:

| Code group | Domain |
|---|---|
| `DT1XXXX` | E-commerce / shipping |
| `DT2XXXX` | Retail |
| `DT3XXXX` | Food supply |
| `DT4XXXX` | Handwritten / unidentified |

Classification is keyword-based in `services/business_rules.py` (`detect_business_category`, `CAT1_KEYWORDS`/`CAT2_KEYWORDS`/`CAT3_KEYWORDS`). Per-category row-formatting logic (`format_receipt_to_sheet_rows`) turns one receipt into 1..N sheet rows — e.g. category 3 filters `(loại bỏ)` items. See `Project_report` §3.2 / §3.4.

## Critical Invariants

Hard-won constraints from the project's version history (`Project_report` §3.1, §3.5–§3.20). Violating them silently corrupts or loses live spreadsheet data.

- **Append-only writes** — never overwrite or clear existing rows in `Data_Header_V2` / `Data_Lines_V2`. Only append new rows or do a targeted single-row/range update.
- **Monotonic object codes** — a new `DTnXXXX` code is always `MAX(existing code in that DTn group) + 1`, looked up **live from the sheet** (never from in-memory/cached state). Changing a receipt's category re-issues a brand-new code in the target group; never reuse the old numeric suffix.
- **Exact Math Row Guard** — before writing to `Data_Lines_V2`, verify
  `new_total_rows == old_total_rows - old_rows_of_this_dt + new_rows_of_this_dt`;
  abort / roll back the write on any mismatch instead of writing partial data.
- **`sheet_write_lock`** — the read/OCR path runs lock-free and concurrent; only the Sheets append/update path takes the lock, to keep `DTnXXXX` allocation sequential and avoid duplicate codes. `reconciliation_engine.py` imports this lock from `server.py`.

## Duplicate detection (two tiers)

`services/duplicate_checker.py` — `DuplicateChecker` normalizes order/tracking/invoice/receipt IDs via `clean_dt_code` and flags:

- **Tier 1** — against sheet history.
- **Tier 2** — within the same scan batch.

## Reconciliation

`services/reconciliation_engine.py` matches Header vs Lines totals with tolerances (`RECON_TOLERANCE_VND`, `RECON_TOLERANCE_PCT`) and classifies breaks via the `BreakFlag` enum (`MATCHED`, `ROUNDING_BREAK`, `MISSING_*_BREAK`, `REAL_DISCREPANCY`, `CROSS_PERIOD_DUPLICATE`, `PENDING_REVIEW`, `OVERRIDE_BREAK`). Invoices without dates are still reconciled (`docs/decisions/0001`). Line discounts are auto-detected (`0002`) and line values auto-computed (`0003`).
