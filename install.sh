#!/data/data/com.termux/files/usr/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║       PDF Merge Tools — Auto Installer v2                   ║
# ║       PT Galva Technologies Tbk                             ║
# ║                                                             ║
# ║  Cara pakai (di Termux):                                    ║
# ║    curl -sSL https://raw.githubusercontent.com/             ║
# ║      NAMA_USER/NAMA_REPO/main/install.sh | bash             ║
# ╚══════════════════════════════════════════════════════════════╝

# ══════════════════════════════════════════════════════════════
# KONFIGURASI — Ganti sesuai repo GitHub
# ══════════════════════════════════════════════════════════════
GITHUB_RAW="https://raw.githubusercontent.com/ShadowSoldiers/xeatools/main"
INSTALL_DIR="$HOME/merge_pdf"
BOOT_DIR="$HOME/.termux/boot"
WIDGET_DIR="$HOME/.shortcuts"
CONFIG_FILE="$HOME/merge_pdf_config.json"

SCRIPTS=(
    "merge_core.py"
    "merge_tui.py"
    "merge_web.py"
    "galva_download.py"
)

# ══════════════════════════════════════════════════════════════
# WARNA & HELPER
# ══════════════════════════════════════════════════════════════
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
info() { echo -e "  ${CYAN}→${RESET}  $1"; }
fail() { echo -e "\n  ${RED}✗  GAGAL: $1${RESET}\n"; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}[$1]${RESET} $2"; }
sep()  { echo -e "${CYAN}══════════════════════════════════════════${RESET}"; }

# ══════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════
clear
sep
echo -e "  ${BOLD}${CYAN}PDF Merge Tools — Installer${RESET}"
echo -e "  PT Galva Technologies Tbk"
sep
echo ""
echo -e "  Installer ini akan:"
echo -e "  ${CYAN}•${RESET} Install Python & semua library"
echo -e "  ${CYAN}•${RESET} Download script dari GitHub"
echo -e "  ${CYAN}•${RESET} Minta akun Galva XEA & Gmail"
echo -e "  ${CYAN}•${RESET} Konfigurasi semua file otomatis"
echo -e "  ${CYAN}•${RESET} Setup shortcut & auto-start"
echo ""
sep
echo ""

# ══════════════════════════════════════════════════════════════
# STEP 1: Cek Termux
# ══════════════════════════════════════════════════════════════
step "1/7" "Cek environment"

if [ ! -d "/data/data/com.termux" ]; then
    warn "Tidak terdeteksi sebagai Termux — melanjutkan (mode test)"
else
    ok "Berjalan di dalam Termux"
fi

# ══════════════════════════════════════════════════════════════
# STEP 2: Storage permission
# ══════════════════════════════════════════════════════════════
step "2/7" "Izin akses storage"

if [ -d "$HOME/storage" ]; then
    ok "Storage sudah diizinkan"
else
    info "Meminta izin storage — ketuk ALLOW pada popup..."
    termux-setup-storage
    sleep 4
    if [ -d "$HOME/storage" ]; then
        ok "Izin storage diberikan"
    else
        warn "Izin storage belum diberikan — jalankan: termux-setup-storage"
    fi
fi

# ══════════════════════════════════════════════════════════════
# STEP 3: Update & install paket
# ══════════════════════════════════════════════════════════════
step "3/7" "Install Python & dependencies"

info "Update repository..."
pkg update -y -q 2>/dev/null || warn "Update sebagian gagal, melanjutkan..."

info "Install Python..."
pkg install -y python 2>/dev/null && ok "Python" || fail "Gagal install Python"

info "Install curl..."
pkg install -y curl 2>/dev/null && ok "curl" || warn "curl gagal"

info "Install library Python..."
for lib in pypdf flask rich requests; do
    pip install "$lib" --quiet --break-system-packages 2>/dev/null \
        && ok "  pip: $lib" \
        || warn "  pip: $lib gagal — coba manual: pip install $lib"
done

# ══════════════════════════════════════════════════════════════
# STEP 4: Download script
# ══════════════════════════════════════════════════════════════
step "4/7" "Download script"

mkdir -p "$INSTALL_DIR"
info "Folder instalasi: $INSTALL_DIR"
echo ""

DOWNLOAD_FAILED=()
for script in "${SCRIPTS[@]}"; do
    dest="$INSTALL_DIR/$script"
    url="$GITHUB_RAW/$script"
    if [ -f "$dest" ]; then
        warn "$script sudah ada — dilewati"
        continue
    fi
    info "Download $script..."
    if curl -sSfL "$url" -o "$dest" 2>/dev/null || wget -q "$url" -O "$dest" 2>/dev/null; then
        ok "$script"
    else
        rm -f "$dest"
        DOWNLOAD_FAILED+=("$script")
        warn "$script GAGAL diunduh"
    fi
done

# ══════════════════════════════════════════════════════════════
# STEP 5: Konfigurasi interaktif
# ══════════════════════════════════════════════════════════════
step "5/7" "Konfigurasi akun & folder"
echo ""

# ── Akun Galva XEA ──────────────────────────────────────────
echo -e "  ${BOLD}${CYAN}── Akun Galva XEA ──${RESET}"
echo -e "  Username & password yang dipakai login di aplikasi XEA"
echo ""

printf "  Username XEA: "
read -r XEA_USERNAME
while [ -z "$XEA_USERNAME" ]; do
    warn "Username tidak boleh kosong"
    printf "  Username XEA: "
    read -r XEA_USERNAME
done

printf "  Password XEA: "
read -rs XEA_PASSWORD
echo ""
while [ -z "$XEA_PASSWORD" ]; do
    warn "Password tidak boleh kosong"
    printf "  Password XEA: "
    read -rs XEA_PASSWORD
    echo ""
done
ok "Akun Galva XEA dicatat"
echo ""

# ── Gmail ────────────────────────────────────────────────────
echo -e "  ${BOLD}${CYAN}── Email Gmail (untuk kirim laporan) ──${RESET}"
echo -e "  ${YELLOW}Butuh App Password (16 karakter), bukan password Gmail biasa.${RESET}"
echo -e "  ${YELLOW}Buat di: myaccount.google.com → Keamanan → App Passwords${RESET}"
echo -e "  ${YELLOW}(Bisa dikosongkan dulu, isi nanti di tab Konfigurasi)${RESET}"
echo ""

printf "  Gmail pengirim (Enter = lewati): "
read -r GMAIL_SENDER
echo ""

printf "  App Password Gmail (Enter = lewati): "
read -rs GMAIL_PASSWORD
echo ""

printf "  Penerima TO, pisah koma (Enter = lewati): "
read -r GMAIL_TO
echo ""

ok "Konfigurasi email dicatat"
echo ""

# ── Folder ───────────────────────────────────────────────────
echo -e "  ${BOLD}${CYAN}── Folder PDF ──${RESET}"
echo ""

DEFAULT_SOURCE="/sdcard/Download/galva_docs"
DEFAULT_OUTPUT="/sdcard/Documents/Hasil"

printf "  Folder sumber PDF\n  [default: $DEFAULT_SOURCE]\n  → "
read -r FOLDER_SOURCE
FOLDER_SOURCE="${FOLDER_SOURCE:-$DEFAULT_SOURCE}"

printf "  Folder output/hasil\n  [default: $DEFAULT_OUTPUT]\n  → "
read -r FOLDER_OUTPUT
FOLDER_OUTPUT="${FOLDER_OUTPUT:-$DEFAULT_OUTPUT}"

ok "Folder dikonfigurasi"
echo ""

# ── Tulis USERNAME/PASSWORD ke galva_download.py ────────────
if [ -f "$INSTALL_DIR/galva_download.py" ]; then
    sed -i "s|^USERNAME = .*|USERNAME = \"$XEA_USERNAME\"|" "$INSTALL_DIR/galva_download.py"
    sed -i "s|^PASSWORD = .*|PASSWORD = \"$XEA_PASSWORD\"|" "$INSTALL_DIR/galva_download.py"
    sed -i "s|^SAVE_DIR.*=.*|SAVE_DIR    = \"$FOLDER_SOURCE\"|" "$INSTALL_DIR/galva_download.py"
    ok "galva_download.py dikonfigurasi"
else
    warn "galva_download.py belum ada — konfigurasi akun XEA dilewati"
fi

# ── Tulis merge_pdf_config.json ─────────────────────────────
TO_JSON="[]"
if [ -n "$GMAIL_TO" ]; then
    TO_JSON=$(echo "$GMAIL_TO" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
items = [x.strip() for x in raw.split(',') if x.strip()]
print(json.dumps(items))
" 2>/dev/null || echo "[]")
fi

python3 - << PYEOF
import json

config = {
    "source_dir"      : "$FOLDER_SOURCE",
    "output_dir"      : "$FOLDER_OUTPUT",
    "digit_count"     : 6,
    "sender_email"    : "$GMAIL_SENDER",
    "sender_password" : "$GMAIL_PASSWORD",
    "to"              : $TO_JSON,
    "cc"              : [],
    "bcc"             : [],
    "subject_template": "Laporan PDF - {tipe_layanan}",
    "body_template"   : (
        "Halo,\n\nBerikut daftar pelanggan untuk Tipe Layanan [{tipe_layanan}]:\n\n"
        "{daftar_pelanggan}\n\nTerlampir {jumlah_file} file PDF.\n\n"
        "Email ini dikirim otomatis oleh script merge_pdf."
    ),
}
with open("$CONFIG_FILE", "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print("  ✓  merge_pdf_config.json ditulis ke $CONFIG_FILE")
PYEOF

# ══════════════════════════════════════════════════════════════
# STEP 6: Shell scripts & auto-start
# ══════════════════════════════════════════════════════════════
step "6/7" "Buat shortcut & auto-start"

# start_merge_web.sh
cat > "$INSTALL_DIR/start_merge_web.sh" << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
cd $HOME/merge_pdf
python merge_web.py >> $HOME/merge_pdf/server.log 2>&1 &
echo "merge_web started PID=$!" >> $HOME/merge_pdf/server.log
SCRIPT

# start_manual.sh
cat > "$INSTALL_DIR/start_manual.sh" << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
cd $HOME/merge_pdf
pkill -f "merge_web.py" 2>/dev/null; sleep 1
python merge_web.py &
echo "Server: http://localhost:5000  (PID $!)"
sleep 2
termux-open-url http://localhost:5000
SCRIPT

# stop_merge_web.sh
cat > "$INSTALL_DIR/stop_merge_web.sh" << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
pkill -f "merge_web.py" 2>/dev/null && echo "Server dihentikan." || echo "Server tidak berjalan."
SCRIPT

# tui.sh
cat > "$INSTALL_DIR/tui.sh" << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
cd $HOME/merge_pdf && python merge_tui.py
SCRIPT

# download.sh
cat > "$INSTALL_DIR/download.sh" << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
cd $HOME/merge_pdf && python galva_download.py
SCRIPT

chmod +x "$INSTALL_DIR"/*.sh
ok "5 shell script dibuat"

# Termux:Boot
if [ -d "/data/data/com.termux.boot" ]; then
    mkdir -p "$BOOT_DIR"
    ln -sf "$INSTALL_DIR/start_merge_web.sh" "$BOOT_DIR/start_merge_web.sh" 2>/dev/null || true
    ok "Termux:Boot → server auto-start saat HP nyala"
else
    warn "Termux:Boot tidak terinstall (install dari F-Droid)"
fi

# Termux:Widget
if [ -d "/data/data/com.termux.widget" ]; then
    mkdir -p "$WIDGET_DIR"
    for script in start_manual.sh stop_merge_web.sh tui.sh download.sh; do
        ln -sf "$INSTALL_DIR/$script" "$WIDGET_DIR/$script" 2>/dev/null || true
    done
    ok "Termux:Widget → 4 shortcut siap di homescreen"
else
    warn "Termux:Widget tidak terinstall (install dari F-Droid)"
fi

# ══════════════════════════════════════════════════════════════
# STEP 7: Verifikasi
# ══════════════════════════════════════════════════════════════
step "7/7" "Verifikasi"

ALL_OK=true
for script in "${SCRIPTS[@]}"; do
    if [ -f "$INSTALL_DIR/$script" ]; then
        ok "$script"
    else
        warn "$script — tidak ditemukan"
        ALL_OK=false
    fi
done

python3 -c "import pypdf, flask, rich, requests" 2>/dev/null \
    && ok "Library Python OK" \
    || warn "Ada library yang belum terinstall"

[ -f "$CONFIG_FILE" ] && ok "merge_pdf_config.json" || warn "Config file tidak ada"

# ══════════════════════════════════════════════════════════════
# SELESAI
# ══════════════════════════════════════════════════════════════
echo ""
sep
if $ALL_OK; then
    echo -e "  ${GREEN}${BOLD}✓ Instalasi selesai!${RESET}"
else
    echo -e "  ${YELLOW}${BOLD}⚠ Selesai dengan peringatan${RESET}"
fi
sep
echo ""
echo -e "  ${BOLD}Cara pakai pertama kali:${RESET}"
echo ""
echo -e "  ${CYAN}Jalankan Web GUI:${RESET}"
echo -e "    cd ~/merge_pdf && bash start_manual.sh"
echo -e "    Lalu buka Chrome → ${BOLD}http://localhost:5000${RESET}"
echo ""
echo -e "  ${CYAN}Download dokumen Galva:${RESET}"
echo -e "    cd ~/merge_pdf && python galva_download.py"
echo ""
if [ ${#DOWNLOAD_FAILED[@]} -gt 0 ]; then
    echo -e "  ${YELLOW}File gagal diunduh — salin manual ke $INSTALL_DIR/ :${RESET}"
    for f in "${DOWNLOAD_FAILED[@]}"; do
        echo -e "    – $f"
    done
    echo ""
fi
sep
echo ""
