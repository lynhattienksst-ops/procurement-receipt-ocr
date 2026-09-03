# ADR-0002: Tự Động Phát Hiện Chiết Khấu Khấu Trừ Trực Tiếp Tại Dòng (Line Net Discount)

## Status
Accepted

## Date
2026-08-24

## Context
Trong thực tế xử lý hóa đơn siêu thị (đặc biệt là MM Mega Market), cách in hóa đơn của các đơn vị bán lẻ dẫn đến việc bộ máy OCR (Gemini LLM) trích xuất:
1. Giá trị `Thành tiền` của từng dòng mặt hàng là giá trị sau khi đã trừ chiết khấu dòng (Net Price).
2. Giá trị `Chiết khấu thương mại` ở cấp độ Header vẫn được trích xuất đầy đủ từ tổng chiết khấu in cuối hóa đơn.

Khi chạy đối soát theo công thức chuẩn:
$$\text{Expected Total} = \sum(\text{Lines.Thành\_tiền}) - \text{Header.Chiết\_Khấu}$$
Hệ thống sẽ vô tình khấu trừ chiết khấu lần thứ 2, dẫn đến lỗi chênh lệch lớn `REAL_DISCREPANCY` (Cờ đỏ), buộc kế toán phải can thiệp thủ công sửa dữ liệu gốc trên Sheet (vi phạm nguyên tắc bất biến dữ liệu gốc và trung thực khách quan).

## Decision
Bổ sung cơ chế tự động kiểm tra chéo (heuristic) trong Reconciliation Engine:
1. Khi có `Header.Discount > 0`, tính toán hai phương án đối soát:
   * **Phương án chuẩn:** `diff_standard = abs(Header_Total - (Lines_Total - Discount))`
   * **Phương án không khấu trừ thêm:** `diff_no_discount = abs(Header_Total - Lines_Total)`
2. Nếu `diff_no_discount < diff_standard` và `diff_no_discount` nằm trong khoảng dung sai cho phép (1.000 VNĐ và 0.5%):
   * Hệ thống tự động nhận diện rằng dòng mặt hàng đã trừ chiết khấu (Net).
   * Sử dụng `Lines_Total` làm `Expected Total` (không trừ chiết khấu Header thêm lần nữa).
   * Vẫn giữ nguyên số liệu chiết khấu gốc trên cơ sở dữ liệu `Data_Header_V2` để bảo toàn vết kiểm toán và phục vụ các báo cáo tài chính khác.

## Alternatives Considered
*   **Yêu cầu kế toán sửa tay dữ liệu gốc về 0:** Bị loại bỏ vì vi phạm nguyên tắc Khách quan (hóa đơn thực sự có chiết khấu), vi phạm nguyên tắc Bất biến dữ liệu gốc của hệ thống và làm tăng thao tác thủ công.

## Consequences
*   Hóa đơn của MM Mega Market và các nhà cung cấp tương tự sẽ được đối soát tự động khớp thành công mà không cần sửa đổi dữ liệu gốc.
*   Bảo vệ sự an toàn và toàn vẹn của Audit Trail.
