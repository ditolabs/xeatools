#!/usr/bin/env python3
"""
merge_web.py — Web GUI untuk merge_pdf + Download + Schedule
Instalasi: pip install flask requests pypdf apscheduler
Jalankan : python merge_web.py
Buka     : Chrome Android → http://localhost:5000
"""

import sys
import json
import queue
import threading
import re
import ssl
import shutil
import smtplib
import os
import requests as req_lib
import base64
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

try:
    from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context
except ImportError:
    print("ERROR: pip install flask"); sys.exit(1)

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("ERROR: pip install pypdf"); sys.exit(1)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    print("PERINGATAN: pip install apscheduler (fitur schedule tidak aktif)")

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────────────────────
CONFIG_FILE = str(Path.home() / "merge_pdf_config.json")

DEFAULT_CONFIG = {
    "source_dir"          : "/sdcard/Documents",
    "output_dir"          : "/sdcard/Documents/Hasil",
    "digit_count"         : 6,
    "sender_email"        : "",
    "sender_password"     : "",
    "to"                  : [],
    "cc"                  : [],
    "bcc"                 : [],
    "subject_template"    : "Laporan PDF - {tipe_layanan}",
    "body_template"       : (
        "Halo,\n\nBerikut daftar pelanggan untuk Tipe Layanan [{tipe_layanan}]:\n\n"
        "{daftar_pelanggan}\n\nTerlampir {jumlah_file} file PDF.\n\n"
        "Email ini dikirim otomatis oleh script merge_pdf."
    ),
    "galva_username"      : "",
    "galva_password"      : "",
    "schedule_enabled"    : False,
    "schedule_time"       : "06:00",
    "schedule_mode"       : "daily",
    "schedule_from"       : "",
    "schedule_to"         : "",
    "schedule_last_run"   : "",
    "schedule_last_result": "",
    "schedule_auto_merge" : False,
    "schedule_auto_email" : False,
    "schedule_notif"      : True,
}

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

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────────────────────
# KONSTANTA
# ─────────────────────────────────────────────────────────────
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
LOG_FILE           = "merge_log.txt"
ESTIMASI_FILE      = "estimasi_biaya.txt"
SMTP_HOST          = "smtp.gmail.com"
SMTP_PORT          = 465

GALVA_BASE_URL     = "https://api.galva.co.id"
GALVA_KEY_USER_ID  = 372
GALVA_LOGIN_HEADERS = {
    "user-agent"     : "Dart/3.4 (dart:io)",
    "accept"         : "application/json",
    "accept-encoding": "gzip",
    "authorization"  : "Basic Z2FsdmFfYmU6YXBpQGJlMjAyMTAxMTQ=",
    "content-type"   : "application/json; charset=utf-8",
}
GALVA_TRIGGER_MAP = {
    "INST": ["CL"],
    "MAIN": ["CL"],
    "TKRP": ["CL"],
    "SERV": ["FN", "CL"],
    "PLOT": ["CL"],
}
GALVA_TARGET_DOCS = ["STAT", "STBA"]

# ─────────────────────────────────────────────────────────────
# STATE GLOBAL
# ─────────────────────────────────────────────────────────────
_state = {
    "running" : False,
    "result"  : None,
}
_scheduler = None

# ─────────────────────────────────────────────────────────────
# UTILITAS MERGE
# ─────────────────────────────────────────────────────────────
def detect_tag(path: Path, new_format: bool = False) -> str:
    name = path.stem.upper()
    if new_format:
        if name.endswith("_STBA"): return "FIRST"
        if name.endswith("_STAT"): return "SECOND"
    else:
        if "STBA"  in name: return "FIRST"
        if "STATS" in name: return "SECOND"
    return None

def extract_key(filename: str, n: int, new_format: bool = False) -> str:
    stem = Path(filename).stem
    if new_format:
        for suffix in ("_STBA", "_STAT", "_stba", "_stat"):
            if stem.upper().endswith(suffix.upper()):
                stem = stem[:-len(suffix)]; break
    alnum = re.sub(r"[^A-Za-z0-9]", "", stem)
    return alnum[-n:].upper() if len(alnum) >= n else None

def find_pdfs(source_dir: str) -> list:
    source = Path(source_dir)
    if not source.exists(): return []
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
                if nama != "-" and tipe != "-": break
            if nama != "-" and tipe != "-": break
    except Exception:
        pass
    folder_name = None
    for pdf_label, folder in TIPE_LAYANAN_MAP.items():
        if tipe.upper() == pdf_label.upper():
            tipe = pdf_label; folder_name = folder; break
    if folder_name is None:
        folder_name = FALLBACK_FOLDER
        if tipe == "-": tipe = FALLBACK_FOLDER
    return nama, tipe, folder_name

def merge_two(first: Path, second: Path, output_path: Path) -> bool:
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

def save_estimasi_biaya(out_root: Path, summary: dict, file_kosong: list) -> Path:
    total = 0
    lines = ["ESTIMASI BIAYA PER TIPE LAYANAN", "=" * 54, ""]
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
              f"  {'TOTAL KESELURUHAN':<22}                     {format_rupiah(total)}", ""]
    if file_kosong:
        lines.append(f"  File Kosong (tidak dihitung) : {len(file_kosong)} file")
        for f in file_kosong:
            lines.append(f"    - {f.name}")
    lines += ["", f"Dibuat otomatis: {datetime.now().strftime('%d %B %Y %H:%M')}"]
    txt_path = out_root / ESTIMASI_FILE
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
# UTILITAS DOWNLOAD
# ─────────────────────────────────────────────────────────────
def galva_get_token(username: str, password: str) -> str:
    resp = req_lib.post(
        f"{GALVA_BASE_URL}/xsyst/api/ldap/xea",
        headers=GALVA_LOGIN_HEADERS,
        json={"user_name": username, "user_password": password}
    )
    resp.raise_for_status()
    token = (resp.json().get("data", {}) or {}).get("jwt_token")
    if not token: raise Exception("Token tidak ditemukan di response login")
    return token

def galva_api_headers(token: str) -> dict:
    return {
        "user-agent"   : "Dart/3.4 (dart:io)",
        "accept"       : "application/json",
        "authorization": f"Bearer {token}",
        "content-type" : "application/json",
    }

def galva_fetch_orders(headers: dict, is_finish: bool) -> list:
    resp = req_lib.get(
        f"{GALVA_BASE_URL}/xsyst/api/engineer-service-orders",
        params={
            "keyUserId": GALVA_KEY_USER_ID,
            "isFinish": "true" if is_finish else "false",
            "onlyMyTask": "true", "serviceOrderNumber": "",
            "userTicketInboxNumber": "", "supportTypeCode": "",
            "serialNumber": "", "customerDetailName": "",
            "engineerKeyuserId": "", "ticketStatusCode": "",
            "startDate": "", "endDate": ""
        },
        headers=headers
    )
    resp.raise_for_status()
    return resp.json().get("data", [])

def galva_fetch_detail(headers: dict, order_id: int) -> dict:
    resp = req_lib.get(
        f"{GALVA_BASE_URL}/xsyst/api/engineer-service-order",
        params={"keyUserId": GALVA_KEY_USER_ID, "serviceOrderId": order_id},
        headers=headers
    )
    resp.raise_for_status()
    return resp.json().get("data", {})

def galva_parse_date(date_str: str):
    if not date_str: return None
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None

def galva_should_download(type_code: str, status_code: str) -> bool:
    triggers = GALVA_TRIGGER_MAP.get(type_code)
    return bool(triggers and status_code in triggers)

def galva_decode_base64(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        url_safe = raw.replace('-', '+').replace('_', '/')
        padded2  = url_safe + "=" * (-len(url_safe) % 4)
        return base64.b64decode(padded2)

def galva_save_document(support_number: str, doc: dict, save_dir: str) -> tuple:
    ext      = doc.get("document_extension") or "pdf"
    doc_code = doc.get("document_type_code", "DOC")
    raw      = doc.get("document")
    if not raw: return False, ""
    filename = f"{support_number}_{doc_code}.{ext}".replace("/", "-")
    filepath = os.path.join(save_dir, filename)
    if os.path.exists(filepath): return False, filename
    try:
        pdf_bytes = galva_decode_base64(raw)
    except Exception:
        return False, filename
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)
    return True, filename


def is_order_already_processed(support_number: str, save_dir: str, output_dir: str) -> str:
    """
    Cek apakah order sudah pernah diproses.
    Cek berdasarkan nomor surat tugas (support_number) di semua lokasi:
    1. File mentah (STAT/STBA) di folder sumber
    2. File mentah di subfolder arsip bulan (dalam folder sumber)
    3. File hasil merge di subfolder output
    """
    number_clean = support_number.replace("/", "-")
    # Ambil kunci 6 karakter alphanumeric terakhir
    alnum = re.sub(r"[^A-Za-z0-9]", "", support_number)
    key6  = alnum[-6:].upper() if len(alnum) >= 6 else alnum.upper()

    # ── Cek 1: file mentah di folder sumber ──────────────────
    src = Path(save_dir)
    for doc_code in ["STAT", "STBA"]:
        if (src / f"{number_clean}_{doc_code}.pdf").exists():
            return f"file {doc_code} ada di folder sumber"

    # ── Cek 2: file mentah di subfolder arsip bulan ──────────
    # Arsip bisa ada di dalam folder sumber (Januari 2026/, Februari 2026/, dst)
    # atau di dalam folder output
    for search_root in [src, Path(output_dir)]:
        if not search_root.exists():
            continue
        for child in search_root.iterdir():
            if not child.is_dir():
                continue
            # Cek apakah namanya seperti "Maret 2026", "April 2026", dll
            # dengan glob pattern *_{STAT,STBA}.pdf
            for doc_code in ["STAT", "STBA"]:
                if list(child.glob(f"{number_clean}_{doc_code}.pdf")):
                    return f"file {doc_code} diarsip di {child.name}/"
            # Cek juga rekursif satu level lebih dalam
            for subchild in child.iterdir():
                if not subchild.is_dir():
                    continue
                for doc_code in ["STAT", "STBA"]:
                    if list(subchild.glob(f"{number_clean}_{doc_code}.pdf")):
                        return f"file {doc_code} diarsip di {child.name}/{subchild.name}/"

    # ── Cek 3: file hasil merge di subfolder output ───────────
    out = Path(output_dir)
    if out.exists():
        # Cek di semua subfolder tipe (Install/, Maintenance/, dll)
        for tipe_dir in out.iterdir():
            if not tipe_dir.is_dir():
                continue
            # File merge namanya {key6}.pdf atau {key6}_N.pdf
            if list(tipe_dir.glob(f"{key6}.pdf")) or list(tipe_dir.glob(f"{key6}_*.pdf")):
                return f"hasil merge ada di {tipe_dir.name}/"

    return ""  # belum ada, silakan download

# ─────────────────────────────────────────────────────────────
# CORE: run_merge & run_download
# ─────────────────────────────────────────────────────────────
def run_merge(source_dir: str, output_dir: str,
              digit_count: int = 6, new_format: bool = False, cb=None) -> dict:
    def emit(event, data):
        if cb: cb(event, data)

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    all_pdfs = find_pdfs(source_dir)
    emit("scan", {"total": len(all_pdfs), "source_dir": source_dir})

    pool = {"FIRST": defaultdict(list), "SECOND": defaultdict(list)}
    unrecognized = []
    for pdf in all_pdfs:
        tag = detect_tag(pdf, new_format)
        key = extract_key(pdf.name, digit_count, new_format)
        if tag is None or key is None:
            unrecognized.append(pdf); continue
        pool[tag][key].append(pdf)

    emit("classify", {
        "stba"   : sum(len(v) for v in pool["FIRST"].values()),
        "stats"  : sum(len(v) for v in pool["SECOND"].values()),
        "unknown": len(unrecognized),
    })

    all_keys   = set(pool["FIRST"]) | set(pool["SECOND"])
    pairs_ok   = sorted(k for k in all_keys if pool["FIRST"].get(k) and pool["SECOND"].get(k))
    only_first = sorted(k for k in all_keys if pool["FIRST"].get(k) and not pool["SECOND"].get(k))
    only_second= sorted(k for k in all_keys if pool["SECOND"].get(k) and not pool["FIRST"].get(k))
    emit("pair_found", {"pairs": len(pairs_ok),
                        "only_stba": len(only_first), "only_stats": len(only_second)})

    summary = defaultdict(list)
    txt_entries = defaultdict(list)
    moved_pairs = []
    log_lines   = []
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
            log_lines.append(f"[OK] {output_file}")
            emit("merge_ok", {"key": key, "nama": nama, "tipe": tipe,
                              "folder": folder_name, "output": str(output_file)})
        else:
            failed += 1
            emit("merge_fail", {"key": key})

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
                emit("file_kosong", {"name": src.name})
            except Exception as e:
                emit("merge_fail", {"key": k, "reason": str(e)})

    folder_bulan = ""
    if moved_pairs:
        folder_bulan, pindah_ok, _ = pindah_file_mentah(source_dir, moved_pairs)
        emit("arsip", {"folder": folder_bulan, "jumlah": pindah_ok})

    for folder_name, entries in sorted(txt_entries.items()):
        tipe_folder = out_root / folder_name
        txt_path    = tipe_folder / f"daftar_pelanggan_{folder_name}.txt"
        save_note_txt(txt_path, entries)
        emit("txt_saved", {"path": str(txt_path)})

    if summary:
        save_estimasi_biaya(out_root, summary, file_kosong_list)
        emit("estimasi", {})

    log_path = out_root / LOG_FILE
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write("MERGE LOG\n")
        lf.write(f"Sumber : {source_dir}\nOutput : {output_dir}\n\n")
        lf.write("\n".join(log_lines))

    result = {
        "success"     : success, "failed": failed,
        "file_kosong" : len(file_kosong_list),
        "only_stats"  : len(only_second),
        "folder_bulan": folder_bulan,
        "summary"     : dict(summary),
        "log_path"    : str(log_path),
        "output_dir"  : output_dir,
    }
    emit("done", result)
    return result


def run_download(date_from_str: str, date_to_str: str,
                 username: str, password: str,
                 save_dir: str, cb=None) -> dict:
    def emit(event, data):
        if cb: cb(event, data)

    try:
        date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        date_to   = datetime.strptime(date_to_str,   "%Y-%m-%d").date()
        if date_from > date_to:
            date_from, date_to = date_to, date_from
    except ValueError as e:
        emit("dl_error", {"msg": f"Format tanggal salah: {e}"}); return {}

    emit("dl_login", {"username": username})
    try:
        token   = galva_get_token(username, password)
        headers = galva_api_headers(token)
    except Exception as e:
        emit("dl_error", {"msg": f"Login gagal: {e}"}); return {}
    emit("dl_login_ok", {})

    emit("dl_fetch", {})
    try:
        orders_active   = galva_fetch_orders(headers, is_finish=False)
        orders_finished = galva_fetch_orders(headers, is_finish=True)
    except Exception as e:
        emit("dl_error", {"msg": f"Gagal ambil order: {e}"}); return {}

    seen, all_orders = set(), []
    for o in orders_active + orders_finished:
        oid = o.get("service_order_id")
        if oid not in seen:
            seen.add(oid); all_orders.append(o)

    qualified = []
    skipped_status = skipped_date = 0
    for order in all_orders:
        type_code = order.get("support_type_code", "")
        status    = order.get("current_status_code", "")
        processed = galva_parse_date(order.get("latest_processed_date"))
        if not galva_should_download(type_code, status):
            skipped_status += 1; continue
        if not processed or not (date_from <= processed <= date_to):
            skipped_date += 1; continue
        qualified.append(order)

    emit("dl_summary", {
        "total"      : len(all_orders),
        "skip_status": skipped_status,
        "skip_date"  : skipped_date,
        "qualified"  : len(qualified),
        "date_from"  : date_from.strftime("%d %b %Y"),
        "date_to"    : date_to.strftime("%d %b %Y"),
    })

    os.makedirs(save_dir, exist_ok=True)
    total_saved  = 0
    skipped_dup  = 0
    cfg_output   = load_config().get("output_dir", save_dir + "/Hasil")

    for order in qualified:
        order_id    = order.get("service_order_id")
        number      = order.get("support_number", str(order_id))
        type_name   = order.get("support_type", "")
        status_name = order.get("current_status_name", "")
        customer    = order.get("customer_detail_name", "")
        processed   = galva_parse_date(order.get("latest_processed_date"))

        # ── Cek duplikat sebelum hit API detail ──────────────
        reason = is_order_already_processed(number, save_dir, cfg_output)
        if reason:
            skipped_dup += 1
            emit("dl_skip_dup", {"number": number, "reason": reason})
            continue

        emit("dl_order", {
            "number": number, "type": type_name, "status": status_name,
            "date"  : processed.strftime("%d %b %Y") if processed else "-",
            "customer": customer,
        })
        try:
            detail    = galva_fetch_detail(headers, order_id)
            documents = detail.get("service_documents", [])
        except Exception as e:
            emit("dl_order_err", {"number": number, "msg": str(e)}); continue
        saved = 0
        for doc in documents:
            if doc.get("document_type_code") in GALVA_TARGET_DOCS:
                ok, filename = galva_save_document(number, doc, save_dir)
                if ok:
                    saved += 1
                    emit("dl_file_ok", {"filename": filename})
        if saved == 0:
            emit("dl_no_doc", {"number": number})
        else:
            total_saved += saved

    result = {"total_saved": total_saved, "qualified": len(qualified),
              "skipped_dup": skipped_dup}
    emit("dl_done", result)
    return result


def do_send_emails(summary: dict, cfg: dict, cb=None) -> dict:
    def emit(ev, data):
        if cb: cb(ev, data)
    ok = fail = 0; detail = []
    for tipe, entries in sorted(summary.items()):
        pdf_files  = [e[2] for e in entries]
        daftar_str = "\n".join(f"  {k} - {n}" for k, n, _ in entries)
        to_list  = cfg.get("to", [])
        cc_list  = cfg.get("cc", [])
        bcc_list = cfg.get("bcc", [])
        subject  = cfg.get("subject_template", "Laporan PDF - {tipe_layanan}").format(tipe_layanan=tipe)
        body     = cfg.get("body_template", "").format(
            tipe_layanan=tipe, daftar_pelanggan=daftar_str, jumlah_file=len(pdf_files))
        msg = MIMEMultipart()
        msg["From"] = cfg["sender_email"]; msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject
        if cc_list: msg["Cc"] = ", ".join(cc_list)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for pdf_path in pdf_files:
            with open(pdf_path, "rb") as f:
                part = MIMEBase("application", "pdf"); part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
            msg.attach(part)
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as srv:
                srv.login(cfg["sender_email"], cfg["sender_password"])
                srv.sendmail(cfg["sender_email"], to_list + cc_list + bcc_list, msg.as_bytes())
            s_flag = True; s_msg = f"Email [{tipe}] terkirim ke {', '.join(to_list)}"
        except smtplib.SMTPAuthenticationError:
            s_flag = False; s_msg = "Login gagal — periksa App Password Gmail"
        except Exception as e:
            s_flag = False; s_msg = str(e)
        detail.append((tipe, s_flag, s_msg))
        emit("email_result", {"tipe": tipe, "ok": s_flag, "msg": s_msg})
        if s_flag: ok += 1
        else: fail += 1
    return {"ok": ok, "fail": fail, "detail": detail}


# ─────────────────────────────────────────────────────────────
# SCHEDULER — dengan fallback timezone untuk Termux
# ─────────────────────────────────────────────────────────────
def get_scheduler_timezone():
    """Coba berbagai cara mendapatkan timezone, fallback ke UTC+7."""
    # Cara 1: tzdata package
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Jakarta")
    except Exception:
        pass
    # Cara 2: pytz
    try:
        import pytz
        return pytz.timezone("Asia/Jakarta")
    except Exception:
        pass
    # Cara 3: UTC+7 fixed offset
    try:
        from datetime import timezone, timedelta
        return timezone(timedelta(hours=7))
    except Exception:
        pass
    # Cara 4: local timezone
    try:
        from apscheduler.util import get_localzone
        return get_localzone()
    except Exception:
        pass
    return "UTC"


# ─────────────────────────────────────────────────────────────
# NOTIFIKASI ANDROID (Termux:API)
# ─────────────────────────────────────────────────────────────
def notif(title: str, content: str, progress: int = -1,
          progress_max: int = 100, ongoing: bool = False, notif_id: int = 1):
    """Kirim notifikasi Android via termux-notification. Graceful fallback."""
    try:
        import subprocess
        cmd = [
            "termux-notification",
            "--id",       str(notif_id),
            "--title",    title,
            "--content",  content,
            "--icon",     "ic_menu_upload",
        ]
        if ongoing:
            cmd.append("--ongoing")
        if progress >= 0:
            cmd += ["--progress", str(progress),
                    "--progress-max", str(progress_max)]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # Termux:API tidak tersedia — lewati saja

def notif_dismiss(notif_id: int = 1):
    """Hapus notifikasi."""
    try:
        import subprocess
        subprocess.Popen(
            ["termux-notification-remove", str(notif_id)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def scheduled_download_job():
    from datetime import timedelta
    cfg        = load_config()
    username   = cfg.get("galva_username", "")
    password   = cfg.get("galva_password", "")
    save_dir   = cfg.get("source_dir", "/sdcard/Documents")
    mode       = cfg.get("schedule_mode", "daily")
    auto_merge = cfg.get("schedule_auto_merge", False)
    auto_email = cfg.get("schedule_auto_email", False)
    use_notif  = cfg.get("schedule_notif", True)
    today      = date.today()

    # ── Hitung rentang tanggal ────────────────────────────────
    if mode == "daily":
        date_from = date_to = today
    elif mode == "weekly":
        from_str = cfg.get("schedule_from", "")
        try:
            date_from = datetime.strptime(from_str, "%Y-%m-%d").date()
        except Exception:
            date_from = today
        date_to = date_from + timedelta(days=6)
    elif mode == "monthly":
        from_str = cfg.get("schedule_from", "")
        try:
            d = datetime.strptime(from_str, "%Y-%m-%d").date()
        except Exception:
            d = today
        date_from = d.replace(day=1)
        if d.month == 12:
            date_to = d.replace(month=12, day=31)
        else:
            date_to = d.replace(month=d.month+1, day=1) - timedelta(days=1)
    else:
        date_from = date_to = today

    NOTIF_ID = 42
    msg_parts = []

    # ── TAHAP 1: DOWNLOAD ─────────────────────────────────────
    if use_notif:
        notif("📥 Schedule Berjalan",
              "Login dan mengambil daftar order...",
              progress=0, ongoing=True, notif_id=NOTIF_ID)

    dl_result = {}
    try:
        # Callback untuk update notifikasi per file
        saved_count = [0]
        total_qualified = [0]

        def dl_cb(event, data):
            if event == "dl_summary":
                total_qualified[0] = data.get("qualified", 0)
                if use_notif and total_qualified[0] > 0:
                    notif("📥 Schedule — Download",
                          "0/" + str(total_qualified[0]) + " file diunduh...",
                          progress=0, progress_max=total_qualified[0],
                          ongoing=True, notif_id=NOTIF_ID)
            elif event == "dl_file_ok":
                saved_count[0] += 1
                n = saved_count[0]
                tot = total_qualified[0] or 1
                if use_notif:
                    notif("📥 Schedule — Download",
                          str(n) + "/" + str(tot) + " file diunduh...",
                          progress=n, progress_max=tot,
                          ongoing=True, notif_id=NOTIF_ID)

        dl_result = run_download(
            date_from.strftime("%Y-%m-%d"),
            date_to.strftime("%Y-%m-%d"),
            username, password, save_dir, dl_cb
        )
        total_saved = dl_result.get("total_saved", 0)
        msg_parts.append(f"Download: {total_saved} file")
    except Exception as e:
        msg_parts.append(f"Download error: {e}")
        if use_notif:
            notif("❌ Schedule Gagal", str(e), notif_id=NOTIF_ID)
            import time; time.sleep(4); notif_dismiss(NOTIF_ID)
        cfg["schedule_last_run"]    = datetime.now().strftime("%d %b %Y %H:%M")
        cfg["schedule_last_result"] = " | ".join(msg_parts)
        save_config(cfg)
        return

    # ── TAHAP 2: AUTO MERGE ───────────────────────────────────
    merge_summary = {}
    if auto_merge and dl_result.get("total_saved", 0) > 0:
        if use_notif:
            notif("🔀 Schedule — Merge",
                  "Menggabungkan PDF...",
                  progress=-1, ongoing=True, notif_id=NOTIF_ID)
        try:
            merge_result = run_merge(
                cfg["source_dir"], cfg["output_dir"],
                cfg.get("digit_count", 6), True  # new_format=True
            )
            merge_summary = merge_result.get("summary", {})
            success = merge_result.get("success", 0)
            msg_parts.append(f"Merge: {success} pasang")
        except Exception as e:
            msg_parts.append(f"Merge error: {e}")

    # ── TAHAP 3: AUTO EMAIL ───────────────────────────────────
    if auto_email and auto_merge and merge_summary:
        if use_notif:
            notif("📧 Schedule — Email",
                  "Mengirim email...",
                  progress=-1, ongoing=True, notif_id=NOTIF_ID)
        try:
            email_result = do_send_emails(merge_summary, cfg)
            ok   = email_result.get("ok", 0)
            fail = email_result.get("fail", 0)
            msg_parts.append(f"Email: {ok} terkirim" + (f", {fail} gagal" if fail else ""))
        except Exception as e:
            msg_parts.append(f"Email error: {e}")

    # ── Notifikasi selesai ────────────────────────────────────
    final_msg = " | ".join(msg_parts)
    if use_notif:
        notif_dismiss(NOTIF_ID)
        import time; time.sleep(0.5)
        notif("✅ Schedule Selesai", final_msg, notif_id=NOTIF_ID)
        # Notifikasi selesai hilang setelah 10 detik
        import threading
        threading.Timer(10, notif_dismiss, [NOTIF_ID]).start()

    cfg["schedule_last_run"]    = datetime.now().strftime("%d %b %Y %H:%M")
    cfg["schedule_last_result"] = final_msg
    save_config(cfg)
    print(f"[Schedule] {cfg['schedule_last_run']} — {final_msg}")


def init_scheduler():
    global _scheduler
    if not HAS_SCHEDULER:
        return
    try:
        tz = get_scheduler_timezone()
        _scheduler = BackgroundScheduler(timezone=tz)
        cfg = load_config()
        if cfg.get("schedule_enabled"):
            apply_schedule(cfg)
        _scheduler.start()
        print(f"[Schedule] Scheduler aktif (timezone: {tz})")
    except Exception as e:
        print(f"[Schedule] Gagal inisialisasi: {e}")
        _scheduler = None


def apply_schedule(cfg: dict):
    global _scheduler
    if not HAS_SCHEDULER or _scheduler is None:
        return
    try:
        _scheduler.remove_job("auto_download")
    except Exception:
        pass
    if not cfg.get("schedule_enabled"):
        return
    time_str = cfg.get("schedule_time", "06:00")
    try:
        hour, minute = time_str.split(":")
        _scheduler.add_job(
            scheduled_download_job,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="auto_download",
            replace_existing=True
        )
        print(f"[Schedule] Aktif — setiap hari pukul {time_str}")
    except Exception as e:
        print(f"[Schedule] Gagal set jadwal: {e}")


# ─────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>merge_pdf</title>
<style>
  :root{--navy:#1a3c5e;--teal:#0891b2;--lteal:#e0f7ff;--green:#059669;
    --lgreen:#d1fae5;--orange:#ea580c;--red:#dc2626;--gray:#64748b;
    --lgray:#f0f4f8;--dark:#0f172a;--white:#ffffff;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:'Segoe UI',sans-serif;background:var(--lgray);color:var(--dark);min-height:100vh;}
  .header{background:var(--navy);color:var(--white);padding:14px 20px;position:sticky;top:0;z-index:99;
    display:flex;align-items:center;gap:12px;border-bottom:3px solid var(--teal);}
  .header h1{font-size:1.15rem;font-weight:700;color:var(--teal);}
  .header span{font-size:0.78rem;color:#94a3b8;}
  .tabs{display:flex;background:var(--navy);padding:0 12px;gap:2px;overflow-x:auto;}
  .tab{padding:9px 14px;font-size:0.8rem;color:#94a3b8;border-bottom:3px solid transparent;
    cursor:pointer;transition:.2s;white-space:nowrap;}
  .tab.active{color:var(--teal);border-bottom-color:var(--teal);font-weight:600;}
  .section{display:none;padding:16px;max-width:680px;margin:0 auto;}
  .section.active{display:block;}
  .card{background:var(--white);border-radius:12px;border:1px solid #e2e8f0;
    margin-bottom:14px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06);}
  .card-header{background:var(--navy);color:var(--white);padding:10px 16px;
    font-weight:600;font-size:.9rem;border-left:4px solid var(--teal);}
  .card-body{padding:14px 16px;}
  label{display:block;font-size:.82rem;color:var(--gray);font-weight:600;margin-bottom:4px;margin-top:10px;}
  input[type=text],input[type=email],input[type=password],input[type=number],input[type=date],input[type=time]{
    width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;
    font-size:.9rem;background:var(--lgray);transition:.2s;}
  input:focus{outline:none;border-color:var(--teal);background:var(--white);
    box-shadow:0 0 0 3px rgba(8,145,178,.15);}
  .toggle-row{display:flex;align-items:center;justify-content:space-between;
    padding:10px 0;border-bottom:1px solid #e2e8f0;}
  .toggle-label{font-size:.9rem;font-weight:600;color:var(--dark);}
  .toggle-sub{font-size:.78rem;color:var(--gray);margin-top:2px;}
  .toggle{position:relative;width:48px;height:26px;flex-shrink:0;}
  .toggle input{opacity:0;width:0;height:0;}
  .slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;
    background:#cbd5e1;border-radius:26px;transition:.3s;}
  .slider:before{position:absolute;content:"";height:20px;width:20px;left:3px;bottom:3px;
    background:white;border-radius:50%;transition:.3s;}
  input:checked+.slider{background:var(--teal);}
  input:checked+.slider:before{transform:translateX(22px);}
  .btn{display:inline-flex;align-items:center;gap:7px;padding:11px 22px;border:none;
    border-radius:9px;font-size:.9rem;font-weight:600;cursor:pointer;transition:.2s;}
  .btn-primary{background:var(--teal);color:var(--white);}
  .btn-primary:hover{background:#0e7490;}
  .btn-primary:disabled{background:var(--gray);cursor:not-allowed;}
  .btn-success{background:var(--green);color:var(--white);}
  .btn-success:hover{background:#047857;}
  .btn-outline{background:transparent;color:var(--teal);border:2px solid var(--teal);}
  .btn-outline:hover{background:var(--lteal);}
  .btn-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;}
  #log-box,#dl-log-box{background:#1e293b;color:#e2e8f0;border-radius:10px;
    padding:12px;font-family:monospace;font-size:.82rem;height:300px;
    overflow-y:auto;white-space:pre-wrap;line-height:1.55;}
  .log-ok{color:#34d399;}.log-warn{color:#fbbf24;}.log-fail{color:#f87171;}
  .log-info{color:#67e8f9;}.log-dim{color:#64748b;}
  .progress-wrap{background:#e2e8f0;border-radius:99px;height:8px;margin:10px 0;overflow:hidden;}
  .progress-bar{background:var(--teal);height:100%;border-radius:99px;transition:width .4s;}
  table{width:100%;border-collapse:collapse;font-size:.88rem;}
  th{background:var(--navy);color:var(--white);padding:9px 12px;text-align:left;font-weight:600;}
  td{padding:8px 12px;border-bottom:1px solid #e2e8f0;}
  tr:nth-child(even) td{background:var(--lgray);}
  .badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.75rem;font-weight:600;}
  .badge-teal{background:var(--lteal);color:var(--teal);}
  .stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;}
  .stat{background:var(--white);border-radius:10px;padding:12px 14px;
    border-left:4px solid var(--teal);box-shadow:0 1px 3px rgba(0,0,0,.06);}
  .stat-num{font-size:1.6rem;font-weight:700;color:var(--navy);}
  .stat-lbl{font-size:.75rem;color:var(--gray);margin-top:2px;}
  .alert{padding:10px 14px;border-radius:8px;margin:10px 0;font-size:.88rem;}
  .alert-info{background:var(--lteal);color:var(--teal);}
  .alert-success{background:var(--lgreen);color:var(--green);}
  .alert-warn{background:#fff7ed;color:var(--orange);}
  .alert-error{background:#fef2f2;color:var(--red);}
  .file-list{background:var(--lgray);border-radius:8px;padding:10px 14px;
    max-height:200px;overflow-y:auto;margin:8px 0;}
  .file-item{padding:4px 0;font-size:.83rem;color:var(--dark);border-bottom:1px solid #e2e8f0;}
  .file-item:last-child{border:none;}
  .schedule-status{background:var(--lgray);border-radius:10px;padding:14px;margin-top:10px;}
  .schedule-status .row{display:flex;justify-content:space-between;
    padding:5px 0;font-size:.85rem;border-bottom:1px solid #e2e8f0;}
  .schedule-status .row:last-child{border:none;}
  .schedule-status .key{color:var(--gray);font-weight:600;}
  .schedule-status .val{color:var(--dark);}
  .date-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
  .spinner{display:inline-block;width:16px;height:16px;border:2px solid #fff;
    border-top-color:transparent;border-radius:50%;animation:spin .7s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .hidden{display:none!important;}
  .mt8{margin-top:8px;}.mt14{margin-top:14px;}
  .bold{font-weight:700;}.teal{color:var(--teal);}.green{color:var(--green);}
  .red{color:var(--red);}.dim{color:var(--gray);}
  select{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;
    font-size:.9rem;background:var(--lgray);}
  .sc-mode-btn{padding:10px 8px;border:2px solid #cbd5e1;border-radius:9px;
    font-size:.82rem;font-weight:600;cursor:pointer;background:var(--lgray);
    color:var(--gray);transition:.2s;text-align:center;}
  .sc-mode-btn:hover{border-color:var(--teal);color:var(--teal);}
  .sc-mode-active{border-color:var(--teal)!important;background:var(--lteal)!important;
    color:var(--teal)!important;}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>merge_pdf</h1>
    <span>PDF Automation Tool</span>
  </div>
</div>

<div class="tabs" id="tab-bar">
  <div class="tab active" data-tab="download">📥 Download</div>
  <div class="tab" data-tab="run">▶ Merge</div>
  <div class="tab" data-tab="schedule">⏰ Schedule</div>
  <div class="tab" data-tab="config">⚙ Konfigurasi</div>
</div>

<!-- ══════════ TAB: DOWNLOAD ══════════ -->
<div id="tab-download" class="section active">
  <div class="card">
    <div class="card-header">📥 Download STAT + STBA dari API Galva</div>
    <div class="card-body">
      <div class="date-row">
        <div>
          <label>Dari Tanggal</label>
          <input type="date" id="dl-from">
        </div>
        <div>
          <label>Sampai Tanggal</label>
          <input type="date" id="dl-to">
        </div>
      </div>
      <div class="progress-wrap hidden" id="dl-progress-wrap">
        <div class="progress-bar" id="dl-progress-bar" style="width:0%"></div>
      </div>
      <div id="dl-log-box">Siap mengunduh...\n</div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btn-dl" onclick="startDownload()">
          📥 Mulai Download
        </button>
        <button class="btn btn-outline" onclick="clearDlLog()">🗑 Bersihkan</button>
      </div>
    </div>
  </div>

  <div id="dl-result-section" class="hidden">
    <div class="card">
      <div class="card-header">✅ Download Selesai</div>
      <div class="card-body">
        <div id="dl-result-stats"></div>
        <div id="dl-merge-btn-row" class="btn-row">
          <button class="btn btn-success" onclick="startMergeFromDownload()">
            ▶ Ya, Lanjut Merge
          </button>
          <button class="btn btn-outline" onclick="skipMerge()">✕ Lewati</button>
        </div>
        <div id="dl-merge-status"></div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════ TAB: MERGE ══════════ -->
<div id="tab-run" class="section">
  <div class="card">
    <div class="card-header">📂 Konfigurasi Aktif</div>
    <div class="card-body" id="cfg-info-body" style="font-size:.85rem;color:#64748b;">Memuat...</div>
  </div>
  <div class="card">
    <div class="card-header">▶ Proses Merge</div>
    <div class="card-body">
      <div class="progress-wrap hidden" id="progress-wrap">
        <div class="progress-bar" id="progress-bar" style="width:0%"></div>
      </div>
      <div id="log-box">Siap menjalankan merge...\n</div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btn-run" onclick="startMerge(false)">
          ▶ Mulai Merge
        </button>
        <button class="btn btn-outline" onclick="clearLog()">🗑 Bersihkan Log</button>
      </div>
    </div>
  </div>
  <div id="result-section" class="hidden">
    <div class="card">
      <div class="card-header">📊 Hasil Merge</div>
      <div class="card-body">
        <div class="stats" id="stats-grid"></div>
        <table><thead><tr><th>Tipe Layanan</th><th>Jumlah</th></tr></thead>
          <tbody id="summary-body"></tbody></table>
      </div>
    </div>
    <div id="email-section" class="card">
      <div class="card-header">📧 Kirim Email</div>
      <div class="card-body">
        <div id="email-file-list"></div>
        <div id="email-status"></div>
        <div class="btn-row" id="email-btn-row">
          <button class="btn btn-success" onclick="sendEmails()">📧 Ya, Kirim Email</button>
          <button class="btn btn-outline" onclick="cancelEmail()">✕ Lewati</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════ TAB: SCHEDULE ══════════ -->
<div id="tab-schedule" class="section">
  <div class="card">
    <div class="card-header">⏰ Jadwal Download Otomatis</div>
    <div class="card-body">
      <div class="toggle-row">
        <div>
          <div class="toggle-label">Aktifkan Schedule</div>
          <div class="toggle-sub">Download otomatis setiap hari pada jam yang ditentukan</div>
        </div>
        <label class="toggle">
          <input type="checkbox" id="sc-enabled" onchange="toggleScheduleOptions()">
          <span class="slider"></span>
        </label>
      </div>

      <div id="sc-options">
        <label>Jam Pelaksanaan</label>
        <input type="time" id="sc-time" value="06:00">

        <label>Mode Rentang</label>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:4px;">
          <button id="sc-mode-daily"   class="sc-mode-btn sc-mode-active" onclick="setScMode('daily')">
            📅 Harian
          </button>
          <button id="sc-mode-weekly"  class="sc-mode-btn" onclick="setScMode('weekly')">
            📅 Mingguan
          </button>
          <button id="sc-mode-monthly" class="sc-mode-btn" onclick="setScMode('monthly')">
            📅 Bulanan
          </button>
        </div>

        <div id="sc-date-wrap" style="margin-top:10px;">
          <label>Tanggal Mulai</label>
          <input type="date" id="sc-from" onchange="scRecalcTo()">
          <div id="sc-range-preview" style="margin-top:6px;padding:8px 12px;
               background:var(--lteal);border-radius:8px;font-size:.85rem;
               color:var(--teal);font-weight:600;"></div>
        </div>

        <div class="btn-row mt14">
          <button class="btn btn-primary" onclick="saveSchedule()">💾 Simpan Jadwal</button>
          <button class="btn btn-outline" id="btn-runnow" onclick="runNow()">▶ Jalankan Sekarang</button>
        </div>

        <div style="margin-top:14px;border-top:1px solid #e2e8f0;padding-top:12px;">
          <div style="font-size:.82rem;font-weight:700;color:var(--gray);margin-bottom:8px;">
            Tindakan Otomatis Setelah Download
          </div>
          <div class="toggle-row" style="padding:6px 0;">
            <div>
              <div class="toggle-label" style="font-size:.88rem;">Auto Merge</div>
              <div class="toggle-sub">Langsung merge PDF setelah download selesai</div>
            </div>
            <label class="toggle">
              <input type="checkbox" id="sc-auto-merge" onchange="toggleAutoEmail()">
              <span class="slider"></span>
            </label>
          </div>
          <div class="toggle-row" id="sc-auto-email-row" style="padding:6px 0;opacity:0.4;">
            <div>
              <div class="toggle-label" style="font-size:.88rem;">Auto Email</div>
              <div class="toggle-sub">Kirim email setelah merge selesai (butuh Auto Merge aktif)</div>
            </div>
            <label class="toggle">
              <input type="checkbox" id="sc-auto-email" disabled>
              <span class="slider"></span>
            </label>
          </div>
          <div class="toggle-row" style="padding:6px 0;">
            <div>
              <div class="toggle-label" style="font-size:.88rem;">Notifikasi Android</div>
              <div class="toggle-sub">Tampilkan progress di notifikasi saat schedule berjalan</div>
            </div>
            <label class="toggle">
              <input type="checkbox" id="sc-notif" checked>
              <span class="slider"></span>
            </label>
          </div>
        </div>

        <div id="sc-save-status" class="mt8"></div>
      </div>

      <!-- Progress log schedule -->
      <div id="sc-log-wrap" class="hidden" style="margin-top:12px;">
        <div style="font-size:.82rem;font-weight:600;color:var(--gray);margin-bottom:6px;">
          Log Eksekusi
        </div>
        <div id="sc-log-box" style="background:#1e293b;color:#e2e8f0;border-radius:10px;
             padding:12px;font-family:monospace;font-size:.82rem;height:220px;
             overflow-y:auto;white-space:pre-wrap;line-height:1.55;"></div>
      </div>

      <div class="schedule-status" id="sc-status-box" style="margin-top:12px;">
        <div class="row"><span class="key">Status</span><span class="val" id="sc-status-val">—</span></div>
        <div class="row"><span class="key">Jam Terjadwal</span><span class="val" id="sc-time-val">—</span></div>
        <div class="row"><span class="key">Mode</span><span class="val" id="sc-mode-val">—</span></div>
        <div class="row"><span class="key">Terakhir Dijalankan</span><span class="val" id="sc-last-run">—</span></div>
        <div class="row"><span class="key">Hasil Terakhir</span><span class="val" id="sc-last-result">—</span></div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════ TAB: KONFIGURASI ══════════ -->
<div id="tab-config" class="section">
  <div class="card">
    <div class="card-header">📂 Folder</div>
    <div class="card-body">
      <label>Folder Sumber</label>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="text" id="c-source" placeholder="/sdcard/Documents" style="flex:1;">
        <button class="btn btn-outline" style="padding:10px 14px;white-space:nowrap;"
                onclick="openBrowser('c-source')">📁 Browse</button>
      </div>
      <label>Folder Output</label>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="text" id="c-output" placeholder="/sdcard/Documents/Hasil" style="flex:1;">
        <button class="btn btn-outline" style="padding:10px 14px;white-space:nowrap;"
                onclick="openBrowser('c-output')">📁 Browse</button>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-header">🔑 Akun Galva XEA</div>
    <div class="card-body">
      <label>Username</label>
      <input type="text" id="c-galva-user" placeholder="username XEA">
      <label>Password</label>
      <input type="password" id="c-galva-pass" placeholder="password XEA">
    </div>
  </div>
  <div class="card">
    <div class="card-header">🔄 Update Aplikasi</div>
    <div class="card-body">
      <div id="update-info" style="font-size:.85rem;color:#64748b;margin-bottom:10px;">
        Memeriksa versi...
      </div>
      <div class="btn-row">
        <button class="btn btn-outline" id="btn-check-update" onclick="checkUpdate()">
          🔄 Cek Update
        </button>
        <button class="btn btn-primary hidden" id="btn-apply-update" onclick="applyUpdate()">
          ⬇ Terapkan Update
        </button>
      </div>
      <div id="update-status" class="mt8"></div>
      <div id="update-log" class="hidden" style="margin-top:10px;background:#1e293b;
           color:#e2e8f0;border-radius:10px;padding:12px;font-family:monospace;
           font-size:.82rem;height:160px;overflow-y:auto;white-space:pre-wrap;line-height:1.55;">
      </div>
    </div>
  </div>
    <div class="card-body">
      <label>Email Pengirim</label>
      <input type="email" id="c-sender" placeholder="emailanda@gmail.com">
      <label>App Password (16 karakter)</label>
      <input type="password" id="c-password" placeholder="xxxx xxxx xxxx xxxx">
      <label>Penerima TO (pisah koma)</label>
      <input type="text" id="c-to">
      <label>CC (opsional)</label>
      <input type="text" id="c-cc">
      <label>BCC (opsional)</label>
      <input type="text" id="c-bcc">
      <div class="btn-row mt14">
        <button class="btn btn-primary" onclick="saveConfig()">💾 Simpan Konfigurasi</button>
      </div>
      <div id="config-status" class="mt8"></div>
    </div>
  </div>
</div>

<!-- ══════════ FOLDER BROWSER MODAL ══════════ -->
<div id="fb-overlay" class="hidden" style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;"
     onclick="closeBrowser()"></div>
<div id="fb-modal" class="hidden" style="position:fixed;bottom:0;left:0;right:0;
     background:#fff;border-radius:16px 16px 0 0;z-index:201;max-height:75vh;
     display:flex;flex-direction:column;box-shadow:0 -4px 24px rgba(0,0,0,.15);">
  <div style="background:#1a3c5e;padding:12px 16px;border-radius:16px 16px 0 0;
              display:flex;align-items:center;justify-content:space-between;">
    <span style="color:#67e8f9;font-weight:700;font-size:.95rem;">📁 Pilih Folder</span>
    <button onclick="closeBrowser()" style="background:none;border:none;color:#94a3b8;
            font-size:1.3rem;cursor:pointer;padding:0 4px;">✕</button>
  </div>
  <div id="fb-path-bar" style="padding:8px 14px;background:#f0f4f8;font-size:.82rem;
       color:#0891b2;font-family:monospace;word-break:break-all;border-bottom:1px solid #e2e8f0;"></div>
  <div id="fb-list" style="overflow-y:auto;flex:1;padding:4px 0;"></div>
  <div style="padding:12px 16px;border-top:1px solid #e2e8f0;display:flex;gap:10px;">
    <button class="btn btn-success" style="flex:1;justify-content:center;"
            onclick="selectCurrentFolder()">✓ Pilih Folder Ini</button>
    <button class="btn btn-outline" onclick="fbGoUp()">⬆ Naik</button>
  </div>
</div>

<script>
let currentResult = null;
let fbTargetInput = null;
let fbCurrentPath = '/sdcard';

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', function() {
    const name = this.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    this.classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
    if (name === 'config')   { loadConfig(); checkUpdate(); }
    if (name === 'schedule') loadSchedule();
    if (name === 'run') fetch('/api/config').then(r=>r.json()).then(cfg=>updateCfgInfo(cfg));
  });
});

window.onload = function() {
  const today    = new Date().toISOString().split('T')[0];
  const firstDay = new Date(new Date().getFullYear(), new Date().getMonth(), 1)
                   .toISOString().split('T')[0];
  document.getElementById('dl-from').value = firstDay;
  document.getElementById('dl-to').value   = today;
  fetch('/api/config').then(r=>r.json()).then(cfg => updateCfgInfo(cfg));
};

// ── Folder Browser ───────────────────────────────────────────
function openBrowser(inputId) {
  fbTargetInput = inputId;
  const current = document.getElementById(inputId).value.trim() || '/sdcard';
  fbCurrentPath = current;
  document.getElementById('fb-overlay').classList.remove('hidden');
  document.getElementById('fb-modal').classList.remove('hidden');
  fbLoad(fbCurrentPath);
}

function closeBrowser() {
  document.getElementById('fb-overlay').classList.add('hidden');
  document.getElementById('fb-modal').classList.add('hidden');
}

function selectCurrentFolder() {
  if (fbTargetInput) {
    document.getElementById(fbTargetInput).value = fbCurrentPath;
  }
  closeBrowser();
}

function fbGoUp() {
  const parts = fbCurrentPath.split('/').filter(Boolean);
  if (parts.length <= 1) { fbLoad('/'); return; }
  parts.pop();
  fbLoad('/' + parts.join('/'));
}

function fbLoad(path) {
  fbCurrentPath = path;
  document.getElementById('fb-path-bar').textContent = path;
  document.getElementById('fb-list').innerHTML =
    '<div style="padding:16px;text-align:center;color:#64748b;font-size:.85rem;">Memuat...</div>';

  fetch('/api/browse?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        document.getElementById('fb-list').innerHTML =
          `<div style="padding:16px;color:#dc2626;font-size:.85rem;">⚠ ${data.error}</div>`;
        return;
      }
      const items = data.items || [];
      if (items.length === 0) {
        document.getElementById('fb-list').innerHTML =
          '<div style="padding:16px;text-align:center;color:#64748b;font-size:.85rem;">Folder kosong</div>';
        return;
      }
      document.getElementById('fb-list').innerHTML = items.map(item =>
        `<div onclick="fbLoad('${item.path.replace(/'/g,"\\\'")}')"
              style="padding:12px 16px;display:flex;align-items:center;gap:12px;
                     border-bottom:1px solid #f0f4f8;cursor:pointer;
                     background:${item.type==='dir'?'#fff':'#f8fafc'};"
              onmousedown="this.style.background='#e0f7ff'"
              onmouseup="this.style.background=''">
           <span style="font-size:1.2rem;">${item.type==='dir'?'📁':'📄'}</span>
           <span style="font-size:.88rem;color:${item.type==='dir'?'#0f172a':'#64748b'};
                  font-weight:${item.type==='dir'?'600':'400'};">${item.name}</span>
         </div>`
      ).join('');
    })
    .catch(e => {
      document.getElementById('fb-list').innerHTML =
        `<div style="padding:16px;color:#dc2626;font-size:.85rem;">Error: ${e}</div>`;
    });
}

function loadConfig() {
  fetch('/api/config').then(r=>r.json()).then(cfg => {
    document.getElementById('c-source').value     = cfg.source_dir || '';
    document.getElementById('c-output').value     = cfg.output_dir || '';
    document.getElementById('c-sender').value     = cfg.sender_email || '';
    document.getElementById('c-password').value   = cfg.sender_password || '';
    document.getElementById('c-to').value         = (cfg.to||[]).join(', ');
    document.getElementById('c-cc').value         = (cfg.cc||[]).join(', ');
    document.getElementById('c-bcc').value        = (cfg.bcc||[]).join(', ');
    document.getElementById('c-galva-user').value = cfg.galva_username || '';
    document.getElementById('c-galva-pass').value = cfg.galva_password || '';
    updateCfgInfo(cfg);
  });
}

function saveConfig() {
  const split = s => s.split(',').map(x=>x.trim()).filter(Boolean);
  const cfg = {
    source_dir: document.getElementById('c-source').value.trim(),
    output_dir: document.getElementById('c-output').value.trim(),
    sender_email: document.getElementById('c-sender').value.trim(),
    sender_password: document.getElementById('c-password').value.trim(),
    to: split(document.getElementById('c-to').value),
    cc: split(document.getElementById('c-cc').value),
    bcc: split(document.getElementById('c-bcc').value),
    galva_username: document.getElementById('c-galva-user').value.trim(),
    galva_password: document.getElementById('c-galva-pass').value.trim(),
  };
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(cfg)}).then(r=>r.json()).then(r=>{
    const el=document.getElementById('config-status');
    el.innerHTML=r.ok?'<div class="alert alert-success">✓ Konfigurasi disimpan.</div>'
                     :'<div class="alert alert-error">Gagal menyimpan.</div>';
    if(r.ok) updateCfgInfo(cfg);
    setTimeout(()=>el.innerHTML='',3000);
  });
}

function updateCfgInfo(cfg) {
  const el=document.getElementById('cfg-info-body');
  if(!el) return;
  el.innerHTML=
    '<b>Sumber:</b> ' + (cfg.source_dir||'-') + '<br>'+
    '<b>Output:</b> ' + (cfg.output_dir||'-') + '<br>'+
    '<b>Email:</b> ' + (cfg.sender_email||'<span style="color:#ea580c">Belum diset</span>') + '<br>'+
    '<b>Galva User:</b> ' + (cfg.galva_username||'<span style="color:#ea580c">Belum diset</span>');
}

// ── Update ───────────────────────────────────────────────────
function checkUpdate() {
  const btn = document.getElementById('btn-check-update');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="border-color:#0891b2;border-top-color:transparent;"></span> Mengecek...';
  document.getElementById('btn-apply-update').classList.add('hidden');
  document.getElementById('update-status').innerHTML = '';
  document.getElementById('update-log').classList.add('hidden');

  fetch('/api/update/check').then(r=>r.json()).then(r=>{
    btn.disabled = false;
    btn.innerHTML = '🔄 Cek Update';
    const info = document.getElementById('update-info');
    if (r.error) {
      info.innerHTML = '<span style="color:#dc2626">✗ ' + r.error + '</span>';
      return;
    }
    if (r.up_to_date) {
      info.innerHTML =
        '<span style="color:#059669">✓ Sudah versi terbaru</span><br>' +
        '<span style="font-size:.8rem;">Versi: <b>' + r.local + '</b> • ' + (r.last_update||'-') + '</span>';
    } else {
      info.innerHTML =
        '<span style="color:#ea580c">⬆ Ada update tersedia!</span><br>' +
        '<span style="font-size:.8rem;">Lokal: <b>' + r.local + '</b>  →  GitHub: <b>' + r.remote + '</b></span>';
      if (r.changed_files && r.changed_files.length > 0) {
        info.innerHTML += '<br><span style="font-size:.78rem;color:#64748b;">File berubah: ' +
          r.changed_files.join(', ') + '</span>';
      }
      document.getElementById('btn-apply-update').classList.remove('hidden');
    }
  }).catch(e=>{
    btn.disabled = false;
    btn.innerHTML = '🔄 Cek Update';
    document.getElementById('update-info').innerHTML =
      '<span style="color:#dc2626">✗ Gagal: ' + e + '</span>';
  });
}

function applyUpdate() {
  if (!confirm('Terapkan update sekarang?\n\nAplikasi akan restart otomatis setelah update.')) return;
  const btn = document.getElementById('btn-apply-update');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Memperbarui...';
  document.getElementById('update-log').classList.remove('hidden');
  document.getElementById('update-log').textContent = '';

  const es = new EventSource('/api/update/apply');
  es.onmessage = function(e) {
    const d = JSON.parse(e.data);
    const box = document.getElementById('update-log');
    if (d.line !== undefined) {
      box.textContent += d.line + '\n';
      box.scrollTop = box.scrollHeight;
    }
    if (d.done) {
      es.close();
      btn.disabled = false;
      btn.innerHTML = '⬇ Terapkan Update';
      if (d.success) {
        document.getElementById('update-status').innerHTML =
          '<div class="alert alert-success">✓ Update berhasil! Aplikasi akan restart...</div>';
        setTimeout(()=>{ location.reload(); }, 4000);
      } else {
        document.getElementById('update-status').innerHTML =
          '<div class="alert alert-error">✗ Update gagal. Lihat log di atas.</div>';
      }
    }
  };
  es.onerror = function() {
    es.close();
    btn.disabled = false;
    btn.innerHTML = '⬇ Terapkan Update';
  };
}

function loadSchedule() {
  fetch('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('sc-enabled').checked    = cfg.schedule_enabled||false;
    document.getElementById('sc-time').value         = cfg.schedule_time||'06:00';
    document.getElementById('sc-auto-merge').checked = cfg.schedule_auto_merge||false;
    document.getElementById('sc-auto-email').checked = cfg.schedule_auto_email||false;
    document.getElementById('sc-notif').checked      = cfg.schedule_notif !== false;
    toggleAutoEmail();
    const mode = cfg.schedule_mode || 'daily';
    setScMode(mode, false);
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('sc-from').value = cfg.schedule_from || today;
    scRecalcTo();
    toggleScheduleOptions();
    const modeLabel = {daily:'Harian', weekly:'Mingguan', monthly:'Bulanan'};
    document.getElementById('sc-status-val').textContent =
      cfg.schedule_enabled ? '✅ Aktif' : '⛔ Nonaktif';
    document.getElementById('sc-time-val').textContent =
      cfg.schedule_enabled ? (cfg.schedule_time||'-') : '-';
    document.getElementById('sc-mode-val').textContent  = modeLabel[mode]||mode;
    document.getElementById('sc-last-run').textContent    = cfg.schedule_last_run||'Belum pernah';
    document.getElementById('sc-last-result').textContent = cfg.schedule_last_result||'-';
  });
}

function toggleAutoEmail() {
  const autoMerge = document.getElementById('sc-auto-merge').checked;
  const row   = document.getElementById('sc-auto-email-row');
  const input = document.getElementById('sc-auto-email');
  row.style.opacity = autoMerge ? '1' : '0.4';
  input.disabled = !autoMerge;
  if (!autoMerge) input.checked = false;
}

let _scMode = 'daily';

function setScMode(mode, recalc=true) {
  _scMode = mode;
  document.querySelectorAll('.sc-mode-btn').forEach(b => b.classList.remove('sc-mode-active'));
  document.getElementById('sc-mode-' + mode).classList.add('sc-mode-active');
  // Harian: sembunyikan date picker (tidak perlu pilih)
  const wrap = document.getElementById('sc-date-wrap');
  if (mode === 'daily') {
    wrap.classList.add('hidden');
  } else {
    wrap.classList.remove('hidden');
    if (recalc) scRecalcTo();
  }
}

function scRecalcTo() {
  const fromVal = document.getElementById('sc-from').value;
  if (!fromVal) return;
  const from = new Date(fromVal);
  let to, label;

  if (_scMode === 'weekly') {
    to = new Date(from);
    to.setDate(to.getDate() + 6);
    label = `${fmtDate(from)} — ${fmtDate(to)}  (7 hari)`;
  } else if (_scMode === 'monthly') {
    // Dari tanggal 1 bulan yang dipilih sampai akhir bulan
    const y = from.getFullYear(), m = from.getMonth();
    from.setDate(1);
    document.getElementById('sc-from').value = toIso(from);
    to = new Date(y, m+1, 0); // hari terakhir bulan
    label = `${fmtDate(from)} — ${fmtDate(to)}  (${to.getDate()} hari)`;
  }
  const preview = document.getElementById('sc-range-preview');
  if (preview) preview.textContent = '📅 Rentang: ' + label;
  // Simpan to ke hidden field
  if (!document.getElementById('sc-to-hidden')) {
    const inp = document.createElement('input');
    inp.type='hidden'; inp.id='sc-to-hidden';
    document.getElementById('sc-options').appendChild(inp);
  }
  document.getElementById('sc-to-hidden').value = toIso(to);
}

function fmtDate(d) {
  return d.toLocaleDateString('id-ID', {day:'2-digit',month:'short',year:'numeric'});
}
function toIso(d) {
  return d.toISOString().split('T')[0];
}

function toggleScheduleOptions() {
  document.getElementById('sc-options').style.opacity =
    document.getElementById('sc-enabled').checked ? '1' : '0.5';
}

function saveSchedule() {
  let fromVal = document.getElementById('sc-from').value;
  let toVal   = (_scMode === 'daily')
    ? fromVal
    : (document.getElementById('sc-to-hidden')?.value || fromVal);
  const cfg = {
    schedule_enabled   : document.getElementById('sc-enabled').checked,
    schedule_time      : document.getElementById('sc-time').value,
    schedule_mode      : _scMode,
    schedule_from      : fromVal,
    schedule_to        : toVal,
    schedule_auto_merge: document.getElementById('sc-auto-merge').checked,
    schedule_auto_email: document.getElementById('sc-auto-email').checked,
    schedule_notif     : document.getElementById('sc-notif').checked,
  };
  fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(cfg)}).then(r=>r.json()).then(r=>{
    const el=document.getElementById('sc-save-status');
    el.innerHTML=r.ok?'<div class="alert alert-success">✓ Jadwal disimpan.</div>'
                     :'<div class="alert alert-error">Gagal: '+(r.error||'')+'</div>';
    setTimeout(()=>{el.innerHTML='';loadSchedule();},2000);
  });
}

function appendScLog(msg, cls='') {
  const box = document.getElementById('sc-log-box');
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = msg + '\n';
  box.appendChild(span);
  box.scrollTop = box.scrollHeight;
}

function runNow() {
  const btn = document.getElementById('btn-runnow');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Berjalan...';

  const modeLabel = {daily:'Harian', weekly:'Mingguan', monthly:'Bulanan'};

  // Hitung rentang sesuai mode
  let fromVal = document.getElementById('sc-from') ?
                document.getElementById('sc-from').value : '';
  if (!fromVal) fromVal = new Date().toISOString().split('T')[0];
  let toVal;
  if (_scMode === 'daily') {
    fromVal = toVal = new Date().toISOString().split('T')[0];
  } else {
    toVal = document.getElementById('sc-to-hidden')?.value || fromVal;
  }

  // Tampilkan log box
  document.getElementById('sc-log-wrap').classList.remove('hidden');
  document.getElementById('sc-log-box').innerHTML = '';
  appendScLog('▶ Menjalankan schedule (' + modeLabel[_scMode] + ')...', 'log-info');
  appendScLog('📅 Rentang: ' + fromVal + ' → ' + toVal, 'log-dim');
  appendScLog('');

  const es = new EventSource('/api/download?date_from=' + fromVal + '&date_to=' + toVal);
  es.onmessage = function(e) {
    const ev = JSON.parse(e.data); const t = ev.type; const d = ev.data;
    if      (t==='dl_login')     appendScLog('🔑 Login sebagai ' + d.username + '...', 'log-info');
    else if (t==='dl_login_ok')  appendScLog('✓ Login berhasil', 'log-ok');
    else if (t==='dl_fetch')     appendScLog('📋 Mengambil daftar order...', 'log-info');
    else if (t==='dl_summary') {
      appendScLog('Total   : ' + d.total + ' order', 'log-info');
      appendScLog('Skip    : ' + (d.skip_status + d.skip_date) + '  |  Diproses: ' + d.qualified, 'log-dim');
      appendScLog('');
    }
    else if (t==='dl_order')     appendScLog('[' + d.number + '] ' + d.type + ' | ' + d.date, 'log-info');
    else if (t==='dl_file_ok')   appendScLog('  ✓ ' + d.filename, 'log-ok');
    else if (t==='dl_no_doc')    appendScLog('  → Tidak ada dokumen', 'log-warn');
    else if (t==='dl_error') {
      appendScLog('ERROR: ' + d.msg, 'log-fail');
      es.close(); btn.disabled=false; btn.innerHTML='▶ Jalankan Sekarang';
    }
    else if (t==='dl_done') {
      appendScLog('');
      appendScLog('══ Selesai: ' + d.total_saved + ' file diunduh ══', 'log-ok');
      es.close(); btn.disabled=false; btn.innerHTML='▶ Jalankan Sekarang';
      setTimeout(()=>loadSchedule(), 500);
    }
  };
  es.onerror = function() {
    appendScLog('Koneksi terputus.', 'log-fail');
    es.close(); btn.disabled=false; btn.innerHTML='▶ Jalankan Sekarang';
  };
}

function appendDlLog(msg,cls=''){
  const box=document.getElementById('dl-log-box');
  const span=document.createElement('span');
  if(cls) span.className=cls;
  span.textContent=msg+'\n';
  box.appendChild(span); box.scrollTop=box.scrollHeight;
}

function clearDlLog(){
  document.getElementById('dl-log-box').innerHTML='';
  document.getElementById('dl-result-section').classList.add('hidden');
}

function startDownload(){
  const from=document.getElementById('dl-from').value;
  const to=document.getElementById('dl-to').value;
  if(!from||!to){alert('Isi kedua tanggal terlebih dahulu.');return;}
  const btn=document.getElementById('btn-dl');
  btn.disabled=true; btn.innerHTML='<span class="spinner"></span> Mengunduh...';
  document.getElementById('dl-result-section').classList.add('hidden');
  document.getElementById('dl-log-box').innerHTML='';
  document.getElementById('dl-progress-wrap').classList.remove('hidden');
  document.getElementById('dl-progress-bar').style.width='5%';

  const es=new EventSource(`/api/download?date_from=${from}&date_to=${to}`);
  es.onmessage=function(e){
    const ev=JSON.parse(e.data);const t=ev.type;const d=ev.data;
    if(t==='dl_login')       appendDlLog(`🔑 Login sebagai ${d.username}...`,'log-info');
    else if(t==='dl_login_ok')    appendDlLog('✓ Login berhasil','log-ok');
    else if(t==='dl_fetch')       appendDlLog('📋 Mengambil daftar order...','log-info');
    else if(t==='dl_summary'){
      appendDlLog('');
      appendDlLog(`Total   : ${d.total} order`,'log-info');
      appendDlLog(`Skip    : ${d.skip_status} (status) + ${d.skip_date} (tanggal)`,'log-dim');
      appendDlLog(`Diproses: ${d.qualified} order`,'log-info');
      appendDlLog(`Rentang : ${d.date_from} → ${d.date_to}`,'log-info');
      appendDlLog('');
      document.getElementById('dl-progress-bar').style.width='30%';
    }
    else if(t==='dl_skip_dup')   appendDlLog(`  ⏭ ${d.number} — skip (${d.reason})`,'log-dim');
    else if(t==='dl_order')      appendDlLog(`[${d.number}] ${d.type} | ${d.status} | ${d.date}\n  ${d.customer}`,'log-info');
    else if(t==='dl_file_ok')    appendDlLog(`  ✓ ${d.filename}`,'log-ok');
    else if(t==='dl_no_doc')     appendDlLog(`  → Tidak ada dokumen tersedia`,'log-warn');
    else if(t==='dl_order_err')  appendDlLog(`  ✗ ${d.number}: ${d.msg}`,'log-fail');
    else if(t==='dl_error'){
      appendDlLog(`ERROR: ${d.msg}`,'log-fail');
      es.close();btn.disabled=false;btn.innerHTML='📥 Mulai Download';
      document.getElementById('dl-progress-wrap').classList.add('hidden');
    }
    else if(t==='dl_done'){
      document.getElementById('dl-progress-bar').style.width='100%';
      const skipInfo = d.skipped_dup > 0 ? ` (${d.skipped_dup} skip duplikat)` : '';
      appendDlLog('');appendDlLog(`══ Selesai: ${d.total_saved} file diunduh${skipInfo} ══`,'log-ok');
      es.close();btn.disabled=false;btn.innerHTML='📥 Mulai Download';
      document.getElementById('dl-result-section').classList.remove('hidden');
      const skipMsg = d.skipped_dup > 0
        ? `<br><span style="font-size:.8rem;color:#64748b;">⏭ ${d.skipped_dup} order dilewati karena sudah ada</span>`
        : '';
      document.getElementById('dl-result-stats').innerHTML=
        `<div class="alert alert-${d.total_saved>0?'success':'warn'}">` +
        `${d.total_saved>0?'✓':'⚠'} ${d.total_saved} file diunduh dari ${d.qualified} order.${skipMsg}</div>`;
      document.getElementById('dl-merge-btn-row').classList.remove('hidden');
      document.getElementById('dl-merge-status').innerHTML='';
      setTimeout(()=>document.getElementById('dl-progress-wrap').classList.add('hidden'),1500);
    }
  };
  es.onerror=function(){
    appendDlLog('Koneksi terputus.','log-fail');
    es.close();btn.disabled=false;btn.innerHTML='📥 Mulai Download';
  };
}

function startMergeFromDownload(){
  document.getElementById('dl-merge-btn-row').classList.add('hidden');
  document.getElementById('dl-merge-status').innerHTML=
    '<div class="alert alert-info">Berpindah ke tab Merge...</div>';
  document.querySelector('[data-tab="run"]').click();
  setTimeout(()=>startMerge(true),400);
}

function skipMerge(){
  document.getElementById('dl-merge-btn-row').classList.add('hidden');
  document.getElementById('dl-merge-status').innerHTML=
    '<div class="alert alert-warn">Proses merge dilewati.</div>';
}

function appendLog(msg,cls=''){
  const box=document.getElementById('log-box');
  const span=document.createElement('span');
  if(cls) span.className=cls;
  span.textContent=msg+'\n';
  box.appendChild(span);box.scrollTop=box.scrollHeight;
}

function clearLog(){
  document.getElementById('log-box').innerHTML='';
  document.getElementById('result-section').classList.add('hidden');
}

function startMerge(newFormat){
  const btn=document.getElementById('btn-run');
  btn.disabled=true;btn.innerHTML='<span class="spinner"></span> Memproses...';
  document.getElementById('result-section').classList.add('hidden');
  document.getElementById('log-box').innerHTML='';
  document.getElementById('progress-wrap').classList.remove('hidden');
  document.getElementById('progress-bar').style.width='5%';

  let totalPairs=0;let done=0;
  const es=new EventSource(`/api/run?new_format=${newFormat?'1':'0'}`);
  es.onmessage=function(e){
    const ev=JSON.parse(e.data);const t=ev.type;const d=ev.data;
    if(t==='scan')         appendLog(`🔍 ${d.total} file PDF ditemukan`,'log-info');
    else if(t==='classify')  appendLog(`📂 STBA: ${d.stba}  STAT: ${d.stats}  Unknown: ${d.unknown}`,'log-info');
    else if(t==='pair_found'){
      totalPairs=d.pairs;
      appendLog(`🔗 Pasangan: ${d.pairs}  |  Hanya STBA: ${d.only_stba}  |  Hanya STAT: ${d.only_stats}`,'log-info');
      appendLog('');
    }
    else if(t==='merge_ok'){
      done++;
      appendLog(`✓  [${d.key}]  ${d.nama}  →  ${d.folder}/`,'log-ok');
      if(totalPairs>0)
        document.getElementById('progress-bar').style.width=
          Math.min(95,Math.round(done/totalPairs*90)+5)+'%';
    }
    else if(t==='merge_fail')  appendLog(`✗  [${d.key}] Gagal merge`,'log-fail');
    else if(t==='file_kosong') appendLog(`⚠  ${d.name}  →  File Kosong/`,'log-warn');
    else if(t==='arsip')       appendLog(`\n📦 ${d.jumlah} file diarsip ke [${d.folder}]`,'log-info');
    else if(t==='txt_saved')   appendLog(`📝 ${d.path.split('/').pop()} disimpan`,'log-dim');
    else if(t==='estimasi')    appendLog(`💰 estimasi_biaya.txt disimpan`,'log-info');
    else if(t==='done'){
      document.getElementById('progress-bar').style.width='100%';
      appendLog('\n══ Selesai ══','log-info');
      es.close();btn.disabled=false;btn.innerHTML='▶ Mulai Merge';
      currentResult=d;showResult(d);
      setTimeout(()=>document.getElementById('progress-wrap').classList.add('hidden'),1500);
    }
    else if(t==='error'){
      appendLog(`ERROR: ${d.msg}`,'log-fail');
      es.close();btn.disabled=false;btn.innerHTML='▶ Mulai Merge';
    }
  };
  es.onerror=function(){
    appendLog('Koneksi terputus.','log-fail');
    es.close();btn.disabled=false;btn.innerHTML='▶ Mulai Merge';
  };
}

function showResult(r){
  document.getElementById('result-section').classList.remove('hidden');
  const colors={green:'#059669',red:'#dc2626',orange:'#ea580c',teal:'#0891b2'};
  const stats=[['✓ Berhasil',r.success+' pasang','green'],['✗ Gagal',r.failed+' pasang','red'],
    ['File Kosong',r.file_kosong+' file','orange'],['Diarsip ke',r.folder_bulan||'-','teal']];
  document.getElementById('stats-grid').innerHTML=stats.map(([lbl,val,col])=>
    `<div class="stat" style="border-left-color:${colors[col]}">
       <div class="stat-num" style="color:${colors[col]}">${val}</div>
       <div class="stat-lbl">${lbl}</div></div>`).join('');
  const tbody=document.getElementById('summary-body');
  tbody.innerHTML='';
  const summary=r.summary||{};
  for(const[folder,entries] of Object.entries(summary).sort()){
    tbody.innerHTML+=`<tr><td><span class="badge badge-teal">${folder}</span></td>
      <td>${entries.length} file</td></tr>`;
  }
  if(!r.summary||Object.keys(r.summary).length===0){
    document.getElementById('email-section').classList.add('hidden');return;
  }
  let fileHtml='';
  for(const[folder,entries] of Object.entries(r.summary).sort()){
    fileHtml+=`<div class="bold teal" style="margin-top:8px;font-size:.85rem">${folder} — ${entries.length} file</div>`;
    entries.forEach(([key,nama])=>{
      fileHtml+=`<div class="file-item">📄 ${key}.pdf  <span class="dim">— ${nama}</span></div>`;
    });
  }
  document.getElementById('email-file-list').innerHTML=
    `<div class="alert alert-info">File berikut akan dikirim sebagai attachment:</div>
     <div class="file-list">${fileHtml}</div>`;
  document.getElementById('email-status').innerHTML='';
  document.getElementById('email-btn-row').classList.remove('hidden');
}

function sendEmails(){
  document.getElementById('email-btn-row').classList.add('hidden');
  document.getElementById('email-status').innerHTML=
    '<div class="alert alert-info"><span class="spinner"></span> Mengirim email...</div>';
  fetch('/api/send-email',{method:'POST'}).then(r=>r.json()).then(r=>{
    document.getElementById('email-status').innerHTML=r.ok>0
      ?`<div class="alert alert-success">✓ ${r.ok} email terkirim.</div>
        ${r.fail>0?`<div class="alert alert-warn">✗ ${r.fail} gagal.</div>`:''}
        ${r.detail.map(([t,ok,msg])=>`<div class="file-item">${ok?'✓':'✗'} [${t}] ${msg}</div>`).join('')}`
      :`<div class="alert alert-error">✗ Gagal kirim email.</div>`;
  });
}

function cancelEmail(){
  document.getElementById('email-btn-row').classList.add('hidden');
  document.getElementById('email-status').innerHTML=
    '<div class="alert alert-warn">Pengiriman email dilewati.</div>';
}
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def post_config():
    try:
        cfg=load_config();cfg.update(request.get_json());save_config(cfg)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/schedule", methods=["POST"])
def post_schedule():
    try:
        data=request.get_json();cfg=load_config()
        cfg["schedule_enabled"]   =data.get("schedule_enabled",False)
        cfg["schedule_time"]      =data.get("schedule_time","06:00")
        cfg["schedule_mode"]      =data.get("schedule_mode","daily")
        cfg["schedule_from"]      =data.get("schedule_from","")
        cfg["schedule_to"]        =data.get("schedule_to","")
        cfg["schedule_auto_merge"]=data.get("schedule_auto_merge",False)
        cfg["schedule_auto_email"]=data.get("schedule_auto_email",False)
        cfg["schedule_notif"]     =data.get("schedule_notif",True)
        save_config(cfg);apply_schedule(cfg)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/schedule/run-now", methods=["POST"])
def run_schedule_now():
    try:
        scheduled_download_job()
        cfg=load_config()
        return jsonify({"ok":True,"result":cfg.get("schedule_last_result","Selesai")})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/download")
def api_download():
    date_from=request.args.get("date_from","")
    date_to  =request.args.get("date_to","")
    cfg      =load_config()
    username =cfg.get("galva_username","")
    password =cfg.get("galva_password","")
    save_dir =cfg.get("source_dir","/sdcard/Documents")
    q        =queue.Queue()

    def fix(obj):
        if isinstance(obj,Path): return str(obj)
        if isinstance(obj,dict): return {k:fix(v) for k,v in obj.items()}
        if isinstance(obj,list): return [fix(v) for v in obj]
        if isinstance(obj,tuple): return [fix(v) for v in obj]
        return obj

    def cb(event,data): q.put({"type":event,"data":fix(data)})

    def worker():
        try:
            run_download(date_from,date_to,username,password,save_dir,cb)
        except Exception as e:
            q.put({"type":"dl_error","data":{"msg":str(e)}})
        finally:
            q.put(None)

    threading.Thread(target=worker,daemon=True).start()

    def generate():
        while True:
            item=q.get()
            if item is None: break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/run")
def api_run():
    cfg       =load_config()
    new_format=request.args.get("new_format","0")=="1"
    q         =queue.Queue()

    def fix(obj):
        if isinstance(obj,Path): return str(obj)
        if isinstance(obj,dict): return {k:fix(v) for k,v in obj.items()}
        if isinstance(obj,list): return [fix(v) for v in obj]
        if isinstance(obj,tuple): return [fix(v) for v in obj]
        return obj

    def cb(event,data): q.put({"type":event,"data":fix(data)})

    def worker():
        try:
            result=run_merge(cfg["source_dir"],cfg["output_dir"],
                             cfg.get("digit_count",6),new_format,cb)
            _state["result"]=result
        except Exception as e:
            q.put({"type":"error","data":{"msg":str(e)}})
        finally:
            q.put(None)

    threading.Thread(target=worker,daemon=True).start()

    def generate():
        while True:
            item=q.get()
            if item is None: break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/send-email",methods=["POST"])
def api_send_email():
    result=_state.get("result")
    if not result or not result.get("summary"):
        return jsonify({"ok":0,"fail":0,"detail":[],"error":"Tidak ada hasil merge"})
    cfg=load_config()
    er=do_send_emails(result["summary"],cfg)
    return jsonify({"ok":er["ok"],"fail":er["fail"],"detail":er["detail"]})

@app.route("/api/update/check")
def api_update_check():
    import subprocess
    UPDATE_CFG = os.path.join(Path.home(), ".merge_pdf_update")
    if not os.path.exists(UPDATE_CFG):
        return jsonify({"error": "Konfigurasi update tidak ditemukan. Jalankan setup.sh dulu."})
    # Baca token dan info repo
    cfg_vals = {}
    with open(UPDATE_CFG) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                cfg_vals[k] = v.strip('"')
    token       = cfg_vals.get("TOKEN", "")
    repo_user   = cfg_vals.get("REPO_USER", "ShadowSoldiers")
    repo_name   = cfg_vals.get("REPO_NAME", "PDF-Merge-Tools")
    install_dir = cfg_vals.get("INSTALL_DIR", str(Path.home() / repo_name))
    repo_url    = f"https://{token}@github.com/{repo_user}/{repo_name}.git"

    try:
        # Fetch remote tanpa merge
        subprocess.run(
            ["git", "fetch", repo_url, "main", "--quiet"],
            cwd=install_dir, capture_output=True, timeout=15
        )
        local  = subprocess.run(["git", "rev-parse", "HEAD"],
                                 cwd=install_dir, capture_output=True).stdout.decode().strip()[:7]
        remote = subprocess.run(["git", "rev-parse", "FETCH_HEAD"],
                                 cwd=install_dir, capture_output=True).stdout.decode().strip()[:7]
        changed = []
        if local != remote:
            r = subprocess.run(["git", "diff", "--name-only", "HEAD", "FETCH_HEAD"],
                               cwd=install_dir, capture_output=True)
            changed = [f for f in r.stdout.decode().strip().split("\n") if f]
        # Last commit date
        last = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%d %b %Y %H:%M"],
            cwd=install_dir, capture_output=True).stdout.decode().strip()
        return jsonify({
            "up_to_date"   : local == remote,
            "local"        : local,
            "remote"       : remote,
            "changed_files": changed,
            "last_update"  : last,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/update/apply")
def api_update_apply():
    import subprocess
    UPDATE_CFG = os.path.join(Path.home(), ".merge_pdf_update")
    q = queue.Queue()

    def worker():
        try:
            if not os.path.exists(UPDATE_CFG):
                q.put({"line": "ERROR: setup.sh belum dijalankan."}); q.put({"done": True, "success": False}); return
            cfg_vals = {}
            with open(UPDATE_CFG) as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        cfg_vals[k] = v.strip('"')
            token       = cfg_vals.get("TOKEN", "")
            repo_user   = cfg_vals.get("REPO_USER", "ShadowSoldiers")
            repo_name   = cfg_vals.get("REPO_NAME", "PDF-Merge-Tools")
            install_dir = cfg_vals.get("INSTALL_DIR", str(Path.home() / repo_name))
            repo_url    = f"https://{token}@github.com/{repo_user}/{repo_name}.git"

            q.put({"line": "📦 Backup konfigurasi..."})
            config_file = str(Path.home() / "merge_pdf_config.json")
            if os.path.exists(config_file):
                import shutil
                shutil.copy(config_file, config_file + ".bak")
                q.put({"line": "✓ merge_pdf_config.json di-backup"})

            q.put({"line": "\n⬇ Mengunduh update dari GitHub..."})
            r = subprocess.run(
                ["git", "pull", repo_url, "main"],
                cwd=install_dir, capture_output=True, text=True, timeout=30
            )
            for line in (r.stdout + r.stderr).splitlines():
                if line.strip(): q.put({"line": line})

            if r.returncode == 0:
                new_ver = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                    cwd=install_dir, capture_output=True).stdout.decode().strip()
                q.put({"line": f"\n✓ Update berhasil! Versi baru: {new_ver}"})
                q.put({"line": "🔄 Restart dalam 3 detik..."})
                q.put({"done": True, "success": True})
                # Restart Flask
                import threading, time
                def restart():
                    time.sleep(3)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                threading.Thread(target=restart, daemon=True).start()
            else:
                q.put({"line": "\n✗ Update gagal."})
                q.put({"done": True, "success": False})
        except Exception as e:
            q.put({"line": f"ERROR: {e}"}); q.put({"done": True, "success": False})

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            item = q.get()
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("done"): break

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "/sdcard")
    try:
        p = Path(path)
        if not p.exists():
            return jsonify({"error": f"Path tidak ditemukan: {path}"})
        if not p.is_dir():
            p = p.parent
        items = []
        for child in sorted(p.iterdir()):
            if child.is_dir() and not child.name.startswith('.'):
                items.append({"name": child.name, "path": str(child), "type": "dir"})
        return jsonify({"path": str(p), "items": items})
    except PermissionError:
        return jsonify({"error": "Tidak ada izin akses folder ini"})
    except Exception as e:
        return jsonify({"error": str(e)})

# ─────────────────────────────────────────────────────────────
if __name__=="__main__":
    init_scheduler()
    print("="*54)
    print("  merge_pdf  —  Web GUI")
    print("="*54)
    print("  Buka di Chrome Android:")
    print("  ➜  http://localhost:5000")
    if not HAS_SCHEDULER:
        print("  ⚠  pip install apscheduler untuk fitur Schedule")
    print("="*54)
    app.run(host="0.0.0.0",port=5000,debug=False,threaded=True)
