# ADR-0001: Đối Soát Hóa Đơn Không Có Ngày Tháng (UNKNOWN Period)

## Status
Accepted

## Date
2026-08-24

## Context
Trong thực tế sử dụng hệ thống Procurement Receipt OCR, có những loại hóa đơn (đặc biệt là hóa đơn viết tay DT4 hoặc biên lai bán lẻ) bị khuyết thông tin ngày tháng, hoặc ngày tháng bị nhòe không thể trích xuất.
Hiện tại, Reconciliation Engine đang gán các hóa đơn này vào kỳ `"UNKNOWN"` và đánh dấu cờ `PENDING_REVIEW`. Tuy nhiên, người dùng cần có khả năng chủ động chạy đối soát và xuất báo cáo cho nhóm hóa đơn khuyết ngày tháng này để đối chiếu số liệu tổng trước khi bổ sung thông tin thủ công.

## Decision
1. Bổ sung tùy chọn `"Không có ngày tháng (UNKNOWN)"` vào danh sách kỳ đối soát trên giao diện Web Dashboard (`reconcile.html`).
2. Khi tùy chọn này được chọn, hệ thống sẽ gửi tham số `period = "UNKNOWN"` đến API đối soát.
3. Reconciliation Engine sẽ tự động lọc toàn bộ các hóa đơn khuyết ngày tháng (hoặc khuyết phần tiêu đề Header dẫn đến khuyết ngày tháng) để chạy đối chiếu và ghi nhận kết quả vào tab `Reconciliation_Report_UNKNOWN`.

## Alternatives Considered
*   **Từ chối đối soát và yêu cầu người dùng điền ngày tháng trước:** Bị loại bỏ vì làm giảm trải nghiệm người dùng, kế toán cần xem nhanh sự chênh lệch tiền hàng/dòng trước khi tốn công điền ngày tháng cho hàng loạt hóa đơn viết tay.
*   **Tự động gán vào năm hiện tại:** Gây sai lệch kỳ kế toán nghiêm trọng và làm hỏng nhật ký kiểm toán.

## Consequences
*   Người dùng có thể đối soát tất cả hóa đơn không có ngày tháng chỉ bằng 1 click chọn `"Không có ngày tháng"` trên Dashboard.
*   Báo cáo được lưu trữ riêng biệt tại tab `Reconciliation_Report_UNKNOWN` giúp kế toán dễ dàng theo dõi và xử lý dứt điểm các hóa đơn lỗi ngày tháng.
*   Mọi hóa đơn trong kỳ này vẫn được gắn cờ `PENDING_REVIEW` bắt buộc duyệt thủ công để đảm bảo tính an toàn nghiệp vụ.
