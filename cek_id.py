import requests

BASE_URL    = "https://api.galva.co.id"
KEY_USER_ID = 372
TOKEN       = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJjMjMxYjBjNi1lZDcxLTQ0MzQtYjFjZS1iM2E5NDFmZjE5M2EiLCJuYW1lIjoiRGVwbyBTdXJhYmF5YSBJSUkiLCJrZXl1c2VySWQiOiIzNzIiLCJpYXQiOiIzLzMwLzIwMjYgMTE6MDU6NDkgUE0iLCJleHAiOjE3Nzc0Nzg3NDksImlzcyI6Imh0dHBzOi8vYXBpLmdhbHZhLmNvLmlkIiwiYXVkIjoiaHR0cHM6Ly94c3lzdC5nYWx2YS5jby5pZCJ9.1G8uA5vnIMAPXwwQgnYQNLd2lsgBI9g9DZ3aBl7vMLU"

HEADERS = {
    "user-agent": "Dart/3.4 (dart:io)",
    "accept": "application/json",
    "authorization": f"Bearer {TOKEN}",
    "content-type": "application/json"
}

for is_finish in [False, True]:
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-orders",
        params={
            "keyUserId": KEY_USER_ID,
            "isFinish": "true" if is_finish else "false",
            "onlyMyTask": "true",
            "serviceOrderNumber": "", "userTicketInboxNumber": "",
            "supportTypeCode": "", "serialNumber": "",
            "customerDetailName": "", "engineerKeyuserId": "",
            "ticketStatusCode": "", "startDate": "", "endDate": ""
        },
        headers=HEADERS
    )
    orders = resp.json().get("data", [])
    label = "FINISHED" if is_finish else "ACTIVE"
    print(f"\n=== {label} ({len(orders)} order) ===")
    for o in orders:
        print(f"  ID: {o['service_order_id']} | {o['support_number']} | {o['support_type_code']} | {o['current_status_code']} | {o.get('latest_processed_date','')[:10]}")
