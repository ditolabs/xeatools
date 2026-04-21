#!/usr/bin/env python3
"""
launcher.py — Menu utama untuk semua script merge_pdf & galva.
Jalankan : python launcher.py
"""

import subprocess
import sys
import os
from pathlib import Path

# Folder tempat semua script berada
BASE_DIR = Path(__file__).parent

SCRIPTS = {
    "1": ("galva_merge.py",  "📥 Download + Merge (dari API Galva)"),
    "2": ("merge_tui.py",    "📄 Merge saja dari file lokal (TUI)"),
    "3": ("merge_web.py",    "🌐 Web GUI (buka di Chrome Android)"),
    "4": ("galva_download.py","⬇  Download saja (tanpa merge)"),
}


def clear():
    os.system("clear")


def print_header():
    clear()
    print("=" * 50)
    print("  merge_pdf  •  Launcher")
    print("  PT Galva Technologies Tbk")
    print("=" * 50)


def main():
    while True:
        print_header()
        print()
        for key, (_, label) in SCRIPTS.items():
            print(f"  [{key}] {label}")
        print(f"  [0] ✕  Keluar")
        print()

        pilihan = input("Pilih menu: ").strip()

        if pilihan == "0":
            print("\nSampai jumpa!\n")
            break

        if pilihan not in SCRIPTS:
            print("Pilihan tidak valid.")
            input("Tekan Enter untuk kembali...")
            continue

        script_file, label = SCRIPTS[pilihan]
        script_path = BASE_DIR / script_file

        if not script_path.exists():
            print(f"\nFile tidak ditemukan: {script_file}")
            input("Tekan Enter untuk kembali...")
            continue

        print(f"\nMemulai: {label}\n")
        print("=" * 50)

        # Jalankan script
        try:
            subprocess.run(
                [sys.executable, str(script_path)],
                check=False
            )
        except KeyboardInterrupt:
            print("\n\nDihentikan.")

        print()
        input("Tekan Enter untuk kembali ke menu...")


if __name__ == "__main__":
    main()
