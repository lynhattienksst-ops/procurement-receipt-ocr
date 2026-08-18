import urllib.request
import json
import time

BASE_URL = 'http://localhost:8000'

def test_pipeline():
    print('[*] Bắt đầu Quét Drive & Phân tích OCR...')
    req = urllib.request.Request(f'{BASE_URL}/api/v1/drive/process-batch', method='POST')
    try:
        with urllib.request.urlopen(req) as response:
            res_data = response.read().decode('utf-8')
            data = json.loads(res_data)
    except Exception as e:
        print(f'[-] Lỗi khi quét batch: {e}')
        return
        
    staged = data.get('staged', [])
    print(f'[*] Đã quét và nhận diện được {len(staged)} ảnh hóa đơn.')
    
    approved_rows = []
    for item in staged:
        if item.get('approved') and item.get('formatted_rows'):
            approved_rows.extend(item['formatted_rows'])
            
    if not approved_rows:
        print('[-] Không có hóa đơn hợp lệ để đưa lên Google Sheet (thư mục rỗng hoặc toàn trùng lặp).')
        return
        
    print(f'[*] Chuẩn bị xuất {len(approved_rows)} dòng dữ liệu lên Google Sheet và di chuyển file...')
    # Pass an empty payload so it uses the staged_receipts internally (just like the frontend might do if not editing rows)
    payload = json.dumps({'rows': approved_rows}).encode('utf-8')
    req2 = urllib.request.Request(f'{BASE_URL}/api/v1/sheets/export', data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    
    try:
        with urllib.request.urlopen(req2) as response:
            res_data2 = response.read().decode('utf-8')
            print('[+] THÀNH CÔNG! Kết quả trả về:')
            print(json.dumps(json.loads(res_data2), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f'[-] Lỗi khi xuất lên Sheet: {e}')

if __name__ == '__main__':
    test_pipeline()
