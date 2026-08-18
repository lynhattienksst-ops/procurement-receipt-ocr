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

# Keywords for Category 2: Supermarkets, retail chains, convenience stores
CAT2_KEYWORDS = [
    "co.opmart", "coopmart", "co.op food", "winmart", "winmart+", "vinmart",
    "big c", "go!", "tops market", "lotte mart", "aeon", "aeon mall", "mega market",
    "emart", "circle k", "7-eleven", "7 eleven", "familymart", "gs25", "ministop",
    "bách hóa xanh", "bach hoa xanh", "guardian", "watsons", "con cưng", "concung",
    "thế giới di động", "dien may xanh", "fpt shop", "siêu thị", "mart", "store"
]

# Keywords for Category 3: Food suppliers, agricultural produce, fresh food wholesale
CAT3_KEYWORDS = [
    "thực phẩm", "nông sản", "rau củ", "thịt", "cá", "hải sản", "gia súc", "gia cầm",
    "chợ đầu mối", "công ty thực phẩm", "food", "food supply", "nguyên liệu thực phẩm",
    "cung ứng thực phẩm", "trứng", "gạo", "gia vị", "suất ăn", "bếp", "bánh kẹo"
]

# Tag to filter out in Category 3
REMOVAL_TAG = "(loại bỏ)"


def detect_business_category(data: Dict[str, Any]) -> Tuple[int, str]:
    """
    Detect the business category (1, 2, 3, 4) from parsed receipt JSON data.
    Returns: (category_id, category_name)
    """
    merchant_name = str(data.get("merchant_name") or "").lower()
    merchant_addr = str(data.get("merchant_address") or "").lower()
    tracking_no = str(data.get("tracking_number") or data.get("order_id") or "").lower()
    notes = str(data.get("notes") or "").lower()
    raw_text = f"{merchant_name} {merchant_addr} {tracking_no} {notes}"

    # Category 1: E-commerce platforms & Delivery
    if tracking_no or any(k in raw_text for k in CAT1_KEYWORDS):
        return 1, "Sàn thương mại điện tử & Dịch vụ vận chuyển"

    # Category 2: Supermarkets, retail chains, convenience stores
    if any(k in raw_text for k in CAT2_KEYWORDS):
        return 2, "Siêu thị, chuỗi bán lẻ & Cửa hàng tiện lợi"

    # Category 3: Food & Agricultural produce suppliers
    if any(k in raw_text for k in CAT3_KEYWORDS):
        return 3, "Doanh nghiệp cung ứng thực phẩm & Nông sản"

    # Check if handwriting / unidentified merchant (Category 4)
    is_handwritten = bool(data.get("is_handwritten", False))
    if is_handwritten or not merchant_name or merchant_name in ["n/a", "không xác định", "unknown", "none", ""]:
        return 4, "Hóa đơn viết tay & Không xác định danh tính"

    # Default fallback: If merchant exists but not in 1/3, categorize as Category 2 (Retail/Business)
    return 2, "Doanh nghiệp / Cửa hàng bán lẻ"


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
    fallback_default: str = ""
) -> str:
    """
    Combine all notes non-destructively:
    - Preserves existing custom notes, duplicate warnings, and manual edits
    - Appends tracking number if present and not yet in note
    - Appends VAT alerts if present and not yet in note
    """
    parts = []
    e_note = str(existing_notes or "").strip()
    if e_note:
        parts.append(e_note)

    tr = str(tracking_no or "").strip()
    if tr:
        tr_label = f"Mã vận đơn: {tr}"
        if tr not in e_note:
            parts.append(tr_label)

    va = str(vat_alert or "").strip()
    if va:
        va_label = f"[VAT] {va}" if not va.startswith("[") else va
        if va not in e_note:
            parts.append(va_label)

    if not parts and fallback_default:
        parts.append(fallback_default)

    return " | ".join(parts).strip() if parts else ""


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
    full_datetime = f"'{date_str} {time_str}".strip() if (date_str or time_str) else ""

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
    # ĐỐI TƯỢNG 2: Siêu thị, cửa hàng bán lẻ, cửa hàng tiện lợi
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
