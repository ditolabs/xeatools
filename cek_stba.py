import requests, re, base64

BASE_URL    = "https://api.galva.co.id"
KEY_USER_ID = 372
TOKEN       = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJjMjMxYjBjNi1lZDcxLTQ0MzQtYjFjZS1iM2E5NDFmZjE5M2EiLCJuYW1lIjoiRGVwbyBTdXJhYmF5YSBJSUkiLCJrZXl1c2VySWQiOiIzNzIiLCJpYXQiOiIzLzMwLzIwMjYgMTE6MDU6NDkgUE0iLCJleHAiOjE3Nzc0Nzg3NDksImlzcyI6Imh0dHBzOi8vYXBpLmdhbHZhLmNvLmlkIiwiYXVkIjoiaHR0cHM6Ly94c3lzdC5nYWx2YS5jby5pZCJ9.1G8uA5vnIMAPXwwQgnYQNLd2lsgBI9g9DZ3aBl7vMLU"
HEADERS = {"user-agent": "Dart/3.4 (dart:io)", "accept": "application/json",
           "authorization": f"Bearer {TOKEN}", "content-type": "application/json"}

ORDER_ID = input("Order ID: ").strip()
resp = requests.get(f"{BASE_URL}/xsyst/api/engineer-service-order",
                    params={"keyUserId": KEY_USER_ID, "serviceOrderId": ORDER_ID}, headers=HEADERS)
data = resp.json().get("data") or {}

for doc in data.get("service_documents", []):
    code = doc.get("document_type_code")
    raw  = doc.get("document", "")
    if not raw:
        print(f"[{code}] null"); continue

    print(f"\n[{code}] panjang={len(raw)} mod4={len(raw)%4}")

    # Coba 3 metode decode
    for label, s in [
        ("original",   raw),
        ("cleaned",    re.sub(r'[^A-Za-z0-9+/=]', '', raw)),
        ("url_safe",   raw.replace('-','+').replace('_','/')),
    ]:
        s2 = s + "=" * (-len(s) % 4)
        try:
            result = base64.b64decode(s2, validate=True)
            print(f"  {label}: OK ({len(result)} bytes, magic={result[:4].hex()})")
        except Exception as e:
            print(f"  {label}: GAGAL - {e}")
