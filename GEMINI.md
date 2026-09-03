# Quy Tắc Dự Án Scan Hóa Đơn (Procurement Receipt OCR)

Mỗi khi nhận được yêu cầu liên quan đến dự án này hoặc qua lệnh `/Scan_Hoa_Don`:
1. **Bắt buộc đọc lại file `Project_report`** để nắm bắt bối cảnh dự án, quy tắc 4 nhóm đối tượng `DT1`-`DT4`, schema Google Sheet quan hệ V2 — **`Data_Header_V2` 14 cột (A→N)** và **`Data_Lines_V2` 11 cột (A→K)**, join qua mã đối tượng ở cột A (cột "Người mua/nhận hàng" = K, đứng trước "Ghi chú chung" = M) — cơ chế chống trùng lặp 2 tầng và cấu hình hệ thống.
2. Tuyệt đối không tự ý thay đổi cấu trúc mã hoặc format cột nếu không có yêu cầu từ người dùng.
3. Bản tóm tắt chi tiết cho AI nằm ở `.claude/rules/` (`workflow.md`, `design.md`, `tech-defaults.md`) và `.claude/skills/scan-context.md`.
