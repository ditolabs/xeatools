#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────
# setup.sh — Setup otomatis merge_pdf di HP baru
# Jalankan: bash setup.sh YOUR_GITHUB_TOKEN
# ─────────────────────────────────────────────────────────────

REPO_USER="ShadowSoldiers"
REPO_NAME="xeatools"
REPO_URL="https://github.com/ShadowSoldiers/xeatools.git"
INSTALL_DIR="$HOME/${REPO_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "═══════════════════════════════════════"
echo "   merge_pdf — Setup Otomatis"
echo "═══════════════════════════════════════"
echo ""

# ── Cek token ───────────────────────────────────────────────
TOKEN="$1"
if [ -z "$TOKEN" ]; then
  echo -e "${RED}ERROR: Token GitHub tidak diberikan.${NC}"
  echo ""
  echo "Cara pakai:"
  echo "  bash setup.sh YOUR_GITHUB_TOKEN"
  echo ""
  echo "Cara buat token:"
  echo "  1. Buka github.com → Settings → Developer settings"
  echo "  2. Personal access tokens → Tokens (classic)"
  echo "  3. Generate new token → centang 'repo' → Generate"
  exit 1
fi

REPO_URL_WITH_TOKEN="https://${TOKEN}@github.com/ShadowSoldiers/xeatools.git"

# ── Step 1: Update Termux ────────────────────────────────────
echo -e "${CYAN}[1/6] Update paket Termux...${NC}"
pkg update -y && pkg upgrade -y
echo -e "${GREEN}✓ Termux diperbarui${NC}"
echo ""

# ── Step 2: Install packages ─────────────────────────────────
echo -e "${CYAN}[2/6] Install paket yang diperlukan...${NC}"
pkg install -y python git
echo -e "${GREEN}✓ Python & Git terinstall${NC}"
echo ""

# ── Step 3: Setup storage ────────────────────────────────────
echo -e "${CYAN}[3/6] Setup akses storage...${NC}"
if [ ! -d "/sdcard" ]; then
  echo -e "${YELLOW}⚠  Memberikan izin akses storage...${NC}"
  termux-setup-storage
  sleep 3
fi
mkdir -p /sdcard/Documents /sdcard/Documents/Hasil
echo -e "${GREEN}✓ Storage siap${NC}"
echo ""

# ── Step 4: Clone repository ─────────────────────────────────
echo -e "${CYAN}[4/6] Clone repository...${NC}"
if [ -d "$INSTALL_DIR" ]; then
  echo -e "${YELLOW}⚠  Folder sudah ada, menghapus dan clone ulang...${NC}"
  rm -rf "$INSTALL_DIR"
fi
git clone "$REPO_URL_WITH_TOKEN" "$INSTALL_DIR"
if [ $? -ne 0 ]; then
  echo -e "${RED}✗ Clone gagal. Periksa token dan koneksi internet.${NC}"
  exit 1
fi
# Set strategi pull default agar update.sh tidak butuh konfirmasi
git -C "$INSTALL_DIR" config pull.rebase true
echo -e "${GREEN}✓ Repository berhasil di-clone ke ${INSTALL_DIR}${NC}"
echo ""

# ── Step 5: Install Python libraries ────────────────────────
echo -e "${CYAN}[5/6] Install Python libraries...${NC}"
pip install flask requests pypdf apscheduler --break-system-packages -q
if [ $? -ne 0 ]; then
  echo -e "${YELLOW}⚠  Beberapa library mungkin gagal. Coba manual:${NC}"
  echo "    pip install flask requests pypdf apscheduler --break-system-packages"
else
  echo -e "${GREEN}✓ Python libraries terinstall${NC}"
fi
echo ""

# ── Step 6: Simpan token untuk update otomatis ───────────────
echo -e "${CYAN}[6/6] Menyimpan konfigurasi update...${NC}"
UPDATE_CFG="$HOME/.merge_pdf_update"
cat > "$UPDATE_CFG" << CFGEOF
REPO_USER="${REPO_USER}"
REPO_NAME="${REPO_NAME}"
TOKEN="${TOKEN}"
INSTALL_DIR="${INSTALL_DIR}"
CFGEOF
chmod 600 "$UPDATE_CFG"
echo -e "${GREEN}✓ Konfigurasi update disimpan${NC}"
echo ""

# ── Setup Termux:Boot (opsional) ──────────────────────────────
BOOT_DIR="$HOME/.termux/boot"
if [ -d "$BOOT_DIR" ] || pkg list-installed 2>/dev/null | grep -q termux-boot; then
  mkdir -p "$BOOT_DIR"
  cat > "$BOOT_DIR/start_merge.sh" << BOOTEOF
#!/data/data/com.termux/files/usr/bin/bash
sleep 5
cd ${INSTALL_DIR}
python merge_web.py &
BOOTEOF
  chmod +x "$BOOT_DIR/start_merge.sh"
  echo -e "${GREEN}✓ Termux:Boot dikonfigurasi (auto-start)${NC}"
  echo ""
fi

# ── Selesai ───────────────────────────────────────────────────
echo "═══════════════════════════════════════"
echo -e "${GREEN}   Setup selesai!${NC}"
echo "═══════════════════════════════════════"
echo ""
echo "Cara menjalankan aplikasi:"
echo ""
echo -e "  ${CYAN}cd ${INSTALL_DIR}${NC}"
echo -e "  ${CYAN}python merge_web.py${NC}"
echo ""
echo "Lalu buka Chrome Android:"
echo -e "  ${CYAN}http://localhost:5000${NC}"
echo ""
