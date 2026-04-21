#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────
# update.sh — Update manual via terminal
# Jalankan: bash update.sh
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

UPDATE_CFG="$HOME/.merge_pdf_update"

echo ""
echo "═══════════════════════════════════════"
echo "   merge_pdf — Update"
echo "═══════════════════════════════════════"
echo ""

# ── Baca konfigurasi ─────────────────────────────────────────
if [ ! -f "$UPDATE_CFG" ]; then
  echo -e "${RED}ERROR: File konfigurasi update tidak ditemukan.${NC}"
  echo "Jalankan setup.sh terlebih dahulu."
  exit 1
fi
source "$UPDATE_CFG"

cd "$INSTALL_DIR" || { echo -e "${RED}ERROR: Folder ${INSTALL_DIR} tidak ditemukan.${NC}"; exit 1; }

# ── Cek koneksi ──────────────────────────────────────────────
echo -e "${CYAN}Mengecek versi terbaru di GitHub...${NC}"
REPO_URL_WITH_TOKEN="https://${TOKEN}@github.com/${REPO_USER}/${REPO_NAME}.git"

git fetch "$REPO_URL_WITH_TOKEN" main --quiet 2>/dev/null
if [ $? -ne 0 ]; then
  echo -e "${RED}✗ Tidak bisa terhubung ke GitHub. Periksa koneksi internet.${NC}"
  exit 1
fi

# ── Bandingkan versi ─────────────────────────────────────────
LOCAL=$(git rev-parse HEAD 2>/dev/null | cut -c1-7)
REMOTE=$(git rev-parse FETCH_HEAD 2>/dev/null | cut -c1-7)

echo "  Versi lokal  : ${LOCAL}"
echo "  Versi GitHub : ${REMOTE}"
echo ""

if [ "$LOCAL" = "$REMOTE" ]; then
  echo -e "${GREEN}✓ Sudah versi terbaru. Tidak ada update.${NC}"
  echo ""
  exit 0
fi

# ── Tampilkan perubahan ──────────────────────────────────────
echo -e "${YELLOW}Ada update tersedia!${NC}"
echo ""
echo "File yang berubah:"
git diff --name-only HEAD FETCH_HEAD 2>/dev/null | while read f; do
  echo "  • $f"
done
echo ""

read -p "Terapkan update sekarang? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "Update dibatalkan."
  exit 0
fi

# ── Backup config ────────────────────────────────────────────
echo ""
echo -e "${CYAN}Backup konfigurasi...${NC}"
CONFIG_FILE="$HOME/merge_pdf_config.json"
if [ -f "$CONFIG_FILE" ]; then
  cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
  echo -e "${GREEN}✓ Konfigurasi di-backup ke merge_pdf_config.json.bak${NC}"
fi

# ── Pull update ──────────────────────────────────────────────
echo -e "${CYAN}Mengunduh update...${NC}"
git remote set-url origin "$REPO_URL_WITH_TOKEN"
git pull origin main --quiet

if [ $? -eq 0 ]; then
  NEW=$(git rev-parse HEAD | cut -c1-7)
  echo -e "${GREEN}✓ Update berhasil! ${LOCAL} → ${NEW}${NC}"
  echo ""
  echo -e "${YELLOW}Restart merge_web.py untuk menerapkan perubahan.${NC}"
else
  echo -e "${RED}✗ Update gagal. Coba jalankan ulang.${NC}"
  exit 1
fi
echo ""
