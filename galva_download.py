import requests
import base64
import json
import os
from datetime import datetime

# =============================================
# KONFIGURASI
# =============================================
BASE_URL = "https://api.galva.co.id"
SAVE_DIR = "/storage/emulated/0/Download/galva_docs"

# Kredensial login akun XEA — diisi otomatis oleh setup.sh / tab Konfigurasi
USERNAME = ""
PASSWORD = ""

TRIGGER_MAP = {
    "INST": ["CL"],
    "MAIN": ["CL"],
    "TKRP": ["CL"],
    "SERV": ["FN", "CL"],
    "PLOT": ["CL"],
}

TARGET_DOCS = ["STAT", "STBA"]

# Basic Auth tetap untuk endpoint login (credential API, bukan user)
LOGIN_HEADERS = {
    "user-agent"   : "Dart/3.4 (dart:io)",
    "accept"       : "application/json",
    "accept-encoding": "gzip",
    "authorization": "Basic Z2FsdmFfYmU6YXBpQGJlMjAyMTAxMTQ=",
    "content-type" : "application/json; charset=utf-8",
}


def get_token():
    """Login ke API dan kembalikan Bearer token."""
    resp = requests.post(
        f"{BASE_URL}/xsyst/api/ldap/xea",
        headers=LOGIN_HEADERS,
        json={"user_name": USERNAME, "user_password": PASSWORD}
    )
    resp.raise_for_status()
    data = resp.json()

    # Cari token di response — sesuaikan key jika berbeda
    token = (data.get("data", {}) or {}).get("jwt_token")
    if not token:
        raise Exception(f"Token tidak ditemukan di response: {list(data.keys())}")
    return token


def decode_key_user_id(token: str) -> int:
    """Ekstrak keyuserId dari JWT payload tanpa library tambahan."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        return int(claims["keyuserId"])
    except Exception as e:
        raise Exception(f"Gagal baca keyuserId dari token: {e}")


def make_headers(token):
    return {
        "user-agent"   : "Dart/3.4 (dart:io)",
        "accept"       : "application/json",
        "authorization": f"Bearer {token}",
        "content-type" : "application/json",
    }


def input_tanggal(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%d-%m-%Y").date()
        except ValueError:
            print("  Format salah. Gunakan DD-MM-YYYY (contoh: 01-03-2026)")


def fetch_orders(headers, key_user_id, is_finish):
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-orders",
        params={
            "keyUserId":             key_user_id,
            "isFinish":              "true" if is_finish else "false",
            "onlyMyTask":            "true",
            "serviceOrderNumber":    "",
            "userTicketInboxNumber": "",
            "supportTypeCode":       "",
            "serialNumber":          "",
            "customerDetailName":    "",
            "engineerKeyuserId":     "",
            "ticketStatusCode":      "",
            "startDate":             "",
            "endDate":               ""
        },
        headers=headers
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_order_detail(headers, key_user_id, order_id):
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-order",
        params={"keyUserId": key_user_id, "serviceOrderId": order_id},
        headers=headers
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None


def should_download(type_code, status_code):
    triggers = TRIGGER_MAP.get(type_code)
    if not triggers:
        return False
    return status_code in triggers


def decode_base64(raw):
    # Coba standard dulu, fallback ke url_safe
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        url_safe = raw.replace('-', '+').replace('_', '/')
        padded2  = url_safe + "=" * (-len(url_safe) % 4)
        return base64.b64decode(padded2)


def save_document(support_number, doc):
    ext      = doc.get("document_extension") or "pdf"
    doc_code = doc.get("document_type_code", "DOC")
    raw      = doc.get("document")

    if not raw:
        return False

    filename = f"{support_number}_{doc_code}.{ext}".replace("/", "-")
    filepath = os.path.join(SAVE_DIR, filename)

    if os.path.exists(filepath):
        print(f"    [SKIP] Sudah ada: {filename}")
        return False

    try:
        pdf_bytes = decode_base64(raw)
    except Exception as e:
        print(f"    [ERR]  Gagal decode {doc_code}: {e}")
        return False

    with open(filepath, "wb") as f:
        f.write(pdf_bytes)
    print(f"    [OK]   {filename}")
    return True


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    print("=" * 50)
    print("  Galva Auto-Download")
    print("=" * 50)
    print("Format tanggal: DD-MM-YYYY\n")

    date_from = input_tanggal("Dari tanggal  : ")
    date_to   = input_tanggal("Sampai tanggal: ")

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    # Auto-login
    print(f"\nLogin sebagai {USERNAME}...")
    try:
        token       = get_token()
        key_user_id = decode_key_user_id(token)
        print(f"Login berhasil! (keyUserId: {key_user_id})")
    except Exception as e:
        print(f"Login gagal: {e}")
        return

    headers = make_headers(token)

    print(f"Mengambil data order...")

    orders_active   = fetch_orders(headers, key_user_id, is_finish=False)
    orders_finished = fetch_orders(headers, key_user_id, is_finish=True)

    seen, all_orders = set(), []
    for o in orders_active + orders_finished:
        oid = o.get("service_order_id")
        if oid not in seen:
            seen.add(oid)
            all_orders.append(o)

    qualified      = []
    skipped_status = 0
    skipped_date   = 0

    for order in all_orders:
        type_code = order.get("support_type_code", "")
        status    = order.get("current_status_code", "")
        processed = parse_date(order.get("latest_processed_date"))

        if not should_download(type_code, status):
            skipped_status += 1
            continue

        if not processed or not (date_from <= processed <= date_to):
            skipped_date += 1
            continue

        qualified.append(order)

    print(f"Total order  : {len(all_orders)}")
    print(f"Status skip  : {skipped_status}")
    print(f"Tanggal skip : {skipped_date}")
    print(f"Diproses     : {len(qualified)}")
    print(f"\nRentang: {date_from.strftime('%d %b %Y')} → {date_to.strftime('%d %b %Y')}")
    print("=" * 50)

    total_saved = 0

    for order in qualified:
        order_id    = order.get("service_order_id")
        number      = order.get("support_number", str(order_id))
        type_name   = order.get("support_type", "")
        status_name = order.get("current_status_name", "")
        customer    = order.get("customer_detail_name", "")
        processed   = parse_date(order.get("latest_processed_date"))

        print(f"\n[{number}]")
        print(f"  {type_name} | {status_name} | {processed.strftime('%d %b %Y')}")
        print(f"  {customer}")

        try:
            detail    = fetch_order_detail(headers, key_user_id, order_id)
            documents = detail.get("service_documents", [])
        except Exception as e:
            print(f"  → Gagal ambil detail: {e}")
            continue

        saved = 0
        for doc in documents:
            if doc.get("document_type_code") in TARGET_DOCS:
                if save_document(number, doc):
                    saved += 1

        if saved == 0:
            print(f"  → Tidak ada dokumen STAT/STBA tersedia")
        else:
            total_saved += saved

    print("\n" + "=" * 50)
    print(f"Selesai! Total file diunduh: {total_saved}")
    print(f"Lokasi: {SAVE_DIR}")
    print("=" * 50)


if __name__ == "__main__":
    main()
