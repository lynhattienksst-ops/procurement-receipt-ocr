"""
Business Rules Engine for Procurement Receipt OCR.
Handles Business Object Categorization (DT1..DT4),
VAT Rate & Tax Calculation (Unit price before tax, % VAT, VAT amount, Total),
and Google Sheet Row Transformations (13-Column Standard Schema).
"""
import re
from typing import Dict, Any, List, Tuple, Optional


# Keywords for Category 1: E-commerce & Delivery
CAT1_KEYWORDS = [
    "shopee", "spx", "spx express", "lazada", "tiktok", "tiktok shop", "tiki",
    "grab", "grabexpress", "be", "be delivery", "ahamove", "giao hàng tiết kiệm",
    "ghtk", "giao hàng nhanh", "ghn", "viettel post", "viettelpost", "j&t", "j&t express",
    "ninja van", "best express", "chuyển phát nhanh", "phiếu gửi hàng", "vận đơn"
]

# Keywords for Category 2: Supermarkets, shopping malls, retail chains, electronic/apparel/book/pharma stores, general retail
CAT2_KEYWORDS = [
    "co.opmart", "coopmart", "co.op food", "winmart", "winmart+", "vinmart",
    "big c", "go!", "tops market", "lotte mart", "aeon", "aeon mall", "mega market",
    "emart", "circle k", "7-eleven", "7 eleven", "familymart", "gs25", "ministop",
    "bách hóa xanh", "bach hoa xanh", "guardian", "watsons", "con cưng", "concung",
    "thế giới di động", "dien may xanh", "fpt shop", "siêu thị", "mart", "store",
    "nhà sách", "fahasa", "tiệm", "cửa hàng", "shop", "trung tâm thương mại", "plaza", "mall",
    "điện máy", "thời trang", "gia dụng", "nhà thuốc", "pharmacity", "long châu"
]

# Keywords for Category 3: Food suppliers, agricultural produce, fresh food wholesale
CAT3_KEYWORDS = [
    "thực phẩm", "nông sản", "rau củ", "thịt", "cá", "hải sản", "gia súc", "gia cầm",
    "chợ đầu mối", "công ty thực phẩm", "food", "food supply", "nguyên liệu thực phẩm",
    "cung ứng thực phẩm", "trứng", "gạo", "gia vị", "suất ăn", "bếp", "bánh kẹo"
]

# Tag to filter out in Category 3
REMOVAL_TAG = "(loại bỏ)"


# =============================================================================
# Phân loại loại-hàng ở CẤP DÒNG (Data_Lines_V2, Cột M "Nhóm hàng")
# ADR line-item-grouping — trục kế toán chi phí VN (TK 152/153/156/627/641/642).
# 1 nguồn sự thật: dict dưới đây. KHÔNG lặp keyword vào regex trong hàm.
# =============================================================================

LINE_GROUP_CODES = [
    "NL_TP",     # Nguyên liệu / thực phẩm tươi        (TK 152 / 611)
    "HH_BAN",    # Hàng hóa mua để bán / vật tư tiêu hao (TK 156 / 152)
    "CCDC_TS",   # Công cụ, dụng cụ & tài sản           (TK 153 / 211)
    "DV_VC",     # Dịch vụ vận chuyển / logistics       (TK 641 / 627)
    "DV_SAN",    # Phí sàn TMĐT / hoa hồng              (TK 641)
    "DV_KHAC",   # Dịch vụ khác                        (TK 627 / 642)
    "VP_PHAM",   # Văn phòng phẩm & tiện ích            (TK 642)
    "KHAC",      # Khác (đã phân loại, không thuộc trên)
    "CAN_SOAT",  # Chưa phân loại được / độ tin cậy thấp → người soát
]

# THỨ TỰ ƯU TIÊN CỐ ĐỊNH khi duyệt (dịch vụ trước hàng hóa để "phí ..." không bị
# nuốt bởi từ khóa hàng hóa; HH_BAN xét cuối vì token bao bì dễ trùng).
LINE_GROUP_PRIORITY = [
    "DV_VC", "DV_SAN", "NL_TP", "CCDC_TS", "VP_PHAM", "DV_KHAC", "HH_BAN",
]

LINE_GROUP_KEYWORDS: Dict[str, List[str]] = {
    "DV_VC": [
        "phí vận chuyển", "phí ship", "cước", "cước vận chuyển", "giao hàng",
        "phí giao hàng", "freight", "logistics", "phí giao vận", "delivery fee",
        "shipping fee", "chi phí vận chuyển", "vận chuyển",
        "phí vc", "tiền ship", "phí gh",
    ],
    "DV_SAN": [
        "phí sàn", "phí dịch vụ shopee", "phí dịch vụ lazada", "phí dịch vụ tiktok",
        "hoa hồng", "phí thanh toán", "phí quảng cáo", "phí cố định", "phí xử lý đơn",
        "phí hạ tầng", "commission", "phí dịch vụ tmđt", "phí nền tảng",
    ],
    "NL_TP": [
        "rau", "củ", "quả", "trái cây", "hoa quả", "thịt", "heo", "bò", "gà", "cá",
        "tôm", "cua", "mực", "ghẹ", "trứng", "gạo", "nếp", "gia vị", "nước mắm",
        "dầu ăn", "hạt nêm", "bột ngọt", "nấm", "hải sản", "thủy sản", "bún", "phở",
        "mì", "hủ tiếu", "đường", "muối", "sữa tươi", "tàu hũ", "đậu", "nông sản",
        # Nước chấm / sốt / gia vị đóng chai
        "nước tương", "tương cà", "tương ớt", "tương đen", "xì dầu", "dầu hào",
        "sa tế", "mắm", "sốt", "syrup", "siro", "sirô", "mật ong", "đường phèn",
        # Trà / cà phê / bột pha chế
        "trà", "oolong", "ô long", "hồng trà", "lục trà", "cà phê", "cafe", "caphe",
        "cacao", "ca cao", "matcha", "bột sữa", "bột béo", "kem béo", "topping",
        "trân châu", "thạch", "mứt", "cốt dừa", "nước cốt",
        # Bánh / thực phẩm chế biến sẵn
        "bánh", "croissant", "pain au chocolate", "pate chaud", "pate", "pâté",
        "sandwich", "phô mai", "phomai", "bơ lạt", "bột mì", "men", "kem tươi",
        "whipping", "chocolate", "socola", "sô cô la",
        # Rau củ khô / hạt / đồ khô
        "hành", "tỏi", "gừng", "sả", "ớt", "tiêu", "hạt", "mè", "vừng", "nước dừa",
        "sữa đặc", "sữa hạt", "nước ép", "nước trái cây",
        # Trái cây đóng lon / đóng hộp, hoa - thảo mộc pha trà, bột & sữa pha chế
        "đào lon", "vải lon", "vài lon", "lon boddob", "nhãn lon", "chôm chôm lon",
        "cà chua", "quế tây", "sweet basil", "húng quế",
        "bông cúc", "hoa cúc", "cúc khô", "nụ hồng", "hoa hồng khô", "hoa lài",
        "atiso", "hoa đậu biếc", "cỏ ngọt", "la hán", "kỷ tử", "táo đỏ", "táo tàu",
        "long nhãn", "hạt chia", "sen", "đông trùng",
        "sấy khô", "sấy dẻo", "sấy giòn", "cam sấy", "dừa sữa", "kem dừa", "thập cẩm",
        "nif lon", "kỳ tử",
        "bột frappe", "frappe", "milk foam", "milkfoam", "bột milk", "bột dp",
        "bột pha", "bột hoa anh đào", "bột khoai", "bột taro", "bột trà",
        "sữa tiệt trùng", "sữa tươi tiệt trùng", "milklab", "sữa milk", "rich vị sữa",
        "rich vị", "vị sữa", "creamer", "kem sữa", "sữa bột", "condensed",
    ],
    "CCDC_TS": [
        "nồi", "chảo", "xoong", "dao", "thớt", "tô", "chén", "đĩa", "dĩa", "ly",
        "cốc", "khay", "kệ", "bàn", "ghế", "tủ", "máy", "bếp", "quạt", "đèn",
        "thiết bị", "dụng cụ",
        # Dụng cụ pha chế / định lượng / bar
        "ca đong", "cốc đong", "thìa định lượng", "muỗng", "muống", "phới", "phới vét",
        "thìa", "thia", "nĩa", "nỉa", "đũa", "vá", "sạn", "vích",
        "spatula", "barspoon", "bar spoon", "xúc đá", "gắp đá", "kẹp gắp", "kẹp đá",
        "rây", "vợt", "chày", "cối", "khuôn", "cân", "cân điện tử", "đồng hồ bấm giờ",
        "bình xịt kem", "cream whipper", "bình bơm", "bình đựng", "bình ủ", "bình lắc",
        "shaker", "jigger", "lọ rắc", "hũ", "hộp đựng", "khay đựng", "rổ", "giá đỡ",
        "kệ inox", "xe đẩy", "tủ mát", "tủ đông", "tủ lạnh", "lò", "lò nướng",
        "máy xay", "máy ép", "máy đánh", "máy pha", "máy làm", "bình thủy",
        "khoan", "kìm", "cọ", "cưa", "búa", "tua vít", "mũi khoan", "lưỡi cưa",
        "két đựng tiền", "két sắt", "loa", "amply", "ampli", "micro", "tivi", "tv",
        "led", "màn hình", "camera", "quầy", "tách sứ", "bộ tách", "ấm", "phin",
        "fin", "cây lau", "chổi", "sọt rác", "thùng rác", "thau", "chậu",
    ],
    "VP_PHAM": [
        "giấy", "bút", "viết", "mực in", "sổ", "kẹp", "băng keo", "băng dính",
        "file", "bìa", "ghim", "phong bì", "tiền điện", "tiền nước", "cước internet",
        "hóa đơn điện", "hóa đơn nước",
        "máy in", "giấy in", "giấy in bill", "cuộn bill", "giấy nhiệt", "mực máy in",
    ],
    "DV_KHAC": [
        "thuê", "cho thuê", "phần mềm", "license", "marketing", "quảng cáo",
        "sửa chữa", "bảo trì", "tư vấn", "phí ngân hàng", "phí chuyển khoản",
        "phí quản lý", "dịch vụ vệ sinh",
        "thi công", "lắp đặt", "lắp ráp", "vận hành", "in ấn", "thiết kế",
        "chụp hình", "quay phim", "đồng phục", "áo đồng phục", "thẻ bảo hành",
        "gói bảo hành", "phí dịch vụ", "công lắp", "nhân công", "công thợ",
        "công chuyển", "tiền công", "công tháo", "công vệ sinh", "phí thi công",
    ],
    "HH_BAN": [
        "túi", "bao bì", "hộp", "ly nhựa", "ống hút", "màng bọc", "khăn giấy",
        "nilon", "nylon", "thùng carton", "hộp giấy", "tem", "nhãn",
        # Vật tư tiêu hao / bao gói phục vụ bán hàng
        "ly giấy", "cốc giấy", "nắp ly", "nắp cốc", "muỗng nhựa", "nĩa nhựa",
        "dao nhựa", "hộp nhựa", "hộp bã mía", "tô giấy", "chén nhựa", "dĩa nhựa",
        "khay giấy", "giấy gói", "giấy lót", "giấy thấm dầu", "màng pe", "màng bọc thực phẩm",
        "dây rút", "dây buộc", "que khuấy", "que tre", "tăm", "khăn ướt", "khăn lạnh",
        "găng tay", "bao tay", "bao tay cao su", "khẩu trang", "túi zip", "túi zipper",
        "bịch", "bao pp", "bao bố", "thùng xốp", "xốp", "đá gel", "đá khô",
        "ống ldpe", "ống pe", "ống nhựa", "ống hút giấy", "ống hút nhựa", "ldpe",
        "dây thun", "thun", "chun", "kẽm", "bao pe",
        # Vật tư lắp đặt / phần cứng lặt vặt (hóa đơn thi công, sửa chữa)
        "nẹp", "co góc", "co nối", "ống đồng", "ống 9", "ống 6", "béc phun", "béc",
        "ren 13", "ren 9", "lọc 3 cấp", "đuôi chuột", "bản mã", "bát gốc", "que 30",
        "vật tư phụ",
    ],
}

# Gợi ý từ đơn vị tính khi tên hàng không khớp keyword nào.
_UNIT_HINT_NL_TP = {"kg", "g", "gram", "bó", "mớ", "con", "quả", "trái", "lít", "lit", "chục", "ký"}
_UNIT_HINT_DV_KHAC = {"lần", "tháng", "gói"}

# Tên hàng "rỗng nghĩa" — dòng gộp / placeholder → cần người soát.
_LOW_CONF_NAMES = {
    "", "đơn hàng tmđt", "don hang tmdt", "hóa đơn", "hoa don", "hóa đơn mua hàng",
    "hoá đơn mua hàng", "hoa don mua hang", "hàng hóa viết tay", "hang hoa viet tay",
    "cung ứng thực phẩm", "cung ung thuc pham",
}


def classify_line_item(item_name: Any, unit: Any = "", is_dt1: bool = False) -> Tuple[str, str]:
    """
    Phân loại 1 dòng hàng vào 1 nhóm loại-hàng (trục kế toán chi phí).

    Trả về ``(group_code, source)`` với:
      - ``group_code`` ∈ ``LINE_GROUP_CODES``
      - ``source`` ∈ {"rule:keyword", "rule:category", "unit_hint", "auto_default", "low_conf"}
        ("manual" chỉ do người dùng sửa tay trên verify.html — không sinh ở đây)

    ``is_dt1=True`` (dòng gộp cả đơn của nhóm Sàn TMĐT / Vận chuyển): dòng gộp trộn
    nhiều loại hàng nên KHÔNG phân chi tiết. Chỉ 3 khả năng — hai loại chứng từ/chi
    phí tách bạch được bằng từ khóa (``DV_SAN`` phí sàn, ``DV_VC`` phí vận chuyển),
    phần còn lại là tiền hàng hóa mua vào → mặc định ``HH_BAN`` (nguồn ``rule:category``).
    """
    name = re.sub(r"\s+", " ", str(item_name or "").strip().lower())
    u = str(unit or "").strip().lower()

    if is_dt1:
        for group in ("DV_VC", "DV_SAN"):
            for kw in LINE_GROUP_KEYWORDS.get(group, []):
                if kw in name:
                    return group, "rule:keyword"
        return "HH_BAN", "rule:category"

    # 2. Duyệt keyword theo thứ tự ưu tiên cố định (substring match, đủ cho tiếng Việt có dấu).
    for group in LINE_GROUP_PRIORITY:
        for kw in LINE_GROUP_KEYWORDS.get(group, []):
            if kw in name:
                return group, "rule:keyword"

    # 3. Gợi ý từ đơn vị tính.
    if u in _UNIT_HINT_NL_TP:
        return "NL_TP", "unit_hint"
    if u in _UNIT_HINT_DV_KHAC:
        return "DV_KHAC", "unit_hint"

    # 4. Không match.
    if name in _LOW_CONF_NAMES:
        return "CAN_SOAT", "low_conf"
    return "KHAC", "auto_default"


def parse_vietnamese_number(val: Any) -> float:
    """
    Parse numbers formatted in Vietnamese style (e.g. '387.997,00 đ', '100.000,00', '1.250.000')
    or raw numbers, preserving exact numbers without rounding.
    100.000,00 = 100000 (một trăm ngàn).
    """
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    for suffix in ["đ", "Đ", "vnđ", "VNĐ", "vnd", "VND", "₫", "$"]:
        s = s.replace(suffix, "")
    s = s.strip()
    if not s:
        return 0.0

    if "." in s and "," in s:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in [1, 2]:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s and "," not in s:
        parts = s.split(".")
        if len(parts) == 2:
            if parts[0] == "0" or (len(parts[1]) in [1, 2] and len(parts[0]) <= 3 and not (len(parts[1]) == 2 and parts[1] == "00")):
                # Decimal e.g. 0.5, 0.50, 1.25, 2.75
                pass
            else:
                s = s.replace(".", "")
        else:
            s = s.replace(".", "")

    try:
        val_f = float(s)
        return int(val_f) if val_f.is_integer() else val_f
    except ValueError:
        return 0.0


def clean_num(val: Any) -> float:
    """
    Coerce any sheet cell (may carry '%', 'đ', 'VND', thousands separators) to a
    plain float, always returning a number (0.0 on failure). Thin wrapper over
    parse_vietnamese_number with an extra comma-stripping fallback.
    Used by server.py's /api/v1/sheets/reconcile-totals endpoint.
    """
    if val is None or str(val).strip() == "":
        return 0.0
    try:
        s = str(val).replace("%", "").replace("đ", "").replace("VND", "").replace("vnd", "").strip()
        num = parse_vietnamese_number(s)
        return float(num)
    except Exception:
        try:
            return float(str(val).replace(",", "").strip())
        except Exception:
            return 0.0


def format_vietnamese_currency(val: Any) -> str:
    """
    Format a numeric amount to Vietnamese number format with decimals and currency symbol ' đ'.
    """
    if val is None or val == "":
        return "0,00 đ"
    
    if isinstance(val, str):
        val_str = val.strip()
        if val_str.endswith(" đ"):
            return val_str
        try:
            clean_str = val_str.replace(".", "").replace(",", ".")
            val = float(clean_str)
        except ValueError:
            return f"{val_str} đ"

    try:
        f = float(val)
        formatted = f"{f:,.2f}"
        parts = formatted.split('.')
        integer_part = parts[0].replace(",", ".")
        decimal_part = parts[1]
        return f"{integer_part},{decimal_part} đ"
    except (ValueError, TypeError):
        return "0,00 đ"


def detect_business_category(data: Dict[str, Any]) -> Tuple[int, str]:
    """
    Content-Driven Business Object Categorization Engine (DT1..DT4).
    Analyzes full semantic content: Legal entity, Tax ID, Item lines, Marketplace/courier brands, and Handwriting.
    """
    merchant_name = str(data.get("merchant_name") or data.get("seller_name") or data.get("company_name") or "").strip()
    merchant_lower = merchant_name.lower()
    seller_tax_id = str(data.get("seller_tax_id") or data.get("tax_id") or "").strip()
    
    # 1. Line items text content
    line_items = data.get("line_items") or []
    items_text = " ".join([str(it.get("item_name") or "") for it in line_items]).lower()
    
    # Text for domain checking
    core_entity_text = f"{merchant_lower} {items_text}"
    notes = str(data.get("notes") or "").lower()
    full_text = f"{merchant_lower} {items_text} {notes}"

    # Check flags from OCR
    category_hint = str(data.get("category_hint") or "").upper().strip()
    is_company_flag = bool(data.get("is_company_invoice", False))
    is_ecom_flag = bool(data.get("is_ecom_shipping_label", False))
    is_handwritten_flag = bool(data.get("is_handwritten", False))

    # 1. Detect Legal Entity / Company Indicators
    has_company_prefix = any(p in merchant_lower for p in [
        "công ty", "tnhh", "cổ phần", "cp ", "dntn", "doanh nghiệp", "chi nhánh", "tập đoàn", "xí nghiệp", "hợp tác xã"
    ])
    has_valid_tax_id = bool(re.search(r"\b\d{10}(-\d{3})?\b", seller_tax_id or full_text))
    is_confirmed_company = has_company_prefix or has_valid_tax_id or is_company_flag

    # 2. Check Food / Agricultural Produce keywords using exact word boundaries
    food_pattern = re.compile(
        r"\b(thực phẩm|nông sản|rau củ|rau|củ quả|trái cây|hoa quả|thịt|cá tươi|hải sản|thủy sản|tôm|cua|mực|"
        r"trứng|gạo|nếp|gia vị|nước mắm|dầu ăn|hạt nêm|nấm tươi|nấm rơm|thực phẩm tươi|thực phẩm sạch|"
        r"kamereo|la paz foods|la paz|thực phẩm số một|food supply|fresh food)\b",
        re.IGNORECASE
    )
    has_food_keywords = bool(food_pattern.search(core_entity_text))

    # 3. Check E-Commerce / Courier Platform keywords
    ecom_pattern = re.compile(
        r"\b(shopee|spx|spx express|lazada|tiktok shop|tiktok|tiki|"
        r"giao hàng tiết kiệm|ghtk|giao hàng nhanh|ghn|viettel post|viettelpost|"
        r"j&t|j&t express|ninja van|best express|grabexpress|ahamove|phiếu gửi hàng|vận đơn)\b",
        re.IGNORECASE
    )
    has_ecom_brand = bool(ecom_pattern.search(full_text))

    # 4. Check Supermarkets / Retail chains / General Retail businesses
    supermarket_pattern = re.compile(
        r"\b(co\.opmart|coopmart|co\.op food|winmart|winmart\+|vinmart|"
        r"big c|go!|tops market|lotte mart|aeon|aeon mall|mega market|"
        r"emart|circle k|7-eleven|7 eleven|familymart|gs25|ministop|"
        r"bách hóa xanh|bach hoa xanh|guardian|watsons|con cưng|concung|"
        r"thế giới di động|dien may xanh|fpt shop|siêu thị|khánh vy home|khánh vy|shop ly|shop mambu|"
        r"nhà sách|fahasa|phương nam|an phước|viettel store|cellphones|hoàng hà mobile|"
        r"hacom|gearvn|an phát|vinh nguyễn|tci|kidsplaza|bibomart|hasaki|avashop|yame|coolmate|routine|"
        r"nhà thuốc|pharmacity|long châu|an khang|trung tâm thương mại|plaza|mall|cửa hàng bán lẻ)\b",
        re.IGNORECASE
    )
    has_supermarket_brand = bool(supermarket_pattern.search(core_entity_text))

    # =========================================================================
    # MULTI-LAYER DECISION MATRIX
    # =========================================================================

    # TẦNG 1: DOANH NGHIỆP / CÔNG TY CÓ PHÁP NHÂN HOẶC MST
    if is_confirmed_company:
        if has_food_keywords or category_hint == "DT3":
            return 3, "Doanh nghiệp cung ứng thực phẩm & Nông sản"
        return 2, "Doanh nghiệp, siêu thị & Bán lẻ chung quy"

    # TẦNG 2: SÀN THƯƠNG MẠI ĐIỆN TỬ & VẬN CHUYỂN BƯU CHÍNH (DT1)
    if (has_ecom_brand or is_ecom_flag or category_hint == "DT1") and not is_confirmed_company:
        return 1, "Sàn thương mại điện tử & Dịch vụ vận chuyển"

    # TẦNG 3: SIÊU THỊ & DOANH NGHIỆP BÁN LẺ CHUNG QUY (DT2)
    if has_supermarket_brand or category_hint == "DT2":
        return 2, "Doanh nghiệp, siêu thị & Bán lẻ chung quy"

    # TẦNG 4: HỘ KINH DOANH NÔNG SẢN / THỰC PHẨM (DT3)
    if has_food_keywords or category_hint == "DT3":
        return 3, "Doanh nghiệp cung ứng thực phẩm & Nông sản"

    # TẦNG 5: HÓA ĐƠN VIẾT TAY & KHÔNG XÁC ĐỊNH DANH TÍNH (DT4)
    if is_handwritten_flag or not merchant_name or merchant_lower in ["n/a", "không xác định", "unknown", "none", "", "hóa đơn", "phiếu tính tiền", "phiếu thu"]:
        return 4, "Hóa đơn viết tay & Không xác định danh tính"

    # MẶC ĐỊNH: Bán lẻ / Doanh nghiệp (DT2)
    return 2, "Doanh nghiệp, siêu thị & Bán lẻ chung quy"


def check_vat_alert(data: Dict[str, Any]) -> Tuple[bool, float, str]:
    """
    Check if the receipt contains Value Added Tax (VAT / GTGT).
    Returns (has_vat, vat_amount, alert_message)
    """
    tax_amount = float(data.get("tax_amount") or data.get("vat_amount") or 0)
    tax_rate = float(data.get("tax_rate") or data.get("vat_rate") or 0)
    has_vat = False
    alert_message = ""

    if tax_amount > 0 or tax_rate > 0:
        has_vat = True
        rate_str = f" ({tax_rate*100:.0f}%)" if tax_rate > 0 else ""
        alert_message = f"CẢNH BÁO: Hóa đơn có thuế GTGT (VAT){rate_str} là {tax_amount:,.0f} VNĐ."
    else:
        notes = str(data.get("notes") or "")
        if re.search(r"\b(vat|gtgt|thuế|tax)\b", notes, re.IGNORECASE):
            has_vat = True
            alert_message = "CẢNH BÁO: Hóa đơn có ghi nhận thông tin thuế VAT."

    return has_vat, tax_amount, alert_message


def compute_item_vat_and_pricing(
    item: Dict[str, Any],
    invoice_tax_rate: float,
    invoice_tax_amount: float,
    is_price_inclusive: bool,
    invoice_subtotal: float
) -> Tuple[Any, str, Any, Any]:
    """
    Compute item-level pricing and VAT details.
    Đơn giá mặc định luôn trong trạng thái CHƯA TÍNH THUẾ VAT.

    Returns:
        (unit_price_before_tax, vat_rate_str, vat_amount, line_total)
    """
    try:
        qty = float(item.get("item_quantity") or 1)
    except (ValueError, TypeError):
        qty = 1.0

    raw_price = item.get("item_price")
    raw_total = item.get("total_price")

    # Determine rate r
    r = 0.0
    item_rate = item.get("vat_rate") or item.get("tax_rate")
    if item_rate is not None:
        try:
            r = float(str(item_rate).replace("%", "").strip())
            if r > 1.0: # e.g. 10 instead of 0.10
                r = r / 100.0
        except (ValueError, TypeError):
            r = 0.0
    elif invoice_tax_rate > 0:
        r = invoice_tax_rate if invoice_tax_rate <= 1.0 else invoice_tax_rate / 100.0
    elif invoice_tax_amount > 0 and invoice_subtotal > 0:
        r = round(invoice_tax_amount / invoice_subtotal, 4)

    # Item specific VAT amount if explicitly present
    item_vat_val = item.get("vat_amount") or item.get("tax_amount")
    try:
        if item_vat_val is not None:
            item_vat_val = float(item_vat_val)
    except (ValueError, TypeError):
        item_vat_val = None

    unit_price_before_tax = 0.0
    vat_amount = 0.0
    line_total = 0.0

    if raw_price is not None:
        try:
            p = float(raw_price)
            if is_price_inclusive and r > 0:
                # Giá đã gồm VAT -> quy đổi về chưa VAT
                unit_price_before_tax = round(p / (1.0 + r), 2)
                vat_amount = item_vat_val if item_vat_val is not None else round(qty * unit_price_before_tax * r, 2)
                line_total = round(qty * p, 2)
            else:
                unit_price_before_tax = round(p, 2)
                vat_amount = item_vat_val if item_vat_val is not None else round(qty * unit_price_before_tax * r, 2)
                line_total = round(qty * unit_price_before_tax + vat_amount, 2)
        except (ValueError, TypeError):
            try:
                unit_price_before_tax = float(raw_price)
            except (ValueError, TypeError):
                unit_price_before_tax = 0.0
    elif raw_total is not None:
        try:
            tot = float(raw_total)
            if is_price_inclusive and r > 0:
                unit_price_before_tax = round((tot / qty) / (1.0 + r), 2)
                vat_amount = item_vat_val if item_vat_val is not None else round(qty * unit_price_before_tax * r, 2)
                line_total = round(tot, 2)
            else:
                unit_price_before_tax = round(tot / qty, 2)
                vat_amount = item_vat_val if item_vat_val is not None else round(qty * unit_price_before_tax * r, 2)
                line_total = round(tot + vat_amount, 2)
        except (ValueError, TypeError):
            unit_price_before_tax = 0.0
            try:
                line_total = float(raw_total)
            except (ValueError, TypeError):
                line_total = 0.0
    else:
        unit_price_before_tax = 0.0
        line_total = 0.0

    vat_rate_str = f"{int(r*100) if (r*100).is_integer() else round(r*100, 2)}%" if r > 0 else "0%"
    vat_amount_out = vat_amount if (r > 0 or vat_amount > 0) else 0

    return unit_price_before_tax, vat_rate_str, vat_amount_out, line_total


def assemble_notes(
    existing_notes: str = "",
    tracking_no: str = "",
    vat_alert: str = "",
    fallback_default: str = "",
    merchant_phone: str = "",
    merchant_email: str = "",
    seller_tax_id: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    customer_email: str = ""
) -> str:
    """
    Combine all notes:
    - Appends contact info: Company phone, Person in charge Name - SĐT, MST, Mail
    - Appends tracking number if category is DT1
    - Appends VAT alerts
    """
    parts = []
    
    # 1. Loại bỏ ghi chú gốc (existing_notes) khỏi cú pháp ghép nối của hóa đơn mới

    # 2. Company/Merchant Phone
    mp = str(merchant_phone or "").strip()
    if mp:
        parts.append(f"SĐT Công ty: {mp}")

    # 3. Person in charge (Định dạng: Tên - SĐT, không khuyết tên)
    cname = str(customer_name or "").strip()
    cp = str(customer_phone or "").strip()
    if cp and cname:
        parts.append(f"{cname} - {cp}")

    # 4. Tax ID (Mã số thuế)
    tax_id = str(seller_tax_id or "").strip()
    if tax_id:
        parts.append(f"MST: {tax_id}")

    # 5. Email (Mail)
    me = str(merchant_email or "").strip()
    ce = str(customer_email or "").strip()
    if me:
        parts.append(f"Mail: {me}")
    if ce and ce != me:
        parts.append(f"Mail người nhận: {ce}")

    # 6. Tracking number (Đối với DT1 ghi thêm mã vận đơn)
    tr = str(tracking_no or "").strip()
    if tr:
        parts.append(f"Mã vận đơn: {tr}")

    # 7. VAT alert
    va = str(vat_alert or "").strip()
    if va:
        va_label = f"[VAT] {va}" if not va.startswith("[") else va
        parts.append(va_label)

    # 8. Fallback default
    if not parts and fallback_default:
        parts.append(fallback_default)

    return " | ".join(parts).strip() if parts else ""


def normalize_datetime_vn(date_val: Any, time_val: Any = "", prefix_quote: bool = True) -> str:
    """
    Standardize datetime into Vietnamese Accounting format: 'HH:MM:SS DD-MM-YYYY'.
    Examples:
      - '2026-08-28', '14:30:00' -> "'14:30:00 28-08-2026"
      - '28/08/2026', '14:30'    -> "'14:30:00 28-08-2026"
      - '2026-08-28 14:30:00'    -> "'14:30:00 28-08-2026"
      - '2026-08-28'             -> "'00:00:00 28-08-2026"
    """
    if date_val is None:
        date_val = ""
    if time_val is None:
        time_val = ""

    raw_combined = f"{str(date_val).strip()} {str(time_val).strip()}".strip()
    raw_combined = raw_combined.lstrip("'")
    if not raw_combined:
        return ""

    # 1. Extract Time Component (HH:MM:SS or HH:MM)
    time_part = "00:00:00"
    m_time_full = re.search(r"\b(\d{1,2}):(\d{1,2}):(\d{1,2})\b", raw_combined)
    if m_time_full:
        hh, mi, ss = m_time_full.groups()
        time_part = f"{int(hh):02d}:{int(mi):02d}:{int(ss):02d}"
        # Remove matched time to avoid confusion with date
        raw_combined = raw_combined[:m_time_full.start()] + " " + raw_combined[m_time_full.end():]
    else:
        m_time_short = re.search(r"\b(\d{1,2}):(\d{1,2})\b", raw_combined)
        if m_time_short:
            hh, mi = m_time_short.groups()
            time_part = f"{int(hh):02d}:{int(mi):02d}:00"
            raw_combined = raw_combined[:m_time_short.start()] + " " + raw_combined[m_time_short.end():]

    # 2. Extract Date Component (YYYY-MM-DD or DD-MM-YYYY)
    date_part = ""
    # Check YYYY-MM-DD or YYYY/MM/DD or YYYY.MM.DD
    m_ymd = re.search(r"\b(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})\b", raw_combined)
    if m_ymd:
        yyyy, mm, dd = m_ymd.groups()
        date_part = f"{int(dd):02d}-{int(mm):02d}-{yyyy}"
    else:
        # Check DD-MM-YYYY or DD/MM/YYYY or DD.MM.YYYY
        m_dmy = re.search(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})\b", raw_combined)
        if m_dmy:
            dd, mm, yyyy = m_dmy.groups()
            date_part = f"{int(dd):02d}-{int(mm):02d}-{yyyy}"
        else:
            # Check 8-digit compact YYYYMMDD
            m_comp = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", raw_combined)
            if m_comp:
                yyyy, mm, dd = m_comp.groups()
                date_part = f"{int(dd):02d}-{int(mm):02d}-{yyyy}"
            else:
                # Fallback: keep leftover text as date_part
                date_part = raw_combined.strip()

    if not date_part:
        return ""

    res = f"{time_part} {date_part}".strip()
    return f"'{res}" if prefix_quote else res


def format_receipt_to_sheet_rows(
    data: Dict[str, Any],
    category_id: int,
    current_index: int = 1
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """
    Format extracted receipt JSON into standard 14-column Google Sheet rows.
    """
    dt_code = f"DT{category_id}{current_index:04d}"

    date_str = str(data.get("transaction_date") or data.get("date") or "").strip()
    time_str = str(data.get("transaction_time") or data.get("time") or "").strip()
    full_datetime = normalize_datetime_vn(date_str, time_str, prefix_quote=True)

    merchant_name = str(data.get("merchant_name") or data.get("seller_name") or data.get("company_name") or "").strip()
    merchant_addr = str(data.get("merchant_address") or data.get("seller_address") or "").strip()
    customer_name = str(data.get("customer_name") or data.get("buyer_name") or "").strip()
    customer_addr = str(data.get("customer_address") or "").strip()
    doc_code = str(data.get("invoice_number") or data.get("receipt_number") or data.get("tracking_number") or "")
    order_id = str(data.get("order_id") or "")
    total_amount = data.get("total_amount")

    has_vat, vat_amount, vat_alert = check_vat_alert(data)
    
    invoice_tax_rate = float(data.get("tax_rate") or data.get("vat_rate") or 0)
    invoice_tax_amount = float(data.get("tax_amount") or data.get("vat_amount") or 0)
    is_price_inclusive = bool(data.get("is_price_inclusive_of_vat", False))
    invoice_subtotal = float(data.get("subtotal_amount") or data.get("subtotal") or 0)

    line_items = data.get("line_items") or []
    rows: List[List[Any]] = []

    # =========================================================================
    # ĐỐI TƯỢNG 1: Sàn TMĐT & Dịch vụ giao nhận hàng
    # =========================================================================
    if category_id == 1:
        tracking_no = str(data.get("tracking_number") or "").strip()
        items_note = assemble_notes(
            existing_notes=data.get("notes"),
            tracking_no=tracking_no,
            vat_alert=vat_alert if has_vat else ""
        )

        items_summary_list = []
        if line_items:
            for it in line_items:
                iname = str(it.get("item_name") or "").strip()
                iqty = it.get("item_quantity") or 1
                items_summary_list.append(f"- {iname} (SL: {iqty})")
                
        item_names_str = "\n".join(items_summary_list) if items_summary_list else "Đơn hàng TMĐT"

        # Chuẩn hóa Tên công ty / Sàn TMĐT (DT1)
        normalized_merchant = merchant_name
        base_platform = ""
        m_lower = merchant_name.lower()
        if "shopee" in m_lower or "spx" in m_lower:
            base_platform = "Shopee"
        elif "lazada" in m_lower or "lex" in m_lower:
            base_platform = "Lazada"
        elif "tiktok" in m_lower:
            base_platform = "TikTok Shop"
        elif "tiki" in m_lower:
            base_platform = "Tiki"
        elif "ghtk" in m_lower or "tiết kiệm" in m_lower:
            base_platform = "Giao Hàng Tiết Kiệm"
        elif "ghn" in m_lower or "nhanh" in m_lower:
            base_platform = "Giao Hàng Nhanh"
        elif "viettel" in m_lower:
            base_platform = "Viettel Post"
        elif "j&t" in m_lower:
            base_platform = "J&T Express"

        # Tách tên shop bằng cách loại bỏ các từ khóa vận chuyển chung
        shop_name = merchant_name
        remove_words = ["shopee", "spx", "express", "cb", "lazada", "lex", "tiktok shop", "tiktok", "tiki", "ghtk", "ghn", "viettel post", "viettel", "j&t", "giao hàng nhanh", "giao hàng tiết kiệm", "logistics", "delivery"]
        for w in remove_words:
            shop_name = re.sub(r'(?i)\b' + re.escape(w) + r'\b', '', shop_name)
            
        # Loại bỏ các ký tự đặc biệt lặt vặt còn sót lại (dấu gạch ngang, phẩy ở đầu/cuối)
        shop_name = re.sub(r'^[\s\-_,]+|[\s\-_,]+$', '', shop_name)
        shop_name = " ".join(shop_name.split()).strip()

        if base_platform:
            normalized_merchant = f"{base_platform} - {shop_name}" if shop_name else base_platform

        # Coi cả đơn hàng là 1 kiện lớn để dùng chung hàm tính VAT
        dummy_item = {
            "item_quantity": 1,
            "total_price": total_amount if total_amount is not None else 0
        }
        u_price, v_rate, v_amt, l_total = compute_item_vat_and_pricing(
            dummy_item, invoice_tax_rate, invoice_tax_amount, is_price_inclusive, invoice_subtotal
        )

        row = [
            dt_code,                                                # Cột A: Mã đối tượng DT1XXXX
            full_datetime,                                          # Cột B: Ngày, tháng, năm
            normalized_merchant,                                    # Cột C: Tên dịch vụ / Sàn TMĐT đã chuẩn hóa
            merchant_addr,                                          # Cột D: Địa chỉ bên bán
            customer_addr,                                          # Cột E: Địa chỉ bên nhận
            order_id if order_id else doc_code,                     # Cột F: Mã đơn hàng
            item_names_str,                                         # Cột G: Tên hàng hóa
            1,                                                      # Cột H: Số lượng (1 đơn)
            u_price,                                                # Cột I: Đơn giá (chưa VAT)
            v_rate,                                                 # Cột J: % VAT
            v_amt,                                                  # Cột K: VAT
            l_total,                                                # Cột L: Thành tiền (Tổng tiền đơn)
            customer_name,                                          # Cột M: Người nhận hàng
            items_note                                              # Cột N: Mã vận đơn & Ghi chú
        ]
        rows.append(row)

    # =========================================================================
    # ĐỐI TƯỢNG 2: Doanh nghiệp, siêu thị & Bán lẻ chung quy
    # =========================================================================
    elif category_id == 2:
        cat_note = assemble_notes(
            existing_notes=data.get("notes"),
            vat_alert=vat_alert if has_vat else ""
        )
        if line_items:
            for it in line_items:
                iname = str(it.get("item_name") or "").strip()
                iqty = it.get("item_quantity") or 1

                u_price, v_rate, v_amt, l_total = compute_item_vat_and_pricing(
                    it, invoice_tax_rate, invoice_tax_amount, is_price_inclusive, invoice_subtotal
                )

                row = [
                    dt_code,                            # Cột A: Mã đối tượng
                    full_datetime,                      # Cột B: Ngày, tháng, năm
                    merchant_name,                      # Cột C: Tên công ty / Siêu thị
                    merchant_addr,                      # Cột D: Địa chỉ bên bán
                    customer_addr,                      # Cột E: Địa chỉ bên nhận
                    doc_code,                           # Cột F: Mã hóa đơn / chứng từ
                    iname,                              # Cột G: Tên hàng hóa, dịch vụ
                    iqty,                               # Cột H: Số lượng
                    u_price,                            # Cột I: Đơn giá (chưa VAT)
                    v_rate,                             # Cột J: % VAT
                    v_amt,                              # Cột K: VAT
                    l_total,                            # Cột L: Thành tiền
                    customer_name,                      # Cột M: Người mua/nhận hàng
                    cat_note                            # Cột N: Ghi chú
                ]
                rows.append(row)
        else:
            row = [
                dt_code,
                full_datetime,
                merchant_name,
                merchant_addr,
                customer_addr,
                doc_code,
                "Hóa đơn mua hàng",
                1,
                invoice_subtotal if invoice_subtotal > 0 else (total_amount if total_amount is not None else 0),
                f"{int(invoice_tax_rate*100)}%" if invoice_tax_rate > 0 else "0%",
                invoice_tax_amount if invoice_tax_amount > 0 else 0,
                total_amount if total_amount is not None else 0,
                customer_name,
                cat_note
            ]
            rows.append(row)

    # =========================================================================
    # ĐỐI TƯỢNG 3: Doanh nghiệp cung ứng thực phẩm
    # =========================================================================
    elif category_id == 3:
        cat_note = assemble_notes(
            existing_notes=data.get("notes"),
            vat_alert=vat_alert if has_vat else ""
        )
        valid_items = []
        for it in line_items:
            iname = str(it.get("item_name") or "")
            if REMOVAL_TAG in iname.lower() or "(loai bo)" in iname.lower():
                continue
            valid_items.append(it)

        if valid_items:
            for it in valid_items:
                iname = str(it.get("item_name") or "").strip()
                iqty = it.get("item_quantity") or 1

                u_price, v_rate, v_amt, l_total = compute_item_vat_and_pricing(
                    it, invoice_tax_rate, invoice_tax_amount, is_price_inclusive, invoice_subtotal
                )

                row = [
                    dt_code,                            # Cột A: Mã đối tượng
                    full_datetime,                      # Cột B: Ngày, tháng, năm
                    merchant_name,                      # Cột C: Tên công ty cung ứng
                    merchant_addr,                      # Cột D: Địa chỉ bên bán
                    customer_addr,                      # Cột E: Địa chỉ bên nhận
                    doc_code,                           # Cột F: Mã hóa đơn / phiếu xuất kho
                    iname,                              # Cột G: Tên hàng hóa, nông sản
                    iqty,                               # Cột H: Số lượng
                    u_price,                            # Cột I: Đơn giá (chưa VAT)
                    v_rate,                             # Cột J: % VAT
                    v_amt,                              # Cột K: VAT
                    l_total,                            # Cột L: Thành tiền
                    customer_name,                      # Cột M: Người mua/nhận hàng
                    cat_note                            # Cột N: Ghi chú
                ]
                rows.append(row)
        else:
            row = [
                dt_code,
                full_datetime,
                merchant_name,
                merchant_addr,
                customer_addr,
                doc_code,
                "Cung ứng thực phẩm",
                1,
                invoice_subtotal if invoice_subtotal > 0 else (total_amount if total_amount is not None else 0),
                f"{int(invoice_tax_rate*100)}%" if invoice_tax_rate > 0 else "0%",
                invoice_tax_amount if invoice_tax_amount > 0 else 0,
                total_amount if total_amount is not None else 0,
                customer_name,
                cat_note
            ]
            rows.append(row)

    # =========================================================================
    # ĐỐI TƯỢNG 4: Hóa đơn viết tay & Không xác định danh tính
    # =========================================================================
    elif category_id == 4:
        cat_note = assemble_notes(
            existing_notes=data.get("notes"),
            fallback_default="Hóa đơn / Giấy viết tay"
        )
        if line_items:
            for it in line_items:
                iname = str(it.get("item_name") or "").strip()
                iqty = it.get("item_quantity") or 1
                iprice = it.get("item_price") or ""
                itotal = it.get("total_price") or ""

                row = [
                    dt_code,                            # Cột A: Mã đối tượng (DT4XXXX)
                    full_datetime,                      # Cột B: Ngày, tháng, năm (nếu có)
                    merchant_name,                      # Cột C: Tên người bán (nếu có)
                    merchant_addr,                      # Cột D: Địa chỉ bên bán (nếu có)
                    customer_addr,                      # Cột E: Địa chỉ bên nhận (nếu có)
                    doc_code,                           # Cột F: Mã chứng từ (nếu có)
                    iname,                              # Cột G: Tên hàng hóa
                    iqty,                               # Cột H: Số lượng
                    iprice if iprice else 0,            # Cột I: Đơn giá
                    "0%",                               # Cột J: % VAT
                    0,                                  # Cột K: VAT
                    itotal if itotal != "" else ((float(iprice)*float(iqty)) if (iprice and iqty) else 0), # Cột L: Thành tiền
                    customer_name,                      # Cột M: Người mua/nhận hàng (nếu có)
                    cat_note                            # Cột N: Ghi chú
                ]
                rows.append(row)
        else:
            row = [
                dt_code,
                full_datetime,
                merchant_name,
                merchant_addr,
                customer_addr,
                doc_code,
                "Hàng hóa viết tay",
                1,
                total_amount if total_amount is not None else 0,
                "0%",
                0,
                total_amount if total_amount is not None else 0,
                customer_name,
                "Hóa đơn / Giấy viết tay"
            ]
            rows.append(row)

    meta = {
        "category_id": category_id,
        "dt_code": dt_code,
        "row_count": len(rows),
        "has_vat": has_vat,
        "vat_amount": vat_amount,
        "vat_alert": vat_alert
    }
    return rows, meta


def format_receipt_to_relational_v2(
    data: Dict[str, Any],
    category_id: int,
    current_index: int = 1,
    drive_link: str = ""
) -> Tuple[List[Any], List[List[Any]], Dict[str, Any]]:
    """
    Format extracted receipt JSON into pure 2-sheet relational structure:
    - header_row: 14 columns for Data_Header_V2 (A→N)
    - line_rows: N rows of 14 columns for Data_Lines_V2 (A→N; M "Nhóm hàng", N "Nguồn phân loại")
    """
    dt_code = f"DT{category_id}{current_index:04d}"

    date_str = str(data.get("transaction_date") or data.get("date") or "").strip()
    time_str = str(data.get("transaction_time") or data.get("time") or "").strip()
    full_datetime = normalize_datetime_vn(date_str, time_str, prefix_quote=True)

    merchant_name = str(data.get("merchant_name") or data.get("seller_name") or data.get("company_name") or "").strip()
    merchant_addr = str(data.get("merchant_address") or data.get("seller_address") or "").strip()
    customer_name = str(data.get("customer_name") or data.get("buyer_name") or "").strip()
    customer_addr = str(data.get("customer_address") or "").strip()
    doc_code = str(data.get("invoice_number") or data.get("receipt_number") or data.get("tracking_number") or "")
    order_id = str(data.get("order_id") or "")
    primary_code = order_id if order_id else doc_code
    if primary_code and primary_code.startswith("0") and len(primary_code) > 1:
        primary_code = f"'{primary_code}"

    has_vat, vat_amount, vat_alert = check_vat_alert(data)
    invoice_tax_rate = float(data.get("tax_rate") or data.get("vat_rate") or 0)
    invoice_tax_amount = float(data.get("tax_amount") or data.get("vat_amount") or 0)
    is_price_inclusive = bool(data.get("is_price_inclusive_of_vat", False))
    invoice_subtotal = float(data.get("subtotal_amount") or data.get("subtotal") or 0)
    bill_discount = float(data.get("discount_amount") or data.get("discount") or 0)

    line_items = data.get("line_items") or []

    # Filter Category 3 removal tag
    if category_id == 3:
        filtered_items = []
        for it in line_items:
            iname = str(it.get("item_name") or "").lower()
            if REMOVAL_TAG in iname or "(loai bo)" in iname:
                continue
            filtered_items.append(it)
        line_items = filtered_items

    # Format Category 1 Merchant Name
    if category_id == 1:
        base_platform = ""
        m_lower = merchant_name.lower()
        if "shopee" in m_lower or "spx" in m_lower:
            base_platform = "Shopee"
        elif "lazada" in m_lower or "lex" in m_lower:
            base_platform = "Lazada"
        elif "tiktok" in m_lower:
            base_platform = "TikTok Shop"
        elif "tiki" in m_lower:
            base_platform = "Tiki"
        elif "ghtk" in m_lower or "tiết kiệm" in m_lower:
            base_platform = "Giao Hàng Tiết Kiệm"
        elif "ghn" in m_lower or "nhanh" in m_lower:
            base_platform = "Giao Hàng Nhanh"
        elif "viettel" in m_lower:
            base_platform = "Viettel Post"
        elif "j&t" in m_lower:
            base_platform = "J&T Express"

        shop_name = merchant_name
        remove_words = ["shopee", "spx", "express", "cb", "lazada", "lex", "tiktok shop", "tiktok", "tiki", "ghtk", "ghn", "viettel post", "viettel", "j&t", "giao hàng nhanh", "giao hàng tiết kiệm", "logistics", "delivery"]
        for w in remove_words:
            shop_name = re.sub(r'(?i)\b' + re.escape(w) + r'\b', '', shop_name)
        shop_name = re.sub(r'^[\s\-_,]+|[\s\-_,]+$', '', shop_name)
        shop_name = " ".join(shop_name.split()).strip()

        if base_platform:
            merchant_name = f"{base_platform} - {shop_name}" if shop_name else base_platform

    line_rows: List[List[Any]] = []
    computed_raw_total = 0.0
    computed_discount_total = bill_discount
    computed_vat_total = 0.0

    def _clean_num(val):
        if val is None or val == "":
            return 0
        try:
            vf = float(val)
            return int(vf) if vf.is_integer() else round(vf, 2)
        except (ValueError, TypeError):
            return 0

    if category_id == 1:
        # === ĐỐI TƯỢNG 1 (Sàn TMĐT & Vận chuyển): Luôn ghi nhận DUY NHẤT 1 DÒNG tổng hợp trên Data_Lines_V2 ===
        items_summary_list = []
        total_qty = 0
        if line_items:
            for it in line_items:
                iname = str(it.get("item_name") or "").strip()
                try:
                    iq = float(it.get("item_quantity") or 1)
                except Exception:
                    iq = 1.0
                total_qty += iq
                items_summary_list.append(f"- {iname} (SL: {int(iq) if isinstance(iq, float) and iq.is_integer() else iq})")
        
        item_names_str = "\n".join(items_summary_list) if items_summary_list else "Đơn hàng TMĐT"

        tot_val = float(data.get("total_amount") or 0)
        raw_val = invoice_subtotal if invoice_subtotal > 0 else tot_val
        vat_val = invoice_tax_amount if invoice_tax_amount > 0 else (vat_amount if has_vat else 0)
        final_val = tot_val if tot_val > 0 else (raw_val - bill_discount + vat_val)

        computed_raw_total = raw_val
        computed_discount_total = bill_discount
        computed_vat_total = vat_val

        dt1_line = [
            dt_code,                                        # Cột A: Mã đối tượng
            "",                                             # Cột B: Mã sản phẩm
            item_names_str,                                 # Cột C: Tên hàng hóa, dịch vụ
            _clean_num(total_qty if total_qty > 0 else 1),  # Cột D: Số lượng
            "Đơn",                                          # Cột E: Đơn vị tính
            _clean_num(raw_val),                            # Cột F: Đơn giá (chưa VAT) - Số thuần
            _clean_num(bill_discount),                      # Cột G: Chiết khấu mặt hàng - Số thuần
            0,                                              # Cột H: Tỷ lệ CK (%) - Số thuần
            0,                                              # Cột I: Thuế suất VAT (%) - Số thuần
            _clean_num(vat_val),                            # Cột J: Tiền thuế VAT - Số thuần
            _clean_num(final_val),                          # Cột K: Thành tiền - Số thuần
            ""                                              # Cột L: Ghi chú mặt hàng
        ]
        line_rows.append(dt1_line)

    elif line_items:
        for it in line_items:
            p_code = str(it.get("product_code") or it.get("sku") or it.get("barcode") or "").strip()
            iname = str(it.get("item_name") or "").strip()
            try:
                iqty = float(it.get("item_quantity") or 1)
            except (ValueError, TypeError):
                iqty = 1.0

            u_price, v_rate, v_amt, l_total = compute_item_vat_and_pricing(
                it, invoice_tax_rate, invoice_tax_amount, is_price_inclusive, invoice_subtotal
            )

            # Item discount & Discount rate normalization
            it_disc = float(it.get("discount_amount") or it.get("discount") or 0)
            it_disc_rate = float(it.get("discount_rate") or 0)
            
            if it_disc_rate > 100.0:
                it_disc_rate = round(it_disc_rate / 100.0, 2)
            elif 0 < it_disc_rate <= 1.0:
                it_disc_rate = round(it_disc_rate * 100.0, 2)

            # Smart logic to distinguish if it_disc is a unit discount or line discount:
            if it_disc > 0 and u_price > 0 and iqty > 1:
                actual_line_total = it.get("total_price") or it.get("total")
                try:
                    t_val = float(actual_line_total) if actual_line_total is not None else 0.0
                except (ValueError, TypeError):
                    t_val = 0.0

                if t_val > 0:
                    # Case A: If D is unit discount, total = (U - D) * Q
                    # Case B: If D is line discount, total = U * Q - D
                    tot_a = (u_price - it_disc) * iqty
                    tot_b = (u_price * iqty) - it_disc
                    if abs(t_val - tot_a) < abs(t_val - tot_b):
                        # D is unit discount, convert it_disc to total line discount
                        it_disc = round(it_disc * iqty, 2)
                else:
                    if it_disc_rate > 0:
                        # Rate based on unit discount: (D / U) * 100
                        rate_unit = (it_disc / u_price) * 100.0
                        # Rate based on line discount: (D / (U * Q)) * 100
                        rate_line = (it_disc / (u_price * iqty)) * 100.0
                        if abs(it_disc_rate - rate_unit) < abs(it_disc_rate - rate_line):
                            # D is unit discount, convert it_disc to total line discount
                            it_disc = round(it_disc * iqty, 2)

            # Recalculate normalized values
            if it_disc == 0 and it_disc_rate > 0 and u_price > 0:
                it_disc = round(iqty * u_price * (it_disc_rate / 100.0), 2)
            elif it_disc > 0 and (iqty * u_price) > 0:
                it_disc_rate = round((it_disc / (iqty * u_price)) * 100.0, 2)

            raw_line_val = round(iqty * u_price, 2)
            net_before_vat = max(0.0, raw_line_val - it_disc)

            # Re-verify VAT amount if discount applied and vat_amount was not hardcoded in item
            if it.get("vat_amount") is None and it.get("tax_amount") is None:
                # Parse numeric rate
                try:
                    num_r = float(str(v_rate).replace("%", "").strip()) / 100.0 if v_rate and v_rate != "0%" else 0.0
                except (ValueError, TypeError):
                    num_r = 0.0
                if num_r > 0:
                    v_amt = round(net_before_vat * num_r, 2)

            final_line_val = round(net_before_vat + float(v_amt or 0), 2)

            computed_raw_total += raw_line_val
            computed_discount_total += it_disc
            computed_vat_total += float(v_amt or 0)

            # Clean numeric values
            vat_num = int(round(float(str(v_rate).replace("%", "").strip()))) if v_rate and v_rate != "0%" else 0
            disc_rate_num = int(it_disc_rate) if it_disc_rate.is_integer() else round(it_disc_rate, 2)

            def _clean_num(val):
                if val is None or val == "":
                    return 0
                try:
                    vf = float(val)
                    return int(vf) if vf.is_integer() else round(vf, 2)
                except (ValueError, TypeError):
                    return 0

            m_unit = str(it.get("measurement_unit") or "").strip()

            line_row = [
                dt_code,                                    # Cột A: Mã đối tượng
                p_code,                                     # Cột B: Mã sản phẩm
                iname,                                      # Cột C: Tên hàng hóa, dịch vụ
                _clean_num(iqty),                           # Cột D: Số lượng
                m_unit,                                     # Cột E: Đơn vị tính
                _clean_num(u_price),                        # Cột F: Đơn giá (chưa VAT) - Số thuần
                _clean_num(it_disc),                        # Cột G: Chiết khấu mặt hàng - Số thuần
                disc_rate_num,                              # Cột H: Tỷ lệ CK (%) - Số thuần
                vat_num,                                    # Cột I: Thuế suất VAT (%) - Số thuần
                _clean_num(v_amt),                          # Cột J: Tiền thuế VAT - Số thuần
                _clean_num(final_line_val),                 # Cột K: Thành tiền - Số thuần
                str(it.get("notes") or "")                  # Cột L: Ghi chú mặt hàng
            ]
            line_rows.append(line_row)

    # === Tự động trích xuất chi phí vận chuyển từ notes nếu chưa có trong line_items (áp dụng cho DT2, DT3, DT4) ===
    notes_raw = str(data.get("notes") or "").strip()
    if notes_raw and category_id != 1:
        shipping_keywords = ["phí vận chuyển", "phí ship", "cước vận chuyển", "shipping fee", "freight", "delivery fee", "phí giao hàng", "chi phí vận chuyển"]
        already_has_shipping = any(
            any(kw in str(it.get("item_name") or "").lower() for kw in shipping_keywords)
            for it in (data.get("line_items") or [])
        )
        if not already_has_shipping:
            ship_match = re.search(
                r"(?:ph[ií]\s*(?:v[aậ]n\s*chuy[eể]n|ship|giao\s*h[àa]ng)|c[uướ][oở]c\s*v[aậ]n\s*chuy[eể]n|chi\s*ph[ií]\s*v[aậ]n\s*chuy[eể]n|shipping\s*fee|freight|delivery\s*fee)[^\d]*(\d[\d\.,]*)(?:\s*(?:vnd|vn[đd]|đ|d))?",
                notes_raw,
                re.IGNORECASE
            )
            if ship_match:
                raw_num_str = ship_match.group(1).replace(".", "").replace(",", ".").strip()
                try:
                    ship_fee = float(raw_num_str)
                    if ship_fee > 0:
                        def _clean_ship(val):
                            try:
                                vf = float(val)
                                return int(vf) if vf.is_integer() else round(vf, 2)
                            except Exception:
                                return 0

                        shipping_line = [
                            dt_code,                    # Cột A: Mã đối tượng
                            "",                         # Cột B: Mã sản phẩm
                            "Phí vận chuyển",           # Cột C: Tên hàng hóa, dịch vụ
                            1,                          # Cột D: Số lượng
                            "Lần",                      # Cột E: Đơn vị tính
                            _clean_ship(ship_fee),      # Cột F: Đơn giá
                            0,                          # Cột G: Chiết khấu
                            0,                          # Cột H: Tỷ lệ CK (%)
                            0,                          # Cột I: Thuế suất VAT (%)
                            0,                          # Cột J: Tiền thuế VAT
                            _clean_ship(ship_fee),      # Cột K: Thành tiền
                            ""                          # Cột L: Ghi chú mặt hàng
                        ]
                        line_rows.append(shipping_line)
                        computed_raw_total += ship_fee
                except (ValueError, TypeError):
                    pass

    if not line_rows:
        # Fallback if no items extracted
        tot_val = float(data.get("total_amount") or 0)
        raw_val = invoice_subtotal if invoice_subtotal > 0 else tot_val
        vat_val = invoice_tax_amount if invoice_tax_amount > 0 else (vat_amount if has_vat else 0)
        final_val = tot_val if tot_val > 0 else (raw_val - bill_discount + vat_val)

        computed_raw_total = raw_val
        computed_vat_total = vat_val

        default_iname = "Đơn hàng TMĐT" if category_id == 1 else ("Cung ứng thực phẩm" if category_id == 3 else "Hóa đơn mua hàng")
        vat_fallback_rate = int(invoice_tax_rate * 100) if invoice_tax_rate > 0 else 0
        def _clean_num(val):
            if val is None or val == "":
                return 0
            try:
                vf = float(val)
                return int(vf) if vf.is_integer() else round(vf, 2)
            except (ValueError, TypeError):
                return 0

        line_row = [
            dt_code,
            "",
            default_iname,
            1,
            "",                                         # Cột E: Đơn vị tính
            _clean_num(raw_val),                        # Cột F: Đơn giá
            _clean_num(bill_discount),                  # Cột G: Chiết khấu mặt hàng
            0,                                          # Cột H: Tỷ lệ CK (%)
            vat_fallback_rate,                          # Cột I: Thuế suất VAT (%)
            _clean_num(vat_val),                        # Cột J: Tiền thuế VAT
            _clean_num(final_val),                      # Cột K: Thành tiền
            ""                                          # Cột L: Ghi chú mặt hàng
        ]
        line_rows.append(line_row)

    # === Phân loại loại-hàng ở cấp DÒNG: append Cột M "Nhóm hàng" + Cột N "Nguồn phân loại" ===
    # Áp cho MỌI nhánh (DT1 dòng gộp, DT2/3/4 chi tiết, dòng phí ship auto, fallback).
    # Chỉ THÊM 2 phần tử cuối mỗi row — không đụng index A→L.
    _is_dt1 = (category_id == 1)
    for lr in line_rows:
        iname = lr[2] if len(lr) > 2 else ""
        iunit = lr[4] if len(lr) > 4 else ""
        g_code, g_src = classify_line_item(iname, iunit, is_dt1=_is_dt1)
        lr.append(g_code)   # Cột M: Nhóm hàng
        lr.append(g_src)    # Cột N: Nguồn phân loại

    # Header calculations
    final_header_vat = invoice_tax_amount if invoice_tax_amount > 0 else (computed_vat_total if computed_vat_total > 0 else (vat_amount if has_vat else 0))
    final_header_raw = invoice_subtotal if invoice_subtotal > 0 else (computed_raw_total if computed_raw_total > 0 else float(data.get("total_amount") or 0))
    final_header_discount = computed_discount_total
    
    declared_total = data.get("total_amount")
    if declared_total is not None and float(declared_total) > 0:
        final_header_payment = float(declared_total)
    else:
        final_header_payment = round(final_header_raw - final_header_discount + final_header_vat, 2)

    def _clean_num(val):
        if val is None or val == "":
            return 0
        try:
            vf = float(val)
            return int(vf) if vf.is_integer() else round(vf, 2)
        except (ValueError, TypeError):
            return 0

    tracking_no = str(data.get("tracking_number") or "").strip()
    header_notes = assemble_notes(
        existing_notes=data.get("notes"),
        tracking_no=tracking_no if category_id == 1 else "",
        vat_alert=vat_alert if has_vat else "",
        fallback_default="Hóa đơn viết tay" if category_id == 4 else "",
        merchant_phone=data.get("merchant_phone", ""),
        merchant_email=data.get("merchant_email", ""),
        seller_tax_id=data.get("seller_tax_id", ""),
        customer_name=data.get("customer_name", ""),
        customer_phone=data.get("customer_phone", ""),
        customer_email=data.get("customer_email", "")
    )

    header_row = [
        dt_code,                                                # Cột A: Mã đối tượng
        full_datetime,                                          # Cột B: Ngày, tháng, năm
        merchant_name,                                          # Cột C: Tên công ty
        merchant_addr,                                          # Cột D: Địa chỉ bên bán
        customer_addr,                                          # Cột E: Địa chỉ bên nhận
        primary_code,                                           # Cột F: Mã hóa đơn, chứng từ
        _clean_num(final_header_raw),                           # Cột G: Tổng tiền hàng (gốc) - Số thuần
        _clean_num(final_header_discount),                      # Cột H: Chiết khấu thương mại - Số thuần
        _clean_num(final_header_vat),                           # Cột I: Thuế VAT - Số thuần
        _clean_num(final_header_payment),                       # Cột J: Tổng Thanh Toán - Số thuần
        customer_name,                                          # Cột K: Người mua/nhận hàng
        drive_link,                                             # Cột L: Link ảnh đối soát
        header_notes,                                           # Cột M: Ghi chú chung
        ""                                                      # Cột N: Xác nhận (chờ duyệt)
    ]

    meta = {
        "category_id": category_id,
        "dt_code": dt_code,
        "line_count": len(line_rows),
        "has_vat": has_vat,
        "vat_amount": final_header_vat,
        "vat_alert": vat_alert,
        "total_discount": final_header_discount,
        "total_payment": final_header_payment
    }

    return header_row, line_rows, meta

