"""
Script kiểm tra nhanh Receipt OCR Service qua API
Cách dùng:
    python test_client.py --image samples/receipt.jpg --mode llm
    python test_client.py --image samples/receipt.jpg --mode tesseract
"""
import argparse
import json
import requests
import sys

def main():
    parser = argparse.ArgumentParser(description="Test Receipt OCR API")
    parser.add_argument("--image", required=True, help="Đường dẫn tới file ảnh hóa đơn")
    parser.add_argument("--url", default="http://localhost:8000", help="URL của API (mặc định: http://localhost:8000)")
    parser.add_argument("--mode", choices=["llm", "tesseract"], default="llm", help="Chế độ phân tích (llm hoặc tesseract)")
    args = parser.parse_args()

    endpoint = f"{args.url}/ocr/" if args.mode == "llm" else f"{args.url}/tesseract/ocr"

    print(f"[*] Đang gửi file: {args.image} tới {endpoint}...")
    try:
        with open(args.image, "rb") as f:
            files = {"file": f}
            res = requests.post(endpoint, files=files)

        if res.status_code == 200:
            print("\n[+] Kết quả thành công:")
            print(json.dumps(res.json(), indent=2, ensure_ascii=False))
        else:
            print(f"\n[-] Lỗi {res.status_code}: {res.text}")
    except Exception as e:
        print(f"\n[-] Không thể kết nối tới server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
