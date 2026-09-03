# ADR-001: Pure Numeric Standard for Currency and Rate Units in Relational V2 Schema

## Status
Accepted

## Date
2026-08-21

## Context
In the Procurement Receipt OCR & Automation System, the Relational V2 database architecture organizes data into two sheets:
- `Data_Header_V2` (Invoice metadata)
- `Data_Lines_V2` (Line item details)

Previously, currency amounts were formatted as localized string representations (e.g. `"150.000,00 đ"`, `"0,00 đ"`) and discount/VAT tax rates were sometimes stored with text characters (e.g. `"10%"`).
While string-formatted text displays visually with currency symbols, it introduces major operational limitations:
1. **Spreadsheet Calculation Inefficiency**: Formulas such as `=SUM()`, `=AVERAGE()`, and Pivot Tables in Google Sheets or Microsoft Excel cannot natively aggregate string values without error-prone text parsing or regex replacement formulas.
2. **ERP & Accounting Export Overhead**: Direct export to accounting systems, BI dashboards (Looker Studio, PowerBI), or downstream databases requires additional data sanitization layers.
3. **Floating Point Precision & Sorting Issues**: Text strings sort alphabetically rather than numerically (e.g., `"100.000 đ"` comes before `"20.000 đ"`).

## Decision
We standardize all currency amounts and rate fields in `Data_Header_V2` and `Data_Lines_V2` to **Pure Numeric Types** (`int` / `float`):

### 1. Currency Fields (Đơn vị tiền - 100% Số thuần):
- **`Data_Header_V2`**:
  - Column G (`Tổng tiền hàng (gốc)`) -> Pure numeric (e.g., `1300000`)
  - Column H (`Chiết khấu thương mại`) -> Pure numeric (e.g., `5000` / `0`)
  - Column I (`Thuế VAT`) -> Pure numeric (e.g., `6000` / `0`)
  - Column J (`Tổng Thanh Toán`) -> Pure numeric (e.g., `1300000`)
- **`Data_Lines_V2`**:
  - Column E (`Đơn giá`) -> Pure numeric (e.g., `40000`)
  - Column F (`Chiết khấu mặt hàng`) -> Pure numeric (e.g., `5000` / `0`)
  - Column I (`Tiền thuế VAT`) -> Pure numeric (e.g., `6000` / `0`)
  - Column J (`Thành tiền`) -> Pure numeric (e.g., `81000`)

### 2. Rate Fields (Đơn vị tỷ suất - 100% Số thuần):
- **`Data_Lines_V2`**:
  - Column G (`Tỷ lệ chiết khấu (%)`) -> Pure numeric (e.g., `10`, `6.25`, `0` — without `%` character)
  - Column H (`Thuế suất VAT (%)`) -> Pure numeric (e.g., `8`, `10`, `0` — without `%` character)

### 3. Data Integrity & Non-Destructive Guarantee:
- The raw OCR input JSON and legacy fallback Flat Schema (`Bang_Ke_Hoa_Don`) remain preserved.
- The web frontend UI formatting utilizes client-side currency formatters (`formatMoney()`), ensuring rich human-readable visual display on the Dashboard while keeping backend spreadsheet data completely numeric.

## Alternatives Considered

### Alternative 1: Keep String Format with `" đ"` and `"%"`
- **Pros**: Directly readable in spreadsheet cells without relying on Google Sheets cell number formatting.
- **Cons**: Broken arithmetic summation (`SUM`), broken pivot tables, and high ETL parsing overhead.
- **Rejected**: Hinders enterprise scalability and automated accounting reconciliation.

### Alternative 2: Store Strings but Apply Google Sheets Custom Number Formats via API
- **Pros**: Cell value is pure numeric, but Google Sheets renders with currency symbol.
- **Cons**: Depends on Google Sheets API format metadata batch updates.
- **Accepted as Complementary Layer**: Pure numeric data is written as the core value, and Google Sheets UI formatting scripts (`beautify_google_sheets.py`) apply visual currency masks without modifying the underlying raw numeric values.

## Consequences
- **Positive**: 100% seamless formula calculations (`SUM`, `SUMIFS`, `PIVOT`, `QUERY`), easy accounting audit, clean data exports to ERP/BI tools.
- **Positive**: Exact unrounded arithmetic consistency between Header and Lines.
- **Compliance**: All test suites (`test_v2_architecture.py`, `test_suite.py`) verified and passing.
