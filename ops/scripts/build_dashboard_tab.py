"""
Dựng (hoặc dựng lại) tab **`Dashboard`** trong chính spreadsheet đang chạy — bảng điều
khiển PHÂN TÍCH đọc THẲNG từ `Data_Header_V2` / `Data_Lines_V2` bằng công thức `QUERY()`,
kèm biểu đồ gốc của Google Sheets.

Thiết kế theo triết lý dashboard hàng đầu (NN/g · Tableau · Cleveland & McGill · Tufte)
và theo TỶ LỆ KHUNG NGANG một-màn-hình (như mẫu marketing dashboard):
  - Bố cục 3 khối theo chiều NGANG:
      • Dải KPI trên cùng (6 thẻ số lớn, quan trọng nhất bên trái — F-pattern).
      • Khối biểu đồ: cột TRÁI rộng (LINE xu hướng + BAR Top NCC) · cột PHẢI hẹp
        (4 BAR ngang: nhóm ĐT, nhóm hàng, chất lượng, Loại Break).
      • Dải "SỐ LIỆU CHI TIẾT" ở đáy: các bảng nguồn xếp cạnh nhau theo cột.
  - Cleveland & McGill: so sánh nhóm = BAR NGANG (sắp theo giá trị, trục từ 0);
    xu hướng thời gian = LINE. KHÔNG pie chart.
  - data-ink (Tufte): 1 màu nhấn navy, bỏ nền thẻ/viền/lưới thừa.
  - Chú giải: 1 dòng gộp / khối, đặt NGAY DƯỚI tiêu đề bảng nguồn ở dải đáy — không
    còn cột chú giải riêng chồng lên biểu đồ (sửa lỗi lệch chú thích).
  - Lưới & khoảng cách: bội số 8px.

Số liệu ĐỘNG (công thức tham chiếu 2 bảng gốc). Chạy lại script chỉ để dựng lại bố cục.

An toàn (Critical Invariants):
  - CHỈ đụng tab `Dashboard` + vùng ẩn phụ trợ (cột AA:AD). KHÔNG sửa bảng gốc / tab khác.
  - Idempotent: chạy lần 2 → reset định dạng + clear vùng + xóa chart cũ + ghi lại.
  - Lấy `sheet_write_lock`. --dry-run: in bố cục, không ghi.

Dùng:
  docker exec procurement-server python ops/scripts/build_dashboard_tab.py --dry-run
  docker exec procurement-server python ops/scripts/build_dashboard_tab.py
"""
import os
import sys
import asyncio
import argparse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.google_service import GoogleSyncService  # noqa: E402
from services.business_rules import LINE_GROUP_CODES  # noqa: E402

try:
    from server import sheet_write_lock
except ImportError:
    sheet_write_lock = asyncio.Lock()

HEADER_TAB = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
LINES_TAB = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")
RECON_LOG_TAB = os.getenv("GOOGLE_SHEET_RECON_LOG_NAME", "Recon_Audit_Log")
RECON_REPORT_TAB = os.getenv("GOOGLE_SHEET_RECON_REPORT_NAME", "Reconciliation_Report_ALL")
DASH_TAB = "Dashboard"

# Locale vi_VN → dấu phân cách tham số hàm ';' ; array literal: cột '\', hàng ';'.
ARG = ";"
ACOL = "\\"

OWNED_RANGE = f"{DASH_TAB}!A1:AL170"

GROUP_LABELS = {
    "NL_TP": "Nguyên liệu / thực phẩm",
    "HH_BAN": "Hàng hóa mua để bán / vật tư",
    "CCDC_TS": "Công cụ, dụng cụ & tài sản",
    "DV_VC": "Dịch vụ vận chuyển",
    "DV_SAN": "Phí sàn TMĐT / hoa hồng",
    "DV_KHAC": "Dịch vụ khác",
    "VP_PHAM": "Văn phòng phẩm & tiện ích",
    "KHAC": "Khác (đã phân loại)",
    "CAN_SOAT": "Cần soát (chưa phân loại)",
}

FMT_MONEY = "#,##0;(#,##0)"

# ---- Bảng màu: 1 màu nhấn (navy) + dải xám nhạt. ----
INK = {"red": 0.11, "green": 0.16, "blue": 0.24}
MUTED = {"red": 0.42, "green": 0.47, "blue": 0.54}
ACCENT = {"red": 0.16, "green": 0.36, "blue": 0.66}
ACCENT_HEX = "294FA6"
RULE = {"red": 0.85, "green": 0.87, "blue": 0.90}
ZEBRA = {"red": 0.965, "green": 0.972, "blue": 0.980}
WHITE = {"red": 1, "green": 1, "blue": 1}

# Chú giải mục "CÁCH ĐỌC" — mỗi biểu đồ một khối, trình bày dạng LIST. Văn phong viết,
# câu hoàn chỉnh, dùng cho cấp quản lý đọc. Giữ nguyên mã tiếng Anh như trên biểu đồ.
# (anchor ô tiêu đề, TIÊU ĐỀ, [các dòng]).
LEGENDS = [
    ("A118", "XU HƯỚNG CHI THEO THÁNG", [
        "Biểu đồ thể hiện tổng chi mua hàng (đường màu xanh) và thuế VAT đầu vào (đường màu đỏ) theo từng tháng.",
        "Đường biểu diễn đi lên cho thấy mức chi trong tháng tăng so với các tháng liền trước.",
        "Nên theo dõi để nhận diện các tháng có mức chi tăng đột biến hoặc sụt giảm bất thường.",
    ]),
    ("H118", "CHI THEO NHÓM ĐỐI TƯỢNG", [
        "Toàn bộ hóa đơn được phân thành bốn nhóm, căn cứ theo kênh mua hàng.",
        "DT1: mua qua sàn thương mại điện tử hoặc phát sinh phí vận chuyển.",
        "DT2: mua trực tiếp tại cửa hàng bán lẻ.",
        "DT3: mua từ nhà cung cấp thực phẩm.",
        "DT4: hóa đơn viết tay hoặc chưa xác định được nguồn.",
    ]),
    ("A130", "CƠ CẤU CHI THEO NHÓM HÀNG", [
        "Biểu đồ phân bổ tiền mua hàng theo từng nhóm chi phí.",
        "Nguyên liệu và thực phẩm.",
        "Hàng hóa mua để bán lại.",
        "Công cụ, máy móc và tài sản.",
        "Phí dịch vụ: vận chuyển, phí sàn thương mại điện tử...",
        "Văn phòng phẩm và các khoản chi khác.",
        "Cột dài hơn thể hiện nhóm chi phí chiếm tỷ trọng lớn hơn.",
    ]),
    ("H130", "TÍNH CHÍNH XÁC PHÂN LOẠI NHÓM HÀNG", [
        "Nhãn trên biểu đồ cho biết phương thức hệ thống dùng để xếp từng dòng hàng vào nhóm.",
        "rule:keyword: nhận diện theo tên hàng hóa — độ chính xác cao nhất.",
        "rule:category: suy luận theo loại hóa đơn — độ tin cậy khá.",
        "unit_hint: suy đoán theo đơn vị tính (kg, cái...) — độ tin cậy trung bình.",
        "auto_default: hệ thống gán mặc định — độ tin cậy thấp, cần rà soát.",
        "low_conf: hệ thống không đủ căn cứ — cần người kiểm tra.",
        "manual: do người dùng trực tiếp phân loại — chính xác.",
    ]),
    ("A144", "PHÂN BỐ LOẠI BREAK (KẾT QUẢ ĐỐI SOÁT)", [
        "Sau khi đối chiếu sổ sách, mỗi hóa đơn được gắn một trạng thái kết quả.",
        "MATCHED: tổng tiền hóa đơn khớp với tổng các dòng chi tiết.",
        "ROUNDING_BREAK: chênh lệch nhỏ do làm tròn, nằm trong ngưỡng chấp nhận.",
        "REAL_DISCREPANCY: chênh lệch thực chất, cần mở hóa đơn để kiểm tra.",
        "PENDING_REVIEW: nhà cung cấp phát sinh chênh lệch nhiều lần, đang chờ rà soát.",
    ]),
    ("H144", "BẢNG CAN_SOAT & LỊCH SỬ ĐỐI SOÁT", [
        "CAN_SOAT: các dòng hàng hệ thống chưa phân loại được, cần người xử lý trên màn hình duyệt hóa đơn.",
        "Lịch sử: mỗi kỳ hiển thị một dòng ứng với lần chạy đối soát gần nhất, gồm tổng số hóa đơn, số khớp, "
        "số lệch do làm tròn (LT) và số lệch thực chất (TT).",
    ]),
]

# Tên bảng ở mục "SỐ LIỆU CHI TIẾT" — KHỚP tên biểu đồ tương ứng ở mục "PHÂN TÍCH".
# (anchor ô tiêu đề, cột đầu, cột cuối (exclusive), tên bảng)
TABLE_TITLES = [
    ("A59", 0, 3, "Xu hướng chi theo tháng"),
    ("E59", 4, 7, "Cơ cấu chi theo nhóm hàng"),
    ("I59", 8, 12, "Tính chính xác phân loại (số dòng)"),
    ("M59", 12, 15, "Phân bố Loại Break"),
    ("A83", 0, 3, "Chi theo nhóm đối tượng"),
    ("A90", 0, 3, "Top 12 nhà cung cấp"),
    ("E83", 4, 7, "Danh sách CAN_SOAT (cần soát tay)"),
    ("I83", 8, 14, "Lịch sử chạy đối soát"),
]


# =============================================================================
# Bố cục
# =============================================================================

def _build_blocks():
    H = f"'{HEADER_TAB}'"
    L = f"'{LINES_TAB}'"
    b = []

    # ---- Tiêu đề ----
    b.append(("A1", [["BẢNG ĐIỀU KHIỂN MUA VÀO"]]))
    b.append(("A2", [["Tự cập nhật từ %s / %s. Đơn vị: VND." % (HEADER_TAB, LINES_TAB)]]))

    # ---- Dải KPI (6 thẻ; quan trọng nhất bên trái). Nhãn hàng 4, số hàng 5, phụ hàng 6. ----
    b.append(("A4", [["TỔNG THANH TOÁN", "TIỀN HÀNG GỐC", "THUẾ VAT", None,
                      "CHIẾT KHẤU", "SỐ HĐ", "SỐ NCC"]]))
    b.append(("A5", [[
        f"=SUM({H}!J2:J)", f"=SUM({H}!G2:G)", f"=SUM({H}!I2:I)", None,
        f"=SUM({H}!H2:H)", f"=COUNTA({H}!A2:A)", f"=COUNTUNIQUE({H}!C2:C)",
    ]]))
    b.append(("A6", [[
        f"=\"TB/HĐ \"&TEXT(SUM({H}!J2:J)/COUNTA({H}!A2:A)%s\"#,##0\")" % ARG,
        "", f"=\"VAT/tiền hàng \"&TEXT(SUM({H}!I2:I)/SUM({H}!G2:G)%s\"0.0%%\")" % ARG, None,
        "", "", "",
    ]]))

    # ---- Dải "PHÂN TÍCH" (biểu đồ overlay từ row 9) ----
    b.append(("A8", [["PHÂN TÍCH"]]))

    # ---- Dải "SỐ LIỆU CHI TIẾT" (dải đáy, row 56) — các bảng nguồn của biểu đồ ----
    b.append(("A56", [["SỐ LIỆU CHI TIẾT"]]))
    for anchor, _c0, _c1, title in TABLE_TITLES:   # tên bảng, khớp tên biểu đồ ở 'PHÂN TÍCH'
        b.append((anchor, [[title]]))

    # ---- Dải "CÁCH ĐỌC" (row 116) — chú giải dạng LIST cho từng biểu đồ ----
    b.append(("A116", [["CÁCH ĐỌC — GIẢI THÍCH TỪNG BIỂU ĐỒ"]]))
    for anchor, title, items in LEGENDS:
        col = anchor[0]
        row0 = int(anchor[1:])
        b.append((f"{col}{row0}", [[title]]))                       # dòng tiêu đề
        for i, line in enumerate(items):                            # mỗi yếu tố 1 dòng
            b.append((f"{col}{row0 + 1 + i}", [[f"•  {line}"]]))

    # --- Cột A–C: Chi theo tháng (LINE nguồn) ---
    b.append(("A60", [["Tháng", "Chi mua vào", "VAT"]]))
    b.append(("A61", [[
        "=QUERY($AA$2:$AC%s "
        "\"select Col1, sum(Col2), sum(Col3) where Col1 is not null and Col1 >= '2024-01' and Col1 <= '2026-12' "
        "group by Col1 order by Col1 label sum(Col2) '', sum(Col3) ''\"%s 0)" % (ARG, ARG)
    ]]))

    # --- Cột E–G: Cơ cấu nhóm hàng (BAR nguồn) ---
    b.append(("E60", [["Nhóm hàng", "Thành tiền", "Số dòng"]]))
    grp = []
    for code in LINE_GROUP_CODES:
        grp.append([
            GROUP_LABELS.get(code, code),
            f'=SUMIF({L}!M2:M%s "{code}"%s {L}!K2:K)' % (ARG, ARG),
            f'=COUNTIF({L}!M2:M%s "{code}")' % ARG,
        ])
    b.append(("E61", grp))  # E61..E69

    # --- Cột I–K: Chất lượng phân loại (BAR nguồn) ---
    b.append(("I60", [["Nguồn", "Số dòng", "Thành tiền"]]))
    b.append(("I61", [[
        "=QUERY(%s!A2:N%s "
        "\"select N, count(A), sum(K) where N is not null group by N order by count(A) desc "
        "label count(A) '', sum(K) ''\"%s 0)" % (LINES_TAB, ARG, ARG)
    ]]))

    # --- Cột M–N: Phân bố Loại Break (BAR nguồn) ---
    b.append(("M60", [["Loại Break", "Số hóa đơn"]]))
    b.append(("M61", [[
        "=IFERROR(QUERY('%s'!A2:G%s "
        "\"select F, count(A) where F is not null group by F order by count(A) desc label count(A) ''\"%s 0)%s "
        "\"Chưa có Reconciliation_Report_ALL. Chạy reconcile/run.\")"
        % (RECON_REPORT_TAB, ARG, ARG, ARG)
    ]]))

    # --- Hàng dưới: Cột A–B nhóm ĐT (BAR nguồn) ---
    b.append(("A84", [["Nhóm", "Chi mua vào"]]))
    b.append(("A85", [[
        "=QUERY({$AD$2:$AD%s $AB$2:$AB}%s "
        "\"select Col1, sum(Col2) where Col1 is not null group by Col1 order by sum(Col2) desc "
        "label sum(Col2) ''\"%s 0)" % (ACOL, ARG, ARG)
    ]]))
    # --- Cột A (tiếp): Top 12 NCC (BAR nguồn) ---
    b.append(("A91", [["Nhà cung cấp", "Chi mua vào"]]))
    b.append(("A92", [[
        "=QUERY(%s!A2:J%s "
        "\"select C, sum(J) where C is not null group by C order by sum(J) desc limit 12 label sum(J) ''\"%s 0)"
        % (HEADER_TAB, ARG, ARG)
    ]]))

    # --- Cột E–F: CAN_SOAT ---
    b.append(("E84", [["Mã đối tượng", "Tên hàng hóa", "Thành tiền"]]))
    b.append(("E85", [[
        "=IFERROR(QUERY(%s!A2:N%s "
        "\"select A, C, K where M = 'CAN_SOAT' order by K desc limit 30\"%s 0)%s \"(không có)\")"
        % (LINES_TAB, ARG, ARG, ARG)
    ]]))

    # --- Cột I–M: Lịch sử đối soát ---
    # Đọc từ vùng phụ trợ AF:AK (Kỳ đã chuẩn hóa + mốc thời gian). SORT theo thời gian
    # mới→cũ, rồi SORTN chế độ 2 → chỉ giữ LẦN CHẠY MỚI NHẤT cho mỗi kỳ (bỏ trùng).
    b.append(("I84", [["Kỳ", "Tổng HĐ", "Khớp", "Lệch LT", "Lệch TT"]]))
    b.append(("I85", [[
        "=IFERROR(QUERY(SORTN(SORT($AF$2:$AK%s 2%s FALSE)%s 50%s 2%s 1%s TRUE)%s "
        "\"select Col1, Col3, Col4, Col5, Col6 where Col1 is not null and Col1 <> '' "
        "order by Col1 label Col3 '', Col4 '', Col5 '', Col6 ''\"%s 0)%s \"(chưa có dữ liệu đối soát)\")"
        % (ARG, ARG, ARG, ARG, ARG, ARG, ARG, ARG, ARG)
    ]]))

    # ---- Vùng phụ trợ ẩn AA:AD ----
    _bcol = "TO_TEXT(%s!B2:B)" % HEADER_TAB
    _yy = "REGEXEXTRACT(%s%s \"-(20\\d{2})$\")" % (_bcol, ARG)
    _mm = "(REGEXEXTRACT(%s%s \"(\\d{1,2})-20\\d{2}$\") + 0)" % (_bcol, ARG)
    _month_f = (
        "=ARRAYFORMULA(IFERROR("
        "IF((%s >= 1) * (%s <= 12)%s %s & \"-\" & TEXT(%s%s \"00\")%s \"\")"
        "%s \"\"))" % (_mm, _mm, ARG, _yy, _mm, ARG, ARG, ARG)
    )
    # AF:AK — phụ trợ cho bảng "Lịch sử chạy đối soát":
    #   AF _ky        = Kỳ chuẩn hóa: 6 chữ số → "Tháng MM/YYYY" · 4 chữ số → "Cả năm YYYY" · khác → giữ nguyên
    #   AG _thoigian  = mốc thời gian chạy (cột B) — để SORT mới→cũ
    #   AH..AK        = Tổng HĐ / Khớp / Lệch LT / Lệch TT (cột E/F/G/H)
    _rc = "'%s'" % RECON_LOG_TAB
    _kc = "TO_TEXT(%s!C2:C)" % _rc
    _ky_f = (
        "=IFERROR(ARRAYFORMULA(IF(%s!C2:C=\"\"%s%s"
        "IF(REGEXMATCH(%s%s \"^\\d{6}$\")%s "
        "\"Tháng \"&MID(%s%s5%s2)&\"/\"&LEFT(%s%s4)%s "
        "IF(REGEXMATCH(%s%s \"^\\d{4}$\")%s \"Cả năm \"&%s%s %s))))%s \"\")"
        % (_rc, ARG, ARG, _kc, ARG, ARG, _kc, ARG, ARG, _kc, ARG, ARG,
           _kc, ARG, ARG, _kc, ARG, _kc, ARG)
    )

    def _rc_col(letter):
        return ("=IFERROR(ARRAYFORMULA(IF(%s!C2:C=\"\"%s%s%s!%s2:%s))%s \"\")"
                % (_rc, ARG, ARG, _rc, letter, letter, ARG))

    helpers = [
        ("AA1", [["_thang", "_tong_tt", "_vat", "_nhom_dt"]]),
        ("AA2", [[
            _month_f,
            "=ARRAYFORMULA(IF(%s!A2:A=\"\"%s%s%s!J2:J))" % (HEADER_TAB, ARG, ARG, HEADER_TAB),
            "=ARRAYFORMULA(IF(%s!A2:A=\"\"%s%s%s!I2:I))" % (HEADER_TAB, ARG, ARG, HEADER_TAB),
            "=ARRAYFORMULA(IF(%s!A2:A=\"\"%s%sLEFT(%s!A2:A%s3)))" % (HEADER_TAB, ARG, ARG, HEADER_TAB, ARG),
        ]]),
        ("AF1", [["_ky", "_thoigian", "_tonghd", "_khop", "_lech_lt", "_lech_tt"]]),
        ("AF2", [[
            _ky_f,
            "=IFERROR(ARRAYFORMULA(IF(%s!C2:C=\"\"%s%sTO_TEXT(%s!B2:B)))%s \"\")" % (_rc, ARG, ARG, _rc, ARG),
            _rc_col("E"), _rc_col("F"), _rc_col("G"), _rc_col("H"),
        ]]),
    ]

    note = (
        "Tab '%s' — khung NGANG một-màn-hình:\n"
        "  Row 1  tiêu đề · Row 4-6  dải 6 KPI (A/B/C · E/F/G)\n"
        "  Row 8  'PHÂN TÍCH' — biểu đồ overlay:\n"
        "     TRÁI  (col A): LINE xu hướng (r9-27) + BAR Top 12 NCC (r28-48)\n"
        "     PHẢI  (col H): BAR nhóm ĐT (r9-18) · BAR nhóm hàng (r19-31) · "
        "BAR chất lượng (r32-42) · BAR Loại Break (r43-53)\n"
        "  Row 56 'SỐ LIỆU CHI TIẾT' — bảng nguồn xếp theo cột (A:C · E:G · I:K · M:N), "
        "hàng 2 (A:B nhóm ĐT + Top12 · E:G CAN_SOAT · I:M lịch sử).\n"
        "  Row 116 'CÁCH ĐỌC' — 6 khối chú giải dạng LIST (tiêu đề + mỗi yếu tố 1 dòng), "
        "2 cột: TRÁI col A:F (r118/130/144) · PHẢI col H:N (r118/130/144).\n"
        "  Chart: 1 LINE + 5 BAR ngang. KHÔNG pie. Bar sắp theo giá trị, trục từ 0."
        % DASH_TAB
    )
    return b, helpers, note


# =============================================================================
# Biểu đồ — 1 LINE + 5 BAR ngang.
# =============================================================================

def _grid(sid, r0, r1, c0, c1):
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _hex_rgb(h):
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


_TITLE_FMT = {"fontFamily": "Roboto", "fontSize": 11, "bold": True, "foregroundColorStyle": {"rgbColor": INK}}
_AXIS_FMT = {"fontFamily": "Roboto", "fontSize": 9, "foregroundColorStyle": {"rgbColor": MUTED}}
_BG = {"backgroundColorStyle": {"rgbColor": WHITE}}


def _pos(sid, row, col, w, h):
    return {"overlayPosition": {
        "anchorCell": {"sheetId": sid, "rowIndex": row, "columnIndex": col},
        "widthPixels": w, "heightPixels": h}}


def _bar_h(title, cat, val, sid, row, col, w, h):
    return {"addChart": {"chart": {"spec": {
        "title": title, "titleTextFormat": _TITLE_FMT, "fontName": "Roboto",
        "basicChart": {
            "chartType": "BAR", "legendPosition": "NO_LEGEND", "headerCount": 1,
            "axis": [
                {"position": "LEFT_AXIS", "format": _AXIS_FMT},
                {"position": "BOTTOM_AXIS", "format": _AXIS_FMT,
                 "viewWindowOptions": {"viewWindowMode": "EXPLICIT", "viewWindowMin": 0}},
            ],
            "domains": [{"domain": {"sourceRange": {"sources": [cat]}}}],
            "series": [{"series": {"sourceRange": {"sources": [val]}},
                       "colorStyle": {"rgbColor": _hex_rgb(ACCENT_HEX)}}],
        },
        **_BG,
    }, "position": _pos(sid, row, col, w, h)}}}


def _line(title, cat, vals, sid, row, col, w, h):
    return {"addChart": {"chart": {"spec": {
        "title": title, "titleTextFormat": _TITLE_FMT, "fontName": "Roboto",
        "basicChart": {
            "chartType": "LINE", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
            "axis": [
                {"position": "BOTTOM_AXIS", "format": _AXIS_FMT},
                {"position": "LEFT_AXIS", "format": _AXIS_FMT},
            ],
            "domains": [{"domain": {"sourceRange": {"sources": [cat]}}}],
            "series": [{"series": {"sourceRange": {"sources": [v]}}} for v in vals],
        },
        **_BG,
    }, "position": _pos(sid, row, col, w, h)}}}


# Cột 0-based cho ô neo biểu đồ.  Trái = col A (0).  Phải = col H (7).
_LEFT, _RIGHT = 0, 7


def _chart_requests(sid):
    """Nguồn dữ liệu ở DẢI ĐÁY (row 60+). _grid end EXCLUSIVE."""
    R = []
    # LEFT — LINE xu hướng. Nguồn A60:C81 (header + ≤20 tháng).
    R.append(_line(
        "Xu hướng chi theo tháng",
        _grid(sid, 59, 81, 0, 1),
        [_grid(sid, 59, 81, 1, 2), _grid(sid, 59, 81, 2, 3)],
        sid, row=8, col=_LEFT, w=780, h=300,
    ))
    # LEFT — BAR Top 12 NCC. Nguồn A91:B104 (header row 91 + 12 dòng data).
    R.append(_bar_h(
        "Top 12 nhà cung cấp (theo chi mua vào)",
        _grid(sid, 90, 104, 0, 1), _grid(sid, 90, 104, 1, 2),
        sid, row=27, col=_LEFT, w=780, h=340,
    ))
    # RIGHT — BAR nhóm ĐT. Nguồn A84:B88.
    R.append(_bar_h(
        "Chi theo nhóm đối tượng",
        _grid(sid, 83, 88, 0, 1), _grid(sid, 83, 88, 1, 2),
        sid, row=8, col=_RIGHT, w=580, h=176,
    ))
    # RIGHT — BAR nhóm hàng. Nguồn E60:F69.
    R.append(_bar_h(
        "Cơ cấu chi theo nhóm hàng",
        _grid(sid, 59, 69, 4, 5), _grid(sid, 59, 69, 5, 6),
        sid, row=18, col=_RIGHT, w=580, h=232,
    ))
    # RIGHT — BAR chất lượng. Nguồn I60:J67.
    R.append(_bar_h(
        "Tính chính xác phân loại (số dòng)",
        _grid(sid, 59, 67, 8, 9), _grid(sid, 59, 67, 9, 10),
        sid, row=31, col=_RIGHT, w=580, h=192,
    ))
    # RIGHT — BAR Loại Break. Nguồn M60:N66.
    R.append(_bar_h(
        "Phân bố Loại Break",
        _grid(sid, 59, 67, 12, 13), _grid(sid, 59, 67, 13, 14),
        sid, row=42, col=_RIGHT, w=580, h=192,
    ))
    return R


def _iter_sources(ch):
    bc = ch["addChart"]["chart"]["spec"].get("basicChart") or {}
    for d in bc.get("domains", []):
        yield from d["domain"]["sourceRange"]["sources"]
    for se in bc.get("series", []):
        yield from se["series"]["sourceRange"]["sources"]


# =============================================================================
# Định dạng
# =============================================================================

def _sheet_meta(service, sid):
    return service.sheets_service.spreadsheets().get(spreadsheetId=sid).execute()


def _dash_chart_ids(meta):
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == DASH_TAB:
            return [c["chartId"] for c in s.get("charts", [])]
    return []


def _txt(color=None, bold=False, size=10):
    tf = {"fontFamily": "Roboto", "fontSize": size, "bold": bold}
    if color:
        tf["foregroundColorStyle"] = {"rgbColor": color}
    return tf


def _fmt(sid, r0, r1, c0, c1, bg=None, txt=None, halign=None, valign="MIDDLE", num=None, wrap=None):
    f, fields = {}, []
    if bg is not None:
        f["backgroundColorStyle"] = {"rgbColor": bg}; fields.append("backgroundColorStyle")
    if txt is not None:
        f["textFormat"] = txt; fields.append("textFormat")
    if halign:
        f["horizontalAlignment"] = halign; fields.append("horizontalAlignment")
    if valign:
        f["verticalAlignment"] = valign; fields.append("verticalAlignment")
    if num is not None:
        f["numberFormat"] = {"type": "NUMBER", "pattern": num}; fields.append("numberFormat")
    if wrap:
        f["wrapStrategy"] = wrap; fields.append("wrapStrategy")
    return {"repeatCell": {"range": _grid(sid, r0, r1, c0, c1),
                           "cell": {"userEnteredFormat": f},
                           "fields": "userEnteredFormat(" + ",".join(fields) + ")"}}


def _rowh(sid, row0, px, n=1):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": row0, "endIndex": row0 + n},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def _colw(sid, c0, c1, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def _rule(sid, row0, c0, c1, medium=False):
    return {"updateBorders": {"range": _grid(sid, row0, row0 + 1, c0, c1),
                              "bottom": {"style": "SOLID_MEDIUM" if medium else "SOLID",
                                         "colorStyle": {"rgbColor": ACCENT if medium else RULE}}}}


def _band(sid, r0, r1, c0, c1):
    return {"addBanding": {"bandedRange": {
        "range": _grid(sid, r0, r1, c0, c1),
        "rowProperties": {"headerColorStyle": {"rgbColor": WHITE},
                          "firstBandColorStyle": {"rgbColor": WHITE},
                          "secondBandColorStyle": {"rgbColor": ZEBRA}}}}}


# Bảng dải đáy: (header_row_0based, c0, c1, last_0based, num_c0)
_TABLES = [
    (59, 0, 3, 81, 1),     # Chi theo tháng   A60:C
    (59, 4, 7, 69, 5),     # Nhóm hàng        E60:G
    (59, 8, 11, 67, 9),    # Chất lượng       I60:K
    (59, 12, 14, 67, 13),  # Loại Break       M60:N
    (83, 0, 2, 89, 1),     # Nhóm ĐT          A84:B
    (90, 0, 2, 104, 1),    # Top 12 NCC       A91:B (row 91 là title, 92 header)
    (83, 4, 7, 114, 6),    # CAN_SOAT         E84:G
    (83, 8, 13, 99, 9),    # Lịch sử đối soát I84:M
]
_SUBTITLES = []  # tiêu đề bảng nằm trong LEGENDS/blocks; xử lý riêng bên dưới
# 'PHÂN TÍCH' (row 8) · 'SỐ LIỆU CHI TIẾT' (row 56) · 'CÁCH ĐỌC' (row 116) — 0-based
_TIER_ROWS = [7, 55, 115]


def _beautify(sid):
    r = []
    r.append({"updateSheetProperties": {
        "properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}},
        "fields": "gridProperties.hideGridlines"}})

    # RESET định dạng toàn vùng (values.clear KHÔNG xóa format cũ).
    _none = {"style": "NONE"}
    r.append({"repeatCell": {
        "range": _grid(sid, 0, 170, 0, 26),
        "cell": {"userEnteredFormat": {
            "backgroundColorStyle": {"rgbColor": WHITE},
            "textFormat": _txt(INK, size=10),
            "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "OVERFLOW_CELL",
            "borders": {"top": _none, "bottom": _none, "left": _none, "right": _none},
            "numberFormat": {"type": "TEXT"},
        }},
        "fields": ("userEnteredFormat(backgroundColorStyle,textFormat,horizontalAlignment,"
                   "verticalAlignment,wrapStrategy,borders,numberFormat)"),
    }})

    # Lưới cột (bội số 8). 4 cụm bảng, mỗi cụm nhãn + 2..4 cột số, xen cột spacer 16.
    r.append(_colw(sid, 0, 1, 168))   # A nhãn
    r.append(_colw(sid, 1, 3, 120))   # B,C số (đủ 9 chữ số)
    r.append(_colw(sid, 3, 4, 16))    # D spacer
    r.append(_colw(sid, 4, 5, 168))   # E nhãn
    r.append(_colw(sid, 5, 7, 120))   # F,G số
    r.append(_colw(sid, 7, 8, 16))    # H spacer
    r.append(_colw(sid, 8, 9, 144))   # I nhãn
    r.append(_colw(sid, 9, 12, 96))   # J,K,L số
    r.append(_colw(sid, 12, 13, 144))  # M nhãn
    r.append(_colw(sid, 13, 17, 72))  # N,O,P,Q số

    # Tiêu đề trang
    r.append(_fmt(sid, 0, 1, 0, 13, txt=_txt(INK, bold=True, size=18), halign="LEFT"))
    r.append(_fmt(sid, 1, 2, 0, 13, txt=_txt(MUTED, size=9), halign="LEFT"))
    r.append(_rowh(sid, 0, 36))
    r.append(_rowh(sid, 1, 16))
    r.append(_rule(sid, 1, 0, 17, medium=True))

    # Dải KPI: nhãn nhỏ (row 4), số lớn (row 5), phụ (row 6). Thẻ ở cột A,B,C và E,F,G.
    for c0, c1 in ((0, 3), (4, 7)):
        r.append(_fmt(sid, 3, 4, c0, c1, txt=_txt(MUTED, bold=True, size=9), halign="LEFT"))
        r.append(_fmt(sid, 4, 5, c0, c1, txt=_txt(INK, bold=True, size=13), halign="LEFT", num=FMT_MONEY))
        r.append(_fmt(sid, 5, 6, c0, c1, txt=_txt(MUTED, size=8), halign="LEFT"))
    r.append(_rowh(sid, 3, 16))
    r.append(_rowh(sid, 4, 30))
    r.append(_rowh(sid, 5, 14))
    r.append(_rule(sid, 6, 0, 17))

    # Dải TẦNG
    for row0 in _TIER_ROWS:
        r.append(_fmt(sid, row0, row0 + 1, 0, 13, txt=_txt(ACCENT, bold=True, size=12), halign="LEFT"))
        r.append(_rowh(sid, row0, 28))
        r.append(_rule(sid, row0, 0, 17, medium=True))

    # ---- Chú giải "CÁCH ĐỌC" dạng LIST: tiêu đề đậm + mỗi yếu tố 1 dòng ----
    #   Khối TRÁI span cột A:F (0-6) · khối PHẢI span cột H:N (7-13).
    for anchor, _title, items in LEGENDS:
        col = ord(anchor[0]) - 65
        row0 = int(anchor[1:]) - 1
        cspan = 7 if col == 0 else 14
        n = 1 + len(items)
        # gộp từng dòng theo trọn chiều rộng khối để text không bị bó trong 1 ô hẹp
        for k in range(n):
            r.append({"mergeCells": {"range": _grid(sid, row0 + k, row0 + k + 1, col, cspan),
                                     "mergeType": "MERGE_ALL"}})
        # tiêu đề khối
        r.append(_fmt(sid, row0, row0 + 1, col, cspan, txt=_txt(ACCENT, bold=True, size=9), halign="LEFT"))
        r.append(_rule(sid, row0, col, cspan))
        r.append(_rowh(sid, row0, 20))
        # các dòng yếu tố — văn phong viết, câu dài nên chừa 2 dòng wrap
        r.append(_fmt(sid, row0 + 1, row0 + n, col, cspan,
                      txt=_txt(INK, size=9), halign="LEFT", valign="TOP", wrap="WRAP"))
        for k in range(1, n):
            r.append(_rowh(sid, row0 + k, 30))

    # Tên bảng (khớp tên biểu đồ ở 'PHÂN TÍCH'): đậm navy, kẻ dày dưới.
    for anchor, c0, c1, _title in TABLE_TITLES:
        row0 = int(anchor[1:]) - 1
        r.append(_fmt(sid, row0, row0 + 1, c0, c1, txt=_txt(ACCENT, bold=True, size=10), halign="LEFT"))
        r.append(_rule(sid, row0, c0, c1, medium=True))
        r.append(_rowh(sid, row0, 22))

    # Bảng dải đáy: header muted + kẻ dưới + cột số phải + zebra.
    for (hr, c0, c1, last, ncol) in _TABLES:
        r.append(_fmt(sid, hr, hr + 1, c0, c1, txt=_txt(MUTED, bold=True, size=9), halign="LEFT"))
        r.append(_rule(sid, hr, c0, c1))
        r.append(_fmt(sid, hr + 1, last, ncol, c1, halign="RIGHT", num=FMT_MONEY, txt=_txt(INK, size=9)))
        r.append(_band(sid, hr, last, c0, c1))

    return r


async def _run(dry_run):
    service = GoogleSyncService()
    sid = os.getenv("GOOGLE_SHEET_ID")
    if not sid:
        print("[-] Chưa cấu hình GOOGLE_SHEET_ID.")
        return 1
    if not service.sheets_service:
        print("[-] Google Sheets service chưa kết nối (thiếu service_account.json?).")
        return 1

    global ARG, ACOL
    try:
        loc = _sheet_meta(service, sid)["properties"].get("locale", "")
    except Exception:
        loc = ""
    if loc.startswith(("vi", "de", "fr", "es", "it", "pt", "nl", "pl", "ru", "tr", "id")):
        ARG, ACOL = ";", "\\"
    else:
        ARG, ACOL = ",", ","
    print(f"[i] Locale = {loc or '(?)'} → phân cách công thức '{ARG}'.")

    blocks, helpers, note = _build_blocks()

    if dry_run:
        print("[dry-run] KHÔNG đụng Sheet.\n")
        print(note)
        print("\n--- block (anchor → dòng đầu) ---")
        for a, v in blocks + helpers:
            first = v[0][0] if v and v[0] else ""
            print(f"  {a:<6} {len(v):>2}d  {str(first)[:88]}")
        return 0

    if sheet_write_lock.locked():
        print("[-] sheet_write_lock đang bận. Thử lại sau.")
        return 2

    async with sheet_write_lock:
        service.ensure_tab_exists(DASH_TAB, sid)
        meta = _sheet_meta(service, sid)
        dash_id, dash_cols = None, 26
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == DASH_TAB:
                dash_id = s["properties"]["sheetId"]
                dash_cols = s["properties"].get("gridProperties", {}).get("columnCount", 26)
        if dash_id is None:
            print("[X] Không tạo/đọc được tab Dashboard.")
            return 3

        if dash_cols < 40:
            service.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=sid, body={"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": dash_id, "gridProperties": {"columnCount": 40}},
                    "fields": "gridProperties.columnCount"}}]}).execute()
            print(f"  [*] Mở rộng {dash_cols} → 40 cột.")

        old = _dash_chart_ids(meta)
        if old:
            service.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={"requests": [{"deleteEmbeddedObject": {"objectId": c}} for c in old]}).execute()
            print(f"  [*] Xóa {len(old)} chart cũ.")

        service.sheets_service.spreadsheets().values().clear(
            spreadsheetId=sid, range=OWNED_RANGE).execute()

        data = [{"range": f"{DASH_TAB}!{a}", "values": v} for a, v in blocks + helpers]
        service.sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
        print(f"  [*] Ghi {len(data)} block.")

        meta2 = _sheet_meta(service, sid)
        pre = [{"unmergeCells": {"range": {"sheetId": dash_id}}}]
        for s in meta2.get("sheets", []):
            if s["properties"]["title"] == DASH_TAB:
                for br in s.get("bandedRanges", []):
                    pre.append({"deleteBanding": {"bandedRangeId": br["bandedRangeId"]}})
        pre.append({"updateDimensionProperties": {
            "range": {"sheetId": dash_id, "dimension": "COLUMNS", "startIndex": 26, "endIndex": 38},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
        service.sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": pre}).execute()

        bfy = _beautify(dash_id)
        bands = [x for x in bfy if "addBanding" in x]
        rest = [x for x in bfy if "addBanding" not in x]
        charts = _chart_requests(dash_id)
        service.sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": rest + charts}).execute()

        ok = 0
        for bx in bands:
            try:
                service.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=sid, body={"requests": [bx]}).execute()
                ok += 1
            except Exception as e:
                print(f"  [!] Bỏ qua banding: {str(e)[:70]}")
        print(f"  [*] Làm đẹp, {ok}/{len(bands)} banding, {len(charts)} biểu đồ (1 LINE + 5 BAR).")

    print(f"\n[✓] Tab '{DASH_TAB}' sẵn sàng. Bảng gốc KHÔNG bị đụng.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Dựng tab Dashboard khung ngang (triết lý NN/g · Tableau · Tufte).")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in bố cục, không ghi Sheet.")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
