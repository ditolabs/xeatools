#!/usr/bin/env python3
"""
galva_download.py — Download dokumen STAT & STBA dari API Galva XEA.
Bisa dijalankan CLI maupun dipanggil sebagai modul dari merge_web.py.
"""

import requests
import base64
import json
import os
from datetime import datetime

BASE_URL = "https://api.galva.co.id"

TRIGGER_MAP = {
    "INST": ["CL"],       # Install
    "MAIN": ["CL"],       # Maintenance
    "TKRP": ["CL"],       # Take Report
    "SERV": ["FN", "CL"], # Repair / Service
    # PLOT (Pull Out) tidak diunduh — tidak ada hitungan nilai
}

TARGET_DOCS = ["STAT", "STBA"]

LOGIN_HEADERS = {
    "user-agent"     : "Dart/3.4 (dart:io)",
    "accept"         : "application/json",
    "accept-encoding": "gzip",
    "authorization"  : "Basic Z2FsdmFfYmU6YXBpQGJlMjAyMTAxMTQ=",
    "content-type"   : "application/json; charset=utf-8",
}


def get_token(username: str, password: str) -> str:
    resp = requests.post(
        f"{BASE_URL}/xsyst/api/ldap/xea",
        headers=LOGIN_HEADERS,
        json={"user_name": username, "user_password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data  = resp.json()
    token = (data.get("data", {}) or {}).get("jwt_token")
    if not token:
        raise Exception(f"Token tidak ditemukan di response: {list(data.keys())}")
    return token


def decode_key_user_id(token: str) -> int:
    try:
        payload_b64  = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims       = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        return int(claims["keyuserId"])
    except Exception as e:
        raise Exception(f"Gagal baca keyuserId dari token: {e}")


def make_headers(token: str) -> dict:
    return {
        "user-agent"   : "Dart/3.4 (dart:io)",
        "accept"       : "application/json",
        "authorization": f"Bearer {token}",
        "content-type" : "application/json",
    }


def fetch_orders(headers: dict, key_user_id: int, is_finish: bool) -> list:
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{BASE_URL}/xsyst/api/engineer-service-orders",
                params={
                    "keyUserId"            : key_user_id,
                    "isFinish"             : "true" if is_finish else "false",
                    "onlyMyTask"           : "true",
                    "serviceOrderNumber"   : "",
                    "userTicketInboxNumber": "",
                    "supportTypeCode"      : "",
                    "serialNumber"         : "",
                    "customerDetailName"   : "",
                    "engineerKeyuserId"    : "",
                    "ticketStatusCode"     : "",
                    "startDate"            : "",
                    "endDate"              : "",
                },
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except requests.exceptions.Timeout:
            if attempt < 2:
                continue
            raise Exception("Koneksi timeout setelah 3 percobaan. Periksa jaringan.")
        except Exception as e:
            raise e


def fetch_order_detail(headers: dict, key_user_id: int, order_id) -> dict:
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-order",
        params={"keyUserId": key_user_id, "serviceOrderId": order_id},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def parse_date(date_str: str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None


def should_download(type_code: str, status_code: str) -> bool:
    triggers = TRIGGER_MAP.get(type_code)
    return bool(triggers) and status_code in triggers


def decode_base64(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        url_safe = raw.replace("-", "+").replace("_", "/")
        padded2  = url_safe + "=" * (-len(url_safe) % 4)
        return base64.b64decode(padded2)


def jpg_to_pdf_bytes(img_bytes: bytes) -> bytes:
    """Konversi bytes JPG/PNG ke bytes PDF single-page."""
    try:
        from pypdf import PdfWriter
        import struct, zlib

        # Deteksi ukuran gambar dari header JPEG
        # Pakai pendekatan sederhana: bungkus image sebagai PDF dengan image XObject
        # Library PIL tidak tersedia di Termux by default, pakai pypdf
        writer = PdfWriter()

        # Tulis JPG langsung sebagai PDF image menggunakan img2pdf pattern
        # Baca lebar/tinggi dari header JPEG (SOF marker)
        w, h = 595, 842  # default A4 jika gagal baca
        try:
            i = 0
            while i < len(img_bytes) - 1:
                if img_bytes[i] != 0xFF:
                    break
                marker = img_bytes[i+1]
                if marker in (0xC0, 0xC1, 0xC2):  # SOF marker
                    h = struct.unpack('>H', img_bytes[i+4:i+6])[0]
                    w = struct.unpack('>H', img_bytes[i+6:i+8])[0]
                    break
                elif marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                    i += 2
                else:
                    length = struct.unpack('>H', img_bytes[i+2:i+4])[0]
                    i += 2 + length
        except Exception:
            pass

        from pypdf.generic import (ArrayObject, DecodedStreamObject,
                                   NameObject, NumberObject, DictionaryObject)
        page = writer.add_blank_page(width=w, height=h)

        img_stream = DecodedStreamObject()
        img_stream._data = img_bytes
        img_stream.update({
            NameObject("/Type")            : NameObject("/XObject"),
            NameObject("/Subtype")         : NameObject("/Image"),
            NameObject("/Width")           : NumberObject(w),
            NameObject("/Height")          : NumberObject(h),
            NameObject("/ColorSpace")      : NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter")          : NameObject("/DCTDecode"),
        })

        page.merge_page(writer.pages[0])
        resources = DictionaryObject()
        xobject   = DictionaryObject()
        xobject[NameObject("/Img")] = writer._add_object(img_stream)
        resources[NameObject("/XObject")] = xobject
        page[NameObject("/Resources")] = resources

        content = b"q %d 0 0 %d 0 0 cm /Img Do Q" % (w, h)
        page[NameObject("/Contents")] = writer._add_object(
            DecodedStreamObject.initialize_from_dictionary(
                {NameObject("/Length"): NumberObject(len(content))}
            )
        )
        page["/Contents"].get_object()._data = content

        import io
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    except Exception:
        # Fallback: minimal valid PDF yang embed JPG langsung
        return _minimal_jpg_pdf(img_bytes)


def _minimal_jpg_pdf(jpg: bytes) -> bytes:
    """Buat PDF minimal yang embed JPG — tidak butuh library tambahan."""
    import struct, io

    # Baca dimensi dari JPEG header
    w, h = 595, 842
    try:
        i = 0
        while i < len(jpg) - 1:
            if jpg[i] != 0xFF: break
            marker = jpg[i+1]
            if marker in (0xC0, 0xC1, 0xC2):
                h = struct.unpack('>H', jpg[i+4:i+6])[0]
                w = struct.unpack('>H', jpg[i+6:i+8])[0]
                break
            elif marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                i += 2
            else:
                length = struct.unpack('>H', jpg[i+2:i+4])[0]
                i += 2 + length
    except Exception:
        pass

    img_len = len(jpg)
    # Minimal PDF structure
    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")

    offsets = []

    # Object 1: catalog
    offsets.append(pdf.tell())
    pdf.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2: pages
    offsets.append(pdf.tell())
    pdf.write(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    # Object 3: page
    offsets.append(pdf.tell())
    pdf.write(
        f"3 0 obj\n<< /Type /Page /Parent 2 0 R "
        f"/MediaBox [0 0 {w} {h}] "
        f"/Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>\nendobj\n"
        .encode()
    )

    # Object 4: content stream
    stream = f"q {w} 0 0 {h} 0 0 cm /Im1 Do Q".encode()
    offsets.append(pdf.tell())
    pdf.write(
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
        + stream + b"\nendstream\nendobj\n"
    )

    # Object 5: image XObject
    offsets.append(pdf.tell())
    pdf.write(
        f"5 0 obj\n<< /Type /XObject /Subtype /Image "
        f"/Width {w} /Height {h} /ColorSpace /DeviceRGB "
        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {img_len} >>\nstream\n"
        .encode()
    )
    pdf.write(jpg)
    pdf.write(b"\nendstream\nendobj\n")

    # xref table
    xref_pos = pdf.tell()
    pdf.write(f"xref\n0 6\n0000000000 65535 f \n".encode())
    for off in offsets:
        pdf.write(f"{off:010d} 00000 n \n".encode())
    pdf.write(
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
        .encode()
    )
    return pdf.getvalue()


def is_jpg_bytes(data: bytes) -> bool:
    """Cek apakah bytes adalah JPEG."""
    return data[:2] == b'\xff\xd8'


def save_document(support_number: str, doc: dict, save_dir: str) -> str:
    """Simpan dokumen. JPG otomatis dikonversi ke PDF. Return 'ok'|'skip'|'fail'."""
    doc_code = doc.get("document_type_code", "DOC")
    raw      = doc.get("document")
    if not raw:
        return "fail"

    # Nama file selalu .pdf (meski sumber JPG)
    filename = f"{support_number}_{doc_code}.pdf".replace("/", "-")
    filepath = os.path.join(save_dir, filename)

    # Cek di folder sumber
    if os.path.exists(filepath):
        return "skip"
    # Cek di subfolder arsip bulan
    try:
        for sub in Path(save_dir).iterdir():
            if sub.is_dir() and (sub / filename).exists():
                return "skip"
    except Exception:
        pass

    try:
        raw_bytes = decode_base64(raw)
    except Exception:
        return "fail"

    try:
        # Konversi otomatis jika file adalah JPG
        if is_jpg_bytes(raw_bytes):
            raw_bytes = _minimal_jpg_pdf(raw_bytes)
        with open(filepath, "wb") as f:
            f.write(raw_bytes)
        return "ok"
    except Exception:
        return "fail"
    """Simpan dokumen. Return 'ok'|'skip'|'fail'."""
    ext      = doc.get("document_extension") or "pdf"
    doc_code = doc.get("document_type_code", "DOC")
    raw      = doc.get("document")
    if not raw:
        return "fail"
    filename = f"{support_number}_{doc_code}.{ext}".replace("/", "-")
    filepath = os.path.join(save_dir, filename)

    # Cek di folder sumber
    if os.path.exists(filepath):
        return "skip"

    # Cek di subfolder arsip bulan (misal: April 2026/, Maret 2026/)
    # File dipindah ke sana setelah merge — jangan unduh ulang
    try:
        for sub in Path(save_dir).iterdir():
            if sub.is_dir() and (sub / filename).exists():
                return "skip"
    except Exception:
        pass

    try:
        with open(filepath, "wb") as f:
            f.write(decode_base64(raw))
        return "ok"
    except Exception:
        return "fail"


# ─────────────────────────────────────────────────────────────
# FUNGSI UTAMA — dipanggil dari merge_web.py
# ─────────────────────────────────────────────────────────────

def run_download(username: str, password: str,
                 date_from, date_to,
                 save_dir: str, cb=None) -> dict:
    """
    Jalankan proses download dengan callback untuk streaming.
    Events: login, login_ok, login_fail, fetch, scan,
            download_ok, download_skip, download_fail, done, error
    """
    def emit(event, data):
        if cb: cb(event, data)

    os.makedirs(save_dir, exist_ok=True)

    # Login
    emit("login", {"username": username})
    try:
        token       = get_token(username, password)
        key_user_id = decode_key_user_id(token)
        emit("login_ok", {"key_user_id": key_user_id})
    except Exception as e:
        emit("login_fail", {"msg": str(e)})
        return {"success": False, "saved": 0, "skipped": 0, "failed": 0}

    headers = make_headers(token)

    # Ambil order
    emit("fetch", {"msg": "Mengambil daftar order..."})
    try:
        orders_active   = fetch_orders(headers, key_user_id, is_finish=False)
        orders_finished = fetch_orders(headers, key_user_id, is_finish=True)
    except Exception as e:
        emit("error", {"msg": f"Gagal ambil order: {e}"})
        return {"success": False, "saved": 0, "skipped": 0, "failed": 0}

    seen, all_orders = set(), []
    for o in orders_active + orders_finished:
        oid = o.get("service_order_id")
        if oid not in seen:
            seen.add(oid)
            all_orders.append(o)

    qualified = []
    skipped_status = skipped_date = 0
    for order in all_orders:
        if not should_download(order.get("support_type_code", ""),
                               order.get("current_status_code", "")):
            skipped_status += 1
            continue
        processed = parse_date(order.get("latest_processed_date"))
        if not processed or not (date_from <= processed <= date_to):
            skipped_date += 1
            continue
        qualified.append(order)

    emit("scan", {
        "total"         : len(all_orders),
        "qualified"     : len(qualified),
        "skipped_status": skipped_status,
        "skipped_date"  : skipped_date,
        "date_from"     : str(date_from),
        "date_to"       : str(date_to),
    })

    # Download
    total_saved = total_skip = total_fail = 0
    for order in qualified:
        order_id  = order.get("service_order_id")
        number    = order.get("support_number", str(order_id))
        customer  = order.get("customer_detail_name", "")
        processed = parse_date(order.get("latest_processed_date"))

        try:
            detail    = fetch_order_detail(headers, key_user_id, order_id)
            documents = detail.get("service_documents", [])
        except Exception as e:
            emit("download_fail", {"number": number, "doc_code": "-", "msg": str(e)})
            total_fail += 1
            continue

        for doc in documents:
            doc_code = doc.get("document_type_code", "")
            if doc_code not in TARGET_DOCS:
                continue
            status_file = save_document(number, doc, save_dir)
            filename    = f"{number}_{doc_code}.pdf"
            cur_status  = order.get("current_status_code", "")
            if status_file == "ok":
                total_saved += 1
                emit("download_ok", {
                    "number"  : number,
                    "doc_code": doc_code,
                    "filename": filename,
                    "customer": customer,
                    "date"    : str(processed) if processed else "",
                })
            elif status_file == "skip":
                total_skip += 1
                # Bedakan: FN→CL (sudah diproses sebelumnya) vs skip biasa
                reason = "sudah diproses saat FN" if cur_status == "CL" and \
                    order.get("support_type_code") == "SERV" else "sudah ada"
                emit("download_skip", {
                    "number"  : number,
                    "doc_code": doc_code,
                    "filename": filename,
                    "reason"  : reason,
                })
            else:
                total_fail += 1
                emit("download_fail", {
                    "number"  : number,
                    "doc_code": doc_code,
                    "msg"     : "Decode gagal / data kosong",
                })

    result = {
        "success": True,
        "saved"  : total_saved,
        "skipped": total_skip,
        "failed" : total_fail,
        "save_dir": save_dir,
    }
    emit("done", result)
    return result


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def _input_tanggal(prompt: str):
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%d-%m-%Y").date()
        except ValueError:
            print("  Format salah. Gunakan DD-MM-YYYY (contoh: 01-03-2026)")


def main():
    config_path = os.path.join(os.path.expanduser("~"), "merge_pdf_config.json")
    username = password = ""
    save_dir = "/storage/emulated/0/Download/galva_docs"

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            username = cfg.get("xea_username", "")
            password = cfg.get("xea_password", "")
            save_dir = cfg.get("source_dir", save_dir)
        except Exception:
            pass

    if not username:
        username = input("Username XEA: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Password XEA: ")

    print("=" * 50)
    print("  Galva Auto-Download")
    print("=" * 50)

    date_from = _input_tanggal("Dari tanggal  : ")
    date_to   = _input_tanggal("Sampai tanggal: ")
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    def cli_cb(event, data):
        if event == "login":
            print(f"\nLogin sebagai {data['username']}...")
        elif event == "login_ok":
            print(f"Login berhasil! (keyUserId: {data['key_user_id']})")
        elif event == "login_fail":
            print(f"Login gagal: {data['msg']}")
        elif event == "scan":
            print(f"Total: {data['total']}  Diproses: {data['qualified']}  "
                  f"Skip status: {data['skipped_status']}  Skip tanggal: {data['skipped_date']}")
            print(f"Rentang: {data['date_from']} → {data['date_to']}")
            print("=" * 50)
        elif event == "download_ok":
            print(f"  [OK]   {data['filename']}  ({data['customer']})")
        elif event == "download_skip":
            print(f"  [SKIP] {data['filename']}")
        elif event == "download_fail":
            print(f"  [FAIL] {data['number']} — {data.get('msg','')}")
        elif event == "done":
            print(f"\n{'=' * 50}")
            print(f"Selesai! OK:{data['saved']}  Skip:{data['skipped']}  Gagal:{data['failed']}")
            print(f"Lokasi: {data['save_dir']}")

    run_download(username, password, date_from, date_to, save_dir, cli_cb)


if __name__ == "__main__":
    main()
