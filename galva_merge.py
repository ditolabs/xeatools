#!/usr/bin/env python3
"""
galva_merge.py — Download STAT+STBA dari API Galva, lalu merge PDF.
Jalankan : python galva_merge.py
"""

import re
import ssl
import shutil
import smtplib
import json
import os
import requests
import base64
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    raise ImportError("Jalankan: pip install pypdf")

# ─────────────────────────────────────────────────────────────
# KONFIGURASI DOWNLOAD
# ─────────────────────────────────────────────────────────────
BASE_URL    = "https://api.galva.co.id"
KEY_USER_ID = 372
USERNAME    = "depo.surabaya.iii"
PASSWORD    = "e401614e"

TRIGGER_MAP = {
    "INST": ["CL"],
    "MAIN": ["CL"],
    "TKRP": ["CL"],
    "SERV": ["FN", "CL"],
    "PLOT": ["CL"],
}

TARGET_DOCS = ["STAT", "STBA"]

LOGIN_HEADERS = {
    "user-agent"    : "Dart/3.4 (dart:io)",
    "accept"        : "application/json",
    "accept-encoding": "gzip",
    "authorization" : "Basic Z2FsdmFfYmU6YXBpQGJlMjAyMTAxMTQ=",
    "content-type"  : "application/json; charset=utf-8",
}

# ─────────────────────────────────────────────────────────────
# KONFIGURASI MERGE (sama dengan merge_core.py)
# ─────────────────────────────────────────────────────────────
CONFIG_FILE = str(Path.home() / "merge_pdf_config.json")

DEFAULT_CONFIG = {
    "source_dir"      : "/sdcard/Documents",
    "output_dir"      : "/sdcard/Documents/Hasil",
    "digit_count"     : 6,
    "sender_email"    : "",
    "sender_password" : "",
    "to"              : [],
    "cc"              : [],
    "bcc"             : [],
    "subject_template": "Laporan PDF - {tipe_layanan}",
    "body_template"   : (
        "Halo,\n\nBerikut daftar pelanggan untuk Tipe Layanan [{tipe_layanan}]:\n\n"
        "{daftar_pelanggan}\n\nTerlampir {jumlah_file} file PDF.\n\n"
        "Email ini dikirim otomatis oleh script merge_pdf."
    ),
}

# Format filename baru: SVODR-2603-T07781_STBA.pdf / _STAT.pdf
TAG_FIRST  = "_STBA"   # halaman pertama
TAG_SECOND = "_STAT"   # halaman berikutnya

TIPE_LAYANAN_MAP = {
    "Install"          : "Install",
    "Maintenance"      : "Maintenance",
    "Repair / Service" : "Repair - Service",
    "Take Report"      : "Take Report",
}

HARGA_PER_TIPE = {
    "Take Report"      : 43_000,
    "Maintenance"      : 86_000,
    "Repair - Service" : 119_000,
    "Install"          : 199_000,
}

FILE_KOSONG_FOLDER = "File Kosong"
FALLBACK_FOLDER    = "Lainnya"
SMTP_HOST          = "smtp.gmail.com"
SMTP_PORT          = 465


# ─────────────────────────────────────────────────────────────
# UTILITAS KONFIGURASI
# ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(saved)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


# ─────────────────────────────────────────────────────────────
# UTILITAS DOWNLOAD
# ─────────────────────────────────────────────────────────────
def get_token() -> str:
    resp = requests.post(
        f"{BASE_URL}/xsyst/api/ldap/xea",
        headers=LOGIN_HEADERS,
        json={"user_name": USERNAME, "user_password": PASSWORD}
    )
    resp.raise_for_status()
    token = (resp.json().get("data", {}) or {}).get("jwt_token")
    if not token:
        raise Exception("Token tidak ditemukan di response login")
    return token


def api_headers(token: str) -> dict:
    return {
        "user-agent"   : "Dart/3.4 (dart:io)",
        "accept"       : "application/json",
        "authorization": f"Bearer {token}",
        "content-type" : "application/json",
    }


def fetch_orders(headers: dict, is_finish: bool) -> list:
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-orders",
        params={
            "keyUserId":             KEY_USER_ID,
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


def fetch_order_detail(headers: dict, order_id: int) -> dict:
    resp = requests.get(
        f"{BASE_URL}/xsyst/api/engineer-service-order",
        params={"keyUserId": KEY_USER_ID, "serviceOrderId": order_id},
        headers=headers
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
    if not triggers:
        return False
    return status_code in triggers


def decode_base64(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        url_safe = raw.replace('-', '+').replace('_', '/')
        padded2  = url_safe + "=" * (-len(url_safe) % 4)
        return base64.b64decode(padded2)


def save_document(support_number: str, doc: dict, save_dir: str) -> bool:
    ext      = doc.get("document_extension") or "pdf"
    doc_code = doc.get("document_type_code", "DOC")
    raw      = doc.get("document")

    if not raw:
        return False

    filename = f"{support_number}_{doc_code}.{ext}".replace("/", "-")
    filepath = os.path.join(save_dir, filename)

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


# ─────────────────────────────────────────────────────────────
# UTILITAS MERGE (format filename baru)
# ─────────────────────────────────────────────────────────────
def detect_tag(path: Path) -> str:
    """Deteksi tag dari suffix nama file: _STBA atau _STAT"""
    name = path.stem.upper()
    if name.endswith("_STBA"):  return "FIRST"
    if name.endswith("_STAT"):  return "SECOND"
    return None


def extract_key(filename: str, n: int) -> str:
    """
    Buang suffix _STBA/_STAT, lalu ambil n char alphanumeric terakhir.
    SVODR-2603-T07781_STBA → SVODR-2603-T07781 → T07781 (n=6)
    """
    stem = Path(filename).stem
    for suffix in ("_STBA", "_STAT", "_stba", "_stat"):
        if stem.upper().endswith(suffix.upper()):
            stem = stem[:-len(suffix)]
            break
    alnum = re.sub(r"[^A-Za-z0-9]", "", stem)
    return alnum[-n:].upper() if len(alnum) >= n else None


def find_pdfs(source_dir: str) -> list:
    source = Path(source_dir)
    if not source.exists():
        return []
    pdfs = list(source.glob("*.pdf")) + list(source.glob("*.PDF"))
    seen, unique = set(), []
    for p in pdfs:
        if p not in seen:
            seen.add(p); unique.append(p)
    return sorted(unique)


def extract_stba_info(stba_path: Path) -> tuple:
    nama = "-"; tipe = "-"
    try:
        reader = PdfReader(str(stba_path))
        for page in reader.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                if nama == "-":
                    m = re.search(r"Nama\s+Pelanggan\s*:\s*(.+)", line, re.IGNORECASE)
                    if m: nama = m.group(1).strip()
                if tipe == "-":
                    m = re.search(r"Tipe\s+Layanan\s*:\s*(.+)", line, re.IGNORECASE)
                    if m: tipe = m.group(1).strip()
                if nama != "-" and tipe != "-":
                    break
            if nama != "-" and tipe != "-":
                break
    except Exception:
        pass

    folder_name = None
    for pdf_label, folder in TIPE_LAYANAN_MAP.items():
        if tipe.upper() == pdf_label.upper():
            tipe        = pdf_label
            folder_name = folder
            break
    if folder_name is None:
        folder_name = FALLBACK_FOLDER
        if tipe == "-": tipe = FALLBACK_FOLDER

    return nama, tipe, folder_name


def merge_two(first: Path, second: Path, output_path: Path) -> bool:
    """Merge STBA (halaman pertama) + STAT (halaman berikutnya)"""
    writer = PdfWriter()
    try:
        for f in [first, second]:
            reader = PdfReader(str(f))
            for page in reader.pages:
                writer.add_page(page)
        with open(output_path, "wb") as out:
            writer.write(out)
        return True
    except Exception:
        return False


def save_note_txt(txt_path: Path, entries: list):
    with open(txt_path, "w", encoding="utf-8") as f:
        for key, nama in entries:
            f.write(f"{key} - {nama}\n")


def format_rupiah(angka: int) -> str:
    return "Rp {:>13,}".format(angka).replace(",", ".")


def nama_bulan_indonesia(dt: datetime) -> str:
    bulan = ["","Januari","Februari","Maret","April","Mei","Juni",
             "Juli","Agustus","September","Oktober","November","Desember"]
    return f"{bulan[dt.month]} {dt.year}"


def save_ringkasan_total(out_root: Path, summary: dict, file_kosong: list) -> Path:
    total = 0
    lines = ["RINGKASAN TOTAL PER TIPE LAYANAN", "=" * 54, ""]
    urutan = list(TIPE_LAYANAN_MAP.values()) + [FALLBACK_FOLDER]
    sorted_keys = sorted(summary.keys(),
                         key=lambda k: urutan.index(k) if k in urutan else 99)
    for folder_name in sorted_keys:
        entries = summary[folder_name]
        jumlah  = len(entries)
        harga   = HARGA_PER_TIPE.get(folder_name, 0)
        sub     = jumlah * harga
        total  += sub
        lines.append(
            f"  {folder_name:<22} : {jumlah:>3} file  x  "
            f"{format_rupiah(harga)}  =  {format_rupiah(sub)}"
        )
    lines += ["", "-" * 54,
              f"  {'TOTAL KESELURUHAN':<22}                     {format_rupiah(total)}",
              ""]
    if file_kosong:
        lines.append(f"  File Kosong (tidak dihitung) : {len(file_kosong)} file")
        for f in file_kosong:
            lines.append(f"    - {f.name}")
    lines += ["", f"Dibuat otomatis: {datetime.now().strftime('%d %B %Y %H:%M')}"]
    txt_path = out_root / "ringkasan_total.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return txt_path


def pindah_file_mentah(source_dir: str, moved_pairs: list) -> tuple:
    folder_bulan = nama_bulan_indonesia(datetime.now())
    target_dir   = Path(source_dir) / folder_bulan
    target_dir.mkdir(parents=True, exist_ok=True)
    ok = gagal = 0
    for stba_path, stats_path in moved_pairs:
        for src in [stba_path, stats_path]:
            dst = target_dir / src.name
            c = 1
            while dst.exists():
                dst = target_dir / f"{src.stem}_{c}{src.suffix}"; c += 1
            try:
                shutil.move(str(src), str(dst)); ok += 1
            except Exception:
                gagal += 1
    return folder_bulan, ok, gagal


# ─────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────
def send_email_subfolder(tipe: str, pdf_files: list,
                         daftar_pelanggan: str, cfg: dict) -> tuple:
    to_list  = cfg.get("to", [])
    cc_list  = cfg.get("cc", [])
    bcc_list = cfg.get("bcc", [])
    subject  = cfg.get("subject_template", "Laporan PDF - {tipe_layanan}").format(
                    tipe_layanan=tipe)
    body     = cfg.get("body_template", "").format(
                    tipe_layanan=tipe,
                    daftar_pelanggan=daftar_pelanggan,
                    jumlah_file=len(pdf_files))
    msg = MIMEMultipart()
    msg["From"]    = cfg["sender_email"]
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list: msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for pdf_path in pdf_files:
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
        msg.attach(part)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as srv:
            srv.login(cfg["sender_email"], cfg["sender_password"])
            srv.sendmail(cfg["sender_email"],
                         to_list + cc_list + bcc_list, msg.as_bytes())
        return True, f"Email [{tipe}] terkirim ke {', '.join(to_list)}"
    except smtplib.SMTPAuthenticationError:
        return False, "Login gagal — periksa App Password Gmail"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# PROSES DOWNLOAD
# ─────────────────────────────────────────────────────────────
def run_download(date_from, date_to, save_dir: str) -> int:
    print(f"\nLogin sebagai {USERNAME}...")
    token   = get_token()
    headers = api_headers(token)
    print("Login berhasil!")

    print("Mengambil data order...")
    orders_active   = fetch_orders(headers, is_finish=False)
    orders_finished = fetch_orders(headers, is_finish=True)

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
            detail    = fetch_order_detail(headers, order_id)
            documents = detail.get("service_documents", [])
        except Exception as e:
            print(f"  → Gagal ambil detail: {e}")
            continue

        saved = 0
        for doc in documents:
            if doc.get("document_type_code") in TARGET_DOCS:
                if save_document(number, doc, save_dir):
                    saved += 1

        if saved == 0:
            print(f"  → Tidak ada dokumen STAT/STBA tersedia")
        else:
            total_saved += saved

    return total_saved


# ─────────────────────────────────────────────────────────────
# PROSES MERGE
# ─────────────────────────────────────────────────────────────
def run_merge(source_dir: str, output_dir: str, digit_count: int = 6):
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    all_pdfs = find_pdfs(source_dir)
    print(f"\n🔍 Scan: {len(all_pdfs)} file PDF ditemukan")

    pool = {"FIRST": defaultdict(list), "SECOND": defaultdict(list)}
    unrecognized = []
    for pdf in all_pdfs:
        tag = detect_tag(pdf)
        key = extract_key(pdf.name, digit_count)
        if tag is None or key is None:
            unrecognized.append(pdf); continue
        pool[tag][key].append(pdf)

    stba_count  = sum(len(v) for v in pool["FIRST"].values())
    stat_count  = sum(len(v) for v in pool["SECOND"].values())
    print(f"📂 Klasifikasi: STBA={stba_count}  STAT={stat_count}  "
          f"Tidak dikenali={len(unrecognized)}")

    all_keys   = set(pool["FIRST"]) | set(pool["SECOND"])
    pairs_ok   = sorted(k for k in all_keys
                        if pool["FIRST"].get(k) and pool["SECOND"].get(k))
    only_first = sorted(k for k in all_keys
                        if pool["FIRST"].get(k) and not pool["SECOND"].get(k))
    print(f"🔗 Pasangan cocok: {len(pairs_ok)}  |  "
          f"Hanya STBA: {len(only_first)}")
    print()

    summary     = defaultdict(list)
    txt_entries = defaultdict(list)
    moved_pairs = []
    success = failed = 0

    for key in pairs_ok:
        first_file  = sorted(pool["FIRST"][key])[0]
        second_file = sorted(pool["SECOND"][key])[0]
        nama, tipe, folder_name = extract_stba_info(first_file)

        tipe_folder = out_root / folder_name
        tipe_folder.mkdir(parents=True, exist_ok=True)

        output_file = tipe_folder / f"{key}.pdf"
        c = 1
        while output_file.exists():
            output_file = tipe_folder / f"{key}_{c}.pdf"; c += 1

        ok = merge_two(first_file, second_file, output_file)
        if ok:
            success += 1
            moved_pairs.append((first_file, second_file))
            summary[folder_name].append((key, nama, output_file))
            txt_entries[folder_name].append((key, nama))
            print(f"  ✓ [{key}]  {nama}  → {folder_name}/")
        else:
            failed += 1
            print(f"  ✗ [{key}] Gagal merge")

    # File Kosong
    file_kosong_list = []
    if only_first:
        kosong_folder = out_root / FILE_KOSONG_FOLDER
        kosong_folder.mkdir(parents=True, exist_ok=True)
        for k in only_first:
            src = pool["FIRST"][k][0]
            dst = kosong_folder / src.name
            c = 1
            while dst.exists():
                dst = kosong_folder / f"{src.stem}_{c}{src.suffix}"; c += 1
            try:
                shutil.move(str(src), str(dst))
                file_kosong_list.append(dst)
                print(f"  ⚠ {src.name} → File Kosong/")
            except Exception:
                pass

    # Arsip file mentah
    folder_bulan = ""
    if moved_pairs:
        folder_bulan, pindah_ok, _ = pindah_file_mentah(source_dir, moved_pairs)
        print(f"\n📦 {pindah_ok} file mentah diarsip ke [{folder_bulan}]")

    # Simpan .txt per folder
    for folder_name, entries in sorted(txt_entries.items()):
        tipe_folder = out_root / folder_name
        txt_path    = tipe_folder / f"daftar_pelanggan_{folder_name}.txt"
        save_note_txt(txt_path, entries)

    # Ringkasan total
    if summary:
        save_ringkasan_total(out_root, summary, file_kosong_list)
        print(f"📊 ringkasan_total.txt disimpan")

    print(f"\n{'='*50}")
    print(f"Merge selesai: {success} berhasil, {failed} gagal, "
          f"{len(file_kosong_list)} file kosong")
    if folder_bulan:
        print(f"Diarsip ke  : {folder_bulan}")
    print(f"{'='*50}")

    return dict(summary), folder_bulan


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def input_tanggal(prompt: str):
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%d-%m-%Y").date()
        except ValueError:
            print("  Format salah. Gunakan DD-MM-YYYY (contoh: 01-04-2026)")


def main():
    cfg = load_config()

    print("=" * 50)
    print("  Galva Download + Merge")
    print("=" * 50)
    print(f"Sumber : {cfg['source_dir']}")
    print(f"Output : {cfg['output_dir']}")
    print("=" * 50)
    print("Format tanggal: DD-MM-YYYY\n")

    date_from = input_tanggal("Dari tanggal  : ")
    date_to   = input_tanggal("Sampai tanggal: ")
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    save_dir = cfg["source_dir"]
    os.makedirs(save_dir, exist_ok=True)

    # ── TAHAP 1: DOWNLOAD ──
    print(f"\n{'='*50}")
    print("  TAHAP 1: DOWNLOAD")
    print(f"{'='*50}")

    try:
        total_downloaded = run_download(date_from, date_to, save_dir)
    except Exception as e:
        print(f"\nDownload gagal: {e}")
        return

    print(f"\nTotal file diunduh: {total_downloaded}")
    print(f"Lokasi: {save_dir}")

    if total_downloaded == 0:
        print("\nTidak ada file baru. Proses selesai.")
        return

    # ── TAHAP 2: KONFIRMASI MERGE ──
    print(f"\n{'='*50}")
    jawab = input("Lanjut ke proses merge? (y/n): ").strip().lower()
    if jawab != "y":
        print("Proses merge dilewati. Selesai.")
        return

    print(f"\n{'='*50}")
    print("  TAHAP 2: MERGE PDF")
    print(f"{'='*50}")

    summary, _ = run_merge(
        cfg["source_dir"],
        cfg["output_dir"],
        cfg.get("digit_count", 6)
    )

    if not summary:
        print("Tidak ada hasil merge.")
        return

    # ── TAHAP 3: KONFIRMASI EMAIL ──
    if not cfg.get("sender_email") or not cfg.get("to"):
        print("\nEmail belum dikonfigurasi. Proses selesai.")
        return

    print(f"\n{'='*50}")
    print("  TAHAP 3: KIRIM EMAIL")
    print(f"{'='*50}")

    for folder_name, entries in sorted(summary.items()):
        print(f"\n  {folder_name} — {len(entries)} file:")
        for key, nama, path in entries:
            print(f"    • {Path(str(path)).name}  {nama}")

    print()
    jawab = input("Kirim semua file di atas melalui email? (y/n): ").strip().lower()
    if jawab != "y":
        print("Pengiriman email dilewati. Selesai.")
        return

    ok = fail = 0
    for tipe, entries in sorted(summary.items()):
        pdf_files  = [e[2] for e in entries]
        daftar_str = "\n".join(f"  {k} - {n}" for k, n, _ in entries)
        success, msg = send_email_subfolder(tipe, pdf_files, daftar_str, cfg)
        if success:
            print(f"  ✓ [{tipe}] {msg}")
            ok += 1
        else:
            print(f"  ✗ [{tipe}] {msg}")
            fail += 1

    print(f"\nEmail terkirim: {ok}  Gagal: {fail}")
    print("\nSelesai!")


if __name__ == "__main__":
    main()
