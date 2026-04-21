#!/usr/bin/env python3
"""
merge_core.py — Logika inti, dipakai oleh TUI dan Web GUI.
Semua fungsi PDF, email, dan file management ada di sini.
"""

import re
import ssl
import shutil
import smtplib
import json
import os
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
# FILE KONFIGURASI (disimpan di HP agar persisten)
# ─────────────────────────────────────────────────────────────
CONFIG_FILE = str(Path.home() / "merge_pdf_config.json")

DEFAULT_CONFIG = {
    "source_dir"      : "/sdcard/Documents",
    "output_dir"      : "/sdcard/Documents/Hasil",
    "digit_count"     : 6,
    "xea_username"    : "",
    "xea_password"    : "",
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
    "schedule_enabled": False,
    "schedule_time"   : "08:00",
    "schedule_days"   : [1, 2, 3, 4, 5],
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
TAG_FIRST  = "STBA"
TAG_SECOND = "STATS"

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

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# ─────────────────────────────────────────────────────────────
# UTILITAS PDF
# ─────────────────────────────────────────────────────────────

def extract_key(filename: str, n: int) -> str:
    stem      = Path(filename).stem
    alnum     = re.sub(r"[^A-Za-z0-9]", "", stem)
    return alnum[-n:].upper() if len(alnum) >= n else None

def detect_tag(path: Path) -> str:
    name = path.stem.upper()
    if TAG_FIRST  in name: return "FIRST"
    if TAG_SECOND in name: return "SECOND"
    return None

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
    """Kembalikan (nama_pelanggan, tipe_layanan_raw, folder_name, serial_number)"""
    nama = "-"; tipe = "-"; serial = "-"
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
                if serial == "-":
                    m = re.search(
                        r"(?:Serial\s*(?:Number|No\.?)|No\.?\s*[Ss]erial|Nomor\s*[Ss]eri(?:al)?)\s*:\s*(.+)",
                        line, re.IGNORECASE)
                    if m: serial = m.group(1).strip()
                if nama != "-" and tipe != "-" and serial != "-":
                    break
            if nama != "-" and tipe != "-" and serial != "-":
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

    return nama, tipe, folder_name, serial

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
        for key, nama, serial in entries:
            f.write(f"{key} - {nama} [{serial}]\n")

def format_rupiah(angka: int) -> str:
    return "Rp {:>13,}".format(angka).replace(",", ".")

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

def nama_bulan_indonesia(dt: datetime) -> str:
    bulan = ["","Januari","Februari","Maret","April","Mei","Juni",
             "Juli","Agustus","September","Oktober","November","Desember"]
    return f"{bulan[dt.month]} {dt.year}"

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
    """Kirim 1 email. Kembalikan (ok: bool, pesan: str)"""
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
# FUNGSI UTAMA — run_merge()
# ─────────────────────────────────────────────────────────────

def run_merge(source_dir: str, output_dir: str,
              digit_count: int = 6, cb=None) -> dict:
    """
    Jalankan proses merge. cb(event, data) dipanggil untuk setiap kejadian.
    Event: 'scan', 'classify', 'pair_found', 'merge_ok', 'merge_fail',
           'file_kosong', 'arsip', 'txt_saved', 'ringkasan', 'done'
    Kembalikan dict hasil untuk dipakai GUI kirim email.
    """
    def emit(event, data):
        if cb: cb(event, data)

    out_root    = Path(output_dir)
    source_path = Path(source_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    # 1. Scan
    all_pdfs = find_pdfs(source_dir)
    emit("scan", {"total": len(all_pdfs), "source_dir": source_dir})

    # 2. Klasifikasi
    pool = {"FIRST": defaultdict(list), "SECOND": defaultdict(list)}
    unrecognized = []
    for pdf in all_pdfs:
        tag = detect_tag(pdf)
        key = extract_key(pdf.name, digit_count)
        if tag is None or key is None:
            unrecognized.append(pdf); continue
        pool[tag][key].append(pdf)
    emit("classify", {
        "stba" : sum(len(v) for v in pool["FIRST"].values()),
        "stats": sum(len(v) for v in pool["SECOND"].values()),
        "unknown": len(unrecognized),
    })

    # 3. Pasangan
    all_keys    = set(pool["FIRST"]) | set(pool["SECOND"])
    pairs_ok    = sorted(k for k in all_keys if pool["FIRST"].get(k) and pool["SECOND"].get(k))
    only_first  = sorted(k for k in all_keys if pool["FIRST"].get(k) and not pool["SECOND"].get(k))
    only_second = sorted(k for k in all_keys if pool["SECOND"].get(k) and not pool["FIRST"].get(k))
    emit("pair_found", {"pairs": len(pairs_ok),
                        "only_stba": len(only_first),
                        "only_stats": len(only_second)})

    # 4. Merge
    summary     = defaultdict(list)
    txt_entries = defaultdict(list)
    moved_pairs = []
    log_lines   = []
    success = failed = 0

    for key in pairs_ok:
        first_file  = sorted(pool["FIRST"][key])[0]
        second_file = sorted(pool["SECOND"][key])[0]
        nama, tipe, folder_name, serial = extract_stba_info(first_file)

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
            summary[folder_name].append((key, nama, serial, output_file))
            txt_entries[folder_name].append((key, nama, serial))
            log_lines.append(f"[OK] {output_file}\n     STBA: {first_file}\n     STATS: {second_file}\n     Pelanggan: {nama}  Serial: {serial}  Tipe: {tipe}")
            emit("merge_ok", {"key": key, "nama": nama, "serial": serial,
                              "tipe": tipe, "folder": folder_name,
                              "output": str(output_file)})
        else:
            failed += 1
            log_lines.append(f"[GAGAL] kunci={key}")
            emit("merge_fail", {"key": key})

    # 5. File Kosong
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
                log_lines.append(f"[KOSONG] {src.name} → File Kosong/")
                emit("file_kosong", {"name": src.name})
            except Exception as e:
                emit("merge_fail", {"key": k, "reason": str(e)})

    # 6. Arsip bulan
    folder_bulan = ""
    if moved_pairs:
        folder_bulan, pindah_ok, pindah_gagal = pindah_file_mentah(source_dir, moved_pairs)
        log_lines.append(f"[ARSIP] {pindah_ok} file mentah dipindah ke [{folder_bulan}]")
        emit("arsip", {"folder": folder_bulan, "jumlah": pindah_ok})

    # 7. Simpan .txt
    for folder_name, entries in sorted(txt_entries.items()):
        tipe_folder = out_root / folder_name
        txt_path    = tipe_folder / f"daftar_pelanggan_{folder_name}.txt"
        save_note_txt(txt_path, entries)
        emit("txt_saved", {"path": str(txt_path)})

    # 8. Ringkasan total
    ringkasan_path = None
    if summary:
        ringkasan_path = save_ringkasan_total(out_root, summary, file_kosong_list)
        emit("ringkasan", {"path": str(ringkasan_path)})

    # 9. Log
    log_path = out_root / LOG_FILE
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write("MERGE LOG\n")
        lf.write(f"Sumber : {source_dir}\nOutput : {output_dir}\n\n")
        lf.write("\n".join(log_lines))

    result = {
        "success"        : success,
        "failed"         : failed,
        "file_kosong"    : len(file_kosong_list),
        "only_stats"     : len(only_second),
        "folder_bulan"   : folder_bulan,
        "summary"        : dict(summary),   # folder_name -> [(key,nama,path)]
        "ringkasan_path" : str(ringkasan_path) if ringkasan_path else "",
        "log_path"       : str(log_path),
        "output_dir"     : output_dir,
    }
    emit("done", result)
    return result

def do_send_emails(summary: dict, cfg: dict, cb=None) -> dict:
    """
    Kirim email untuk semua Tipe Layanan.
    Kembalikan {"ok": n, "fail": n, "detail": [(tipe, bool, msg)]}
    """
    def emit(ev, data):
        if cb: cb(ev, data)

    ok = fail = 0
    detail = []
    for tipe, entries in sorted(summary.items()):
        pdf_files  = [e[3] for e in entries]
        daftar_str = "\n".join(
            f"  {k} - {n}  |  SN: {s}" for k, n, s, _ in entries
        )
        success, msg = send_email_subfolder(tipe, pdf_files, daftar_str, cfg)
        detail.append((tipe, success, msg))
        emit("email_result", {"tipe": tipe, "ok": success, "msg": msg})
        if success: ok += 1
        else:       fail += 1
    return {"ok": ok, "fail": fail, "detail": detail}
