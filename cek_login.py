import requests
import json

BASE_URL = "https://api.galva.co.id"

resp = requests.post(
    f"{BASE_URL}/xsyst/api/ldap/xea",
    headers={
        "user-agent"   : "Dart/3.4 (dart:io)",
        "accept"       : "application/json",
        "authorization": "Basic Z2FsdmFfYmU6YXBpQGJlMjAyMTAxMTQ=",
        "content-type" : "application/json; charset=utf-8",
    },
    json={"user_name": "depo.surabaya.iii", "user_password": "e401614e"}
)

print("Status:", resp.status_code)
print(json.dumps(resp.json(), indent=2))
