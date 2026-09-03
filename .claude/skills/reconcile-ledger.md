---
name: reconcile-ledger
description: >
  Chạy hoặc kiểm tra quy trình đối soát sổ sách tự động giữa Data_Header_V2 và
  Data_Lines_V2 — so khớp số liệu, xác định dung sai làm tròn, gắn cờ BreakFlag,
  xuất báo cáo kỳ. Dùng khi người dùng yêu cầu đối soát, khóa sổ một kỳ, chạy
  reconciliation engine, hoặc xử lý các sai lệch PENDING_REVIEW.
---

# Đối soát sổ sách — Reconciliation DTn

> Port từ `.agents/skills/Reconciliation_DTn/SKILL.md`. Code tương ứng:
> `services/reconciliation_engine.py`. ADR liên quan: `docs/decisions/0001`,
> `0002`, `0003`.

## ⚠️ Nguyên tắc cốt lõi (bắt buộc)

1. **Read-only với dữ liệu gốc** — không sửa bất kỳ trường nào trên
   `Data_Header_V2`, `Data_Lines_V2` khi đối soát.
2. **Ghi báo cáo ra tab riêng** — kết quả kỳ vào `Reconciliation_Report_YYYYMM`,
   nhật ký chạy vào `Recon_Audit_Log`.
3. **Human-in-the-loop** — mọi sai lệch vượt ngưỡng / sai lệch thuế / trùng kỳ
   phải gắn cờ `PENDING_REVIEW`, tuyệt đối không tự ghi đè.

## Quy trình 5 giai đoạn

```
[Normalize] → [Match] → [Tolerance Check] → [Flag Break] → [Export Report]
```

### 1. Normalize
- Gom dòng từ `Data_Lines_V2`, join với `Data_Header_V2` qua `Mã đối tượng`.
- Trích: Ngày, kỳ `YYYYMM`, tên đơn vị bán, Thành tiền, Chiết khấu, VAT.
- **Line Auto-Compute** (`docs/decisions/0003`): trước khi cộng `lines_total`,
  phân loại lại chiết khấu mỗi dòng — mọi điều chỉnh chỉ **in-memory**:
  - `disc_raw < đơn_giá × 0.99` **và** `SL > 1` → chiết khấu đơn vị → nhân `disc_raw × SL`.
  - `disc_raw >= đơn_giá × 0.99` → chiết khấu tổng dòng → dùng trực tiếp.
  - Ưu tiên `disc_rate%` nếu khai báo → `gross × disc_rate`.

### 2. Match
- **DT2/DT3/DT4**: mặc định `Expected_Total = sum(Lines.Thành_tiền) − Header.Chiết_Khấu_Thương_Mại`.
  - **Line Net Discount Heuristic** (`docs/decisions/0002`): nếu `Chiết_Khấu > 0`
    và `sum(Lines.Thành_tiền)` khớp `Header_Total` hơn công thức chuẩn (trong dung sai)
    → chiết khấu đã trừ ở dòng → dùng `Expected_Total = sum(Lines.Thành_tiền)` để
    tránh trừ trùng.
  - Chiết khấu cấp dòng: theo đơn vị → nhân với số lượng dòng trước khi đối soát;
    theo tổng đơn → lấy tổng gốc trừ chiết khấu tổng.
- **DT1 (khuyết Lines)**: đối soát nội bộ Header → `Expected_Total = Raw − Discount + VAT`.

### 3. Tolerance Check
Dung sai làm tròn hợp lệ khi **đồng thời**:
- Chênh lệch ≤ `RECON_TOLERANCE_VND` (mặc định 1.000 VNĐ)
- Tỷ lệ chênh lệch ≤ `RECON_TOLERANCE_PCT` (mặc định 0,5%)
- Không lặp lại qua nhiều kỳ từ cùng một nhà cung cấp.

### 4. Flag Break (`BreakFlag` enum trong `reconciliation_engine.py`)

| Cờ | Định nghĩa |
|---|---|
| `MATCHED` | Khớp tuyệt đối 100% |
| `ROUNDING_BREAK` | Lệch trong dung sai làm tròn (cảnh báo vàng) |
| `MISSING_HEADER_BREAK` | Khuyết thông tin Header |
| `MISSING_LINES_BREAK` | Khuyết dòng chi tiết |
| `REAL_DISCREPANCY` | Lệch số học thực tế ngoài dung sai |
| `CROSS_PERIOD_DUPLICATE` | Trùng số hóa đơn ở kỳ kế toán khác |
| `PENDING_REVIEW` | Có yếu tố thuế/chứng từ/kỳ hoặc lặp lại cần duyệt |
| `OVERRIDE_BREAK` | Đã có phê duyệt thủ công ghi đè |

### 5. Export
- `Reconciliation_Report_YYYYMM` — kết quả kỳ.
- `Recon_Audit_Log` — lịch sử chạy (bằng chứng kiểm toán).

## Endpoint & lệnh

```bash
# Qua API
POST /api/v1/reconcile/run
GET  /api/v1/reconcile/status/{period}
GET  /api/v1/reconcile/report/{period}
POST /api/v1/sheets/reconcile-totals

# Test engine
docker exec procurement-server python -m pytest tests/test_reconciliation.py -q
```

Trước khi chạy đối soát ghi báo cáo lần đầu cho một kỳ, cân nhắc snapshot Sheet
bằng skill `backup-sheet`.
