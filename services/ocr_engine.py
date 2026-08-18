"""
Unified Receipt OCR Engine supporting Native Google Gemini API (httpx client with retry), OpenAI, and Local Ollama.
"""
import os
import io
import json
import base64
import time
import logging
from typing import Dict, Any, Optional
import httpx
from PIL import Image

logger = logging.getLogger("ocr-engine")

try:
    from receipt_ocr.processors import ReceiptProcessor
    from receipt_ocr.providers import OpenAIProvider
except ImportError:
    ReceiptProcessor = None
    OpenAIProvider = None

PROCUREMENT_RECEIPT_SCHEMA = {
    "merchant_name": "string",
    "merchant_address": "string",
    "merchant_phone": "string",
    "customer_name": "string",
    "customer_address": "string",
    "tracking_number": "string",
    "order_id": "string",
    "invoice_number": "string",
    "transaction_date": "string",
    "transaction_time": "string",
    "subtotal_amount": "number",
    "tax_rate": "number",
    "tax_amount": "number",
    "total_amount": "number",
    "currency": "string",
    "payment_method": "string",
    "is_price_inclusive_of_vat": "boolean",
    "is_handwritten": "boolean",
    "line_items": [
        {
            "item_name": "string",
            "item_quantity": "number",
            "item_price": "number",
            "vat_rate": "number",
            "vat_amount": "number",
            "total_price": "number"
        }
    ],
    "notes": "string"
}

SYSTEM_INSTRUCTION = """
Bạn là một chuyên gia OCR và xử lý hóa đơn, phiếu gửi hàng (Procurement Receipt Extraction).
Nhiệm vụ: Đọc toàn bộ chữ trên hình ảnh và trích xuất thành đối tượng JSON chính xác theo đúng cấu trúc sau:
{
  "merchant_name": "Tên cửa hàng / Đơn vị bán / Sàn TMĐT / Dịch vụ vận chuyển",
  "merchant_address": "Địa chỉ cửa hàng / người gửi",
  "merchant_phone": "Số điện thoại",
  "customer_name": "Tên người mua / người nhận",
  "customer_address": "Địa chỉ người nhận",
  "tracking_number": "Mã vận đơn (nếu có)",
  "order_id": "Mã đơn hàng (nếu có)",
  "invoice_number": "Số hóa đơn / Mã chứng từ (nếu có)",
  "transaction_date": "YYYY-MM-DD",
  "transaction_time": "HH:MM:SS",
  "subtotal_amount": 0,
  "tax_rate": 0,
  "tax_amount": 0,
  "total_amount": 0,
  "currency": "VND",
  "payment_method": "Tiền thu Người nhận (COD) hoặc Chuyển khoản",
  "is_price_inclusive_of_vat": false,
  "is_handwritten": false,
  "line_items": [
    {
      "item_name": "Tên mặt hàng",
      "item_quantity": 1,
      "item_price": 0,
      "vat_rate": 0,
      "vat_amount": 0,
      "total_price": 0
    }
  ],
  "notes": "Ghi chú bổ sung"
}
Lưu ý về thuế VAT:
- Nếu hóa đơn có thuế GTGT/VAT, hãy xác định rõ tax_rate (ví dụ 0.08 hoặc 0.10) và tax_amount (tiền thuế).
- Nếu giá trên từng dòng đã bao gồm thuế thì đặt is_price_inclusive_of_vat: true.
- Chỉ trả về duy nhất chuỗi JSON hợp lệ.
"""

class UnifiedOCREngine:
    def __init__(self):
        self.cloud_provider = None
        self.ollama_provider = None
        self._init_providers()

    def _init_providers(self):
        from dotenv import load_dotenv
        load_dotenv(override=True)

        cloud_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if cloud_api_key and OpenAIProvider:
            try:
                self.cloud_provider = OpenAIProvider(
                    api_key=cloud_api_key,
                    base_url=os.getenv("OPENAI_BASE_URL")
                )
            except Exception as e:
                logger.warning(f"Could not init Cloud Provider: {e}")

        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
        if OpenAIProvider:
            try:
                self.ollama_provider = OpenAIProvider(
                    api_key="ollama",
                    base_url=ollama_base_url
                )
            except Exception as e:
                logger.warning(f"Could not init Ollama Provider: {e}")

    def process_image(
        self,
        image_input: Any,
        mode: str = "auto",
        custom_model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process receipt image using specified mode: 'auto', 'cloud', or 'ollama'.
        """
        from dotenv import load_dotenv
        if custom_model:
            if any(k in custom_model.lower() for k in ["qwen", "llava", "moondream", "minicpm", "local"]):
                engine_mode = "ollama"
            elif "gemini" in custom_model.lower() or "gpt" in custom_model.lower():
                engine_mode = "cloud"
            else:
                engine_mode = mode or os.getenv("AI_ENGINE_MODE", "auto")
        else:
            engine_mode = mode or os.getenv("AI_ENGINE_MODE", "auto")

        # Convert bytes to PIL Image if needed
        if isinstance(image_input, (bytes, bytearray)):
            image = Image.open(io.BytesIO(image_input))
            raw_bytes = bytes(image_input)
        elif isinstance(image_input, str) and os.path.exists(image_input):
            with open(image_input, "rb") as f:
                raw_bytes = f.read()
            image = Image.open(image_input)
        elif isinstance(image_input, Image.Image):
            image = image_input
            buf = io.BytesIO()
            image.save(buf, format="JPEG")
            raw_bytes = buf.getvalue()
        else:
            raise ValueError("Unsupported image input type.")

        if engine_mode == "ollama":
            return self._run_ollama(image, custom_model)

        if engine_mode == "cloud":
            return self._run_cloud(image, raw_bytes, custom_model)

        # Auto: Try Native Gemini first, fallback to Ollama
        try:
            return self._run_cloud(image, raw_bytes, custom_model)
        except Exception as e:
            logger.warning(f"Cloud OCR failed ({e}). Falling back to Ollama...")
            try:
                result = self._run_ollama(image, custom_model)
                result["_fallback_notice"] = f"Cloud failed, fallback to Ollama: {str(e)}"
                return result
            except Exception as e_local:
                raise RuntimeError(f"Cloud and Local OCR failed. Cloud: {e}, Local: {e_local}")

    def _run_cloud(self, image: Image.Image, raw_bytes: bytes, custom_model: Optional[str] = None) -> Dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("API Key not found in OPENAI_API_KEY / GEMINI_API_KEY.")

        base_url = os.getenv("OPENAI_BASE_URL", "")
        if "generativelanguage.googleapis.com" in base_url or api_key.startswith("AQ.") or api_key.startswith("AIza"):
            return self._run_native_gemini(raw_bytes, api_key, custom_model)

        if not self.cloud_provider:
            self._init_providers()
        if not self.cloud_provider:
            raise RuntimeError("Cloud Provider could not be initialized.")

        processor = ReceiptProcessor(provider=self.cloud_provider)
        target_model = custom_model or os.getenv("OPENAI_MODEL", "gpt-4o")
        result = processor.process_receipt(
            image_path=image,
            json_schema=PROCUREMENT_RECEIPT_SCHEMA,
            model=target_model
        )
        result["_engine_used"] = f"Cloud ({target_model})"
        return result

    def _run_native_gemini(self, raw_bytes: bytes, api_key: str, custom_model: Optional[str] = None) -> Dict[str, Any]:
        img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
        
        # Priority list of Flash-Lite models with automatic fallback
        default_chain = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash-lite"]
        candidate_models = list(default_chain)
        if custom_model:
            clean_model = str(custom_model).strip()
            if clean_model:
                if clean_model in candidate_models:
                    candidate_models.remove(clean_model)
                candidate_models.insert(0, clean_model)

        payload = {
            "systemInstruction": {
                "parts": [{"text": SYSTEM_INSTRUCTION}]
            },
            "contents": [{
                "parts": [
                    {"text": "Đọc và trích xuất thông tin hóa đơn này thành JSON."},
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }

        with httpx.Client(timeout=60.0) as client:
            last_err = None
            for model in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                for attempt in range(2):
                    try:
                        resp = client.post(url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            text_out = data["candidates"][0]["content"]["parts"][0]["text"]
                            parsed = json.loads(text_out)
                            parsed["_engine_used"] = f"Google Gemini ({model})"
                            return parsed
                        elif resp.status_code in [503, 429]:
                            time.sleep(2)
                        else:
                            last_err = f"Status {resp.status_code}: {resp.text[:200]}"
                            break
                    except Exception as req_err:
                        last_err = req_err
                        time.sleep(1)

        raise RuntimeError(f"Gemini API request failed: {last_err}")

    def _run_ollama(self, image: Image.Image, custom_model: Optional[str] = None) -> Dict[str, Any]:
        if not self.ollama_provider:
            self._init_providers()
        if not self.ollama_provider:
            raise RuntimeError("Ollama Provider is not initialized.")

        processor = ReceiptProcessor(provider=self.ollama_provider)
        target_model = custom_model or os.getenv("OLLAMA_MODEL", "qwen2.5-vl:7b")

        result = processor.process_receipt(
            image_path=image,
            json_schema=PROCUREMENT_RECEIPT_SCHEMA,
            model=target_model
        )
        result["_engine_used"] = f"Local Ollama ({target_model})"
        return result
