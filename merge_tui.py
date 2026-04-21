#!/usr/bin/env python3
"""
merge_tui.py — Terminal UI untuk merge_pdf
Instalasi: pip install rich
Jalankan : python merge_tui.py
"""

import sys
import os
import threading
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.text import Text
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.columns import Columns
    from rich import box
    from rich.rule import Rule
    from rich.padding import Padding
except ImportError:
    print("ERROR: pip install rich")
    sys.exit(1)

try:
    import merge_core as core
except ImportError:
    print("ERROR: Pastikan merge_core.py ada di folder yang sama.")
    sys.exit(1)

console = Console()

TITLE_COLOR  = "bold cyan"
OK_COLOR     = "green"
FAIL_COLOR   = "red"
WARN_COLOR   = "yellow"
INFO_COLOR   = "dim white"
ACCENT       = "bright_cyan"
NAVY         = "blue"


def print_header():
    console.clear()
    console.print(Panel(
        Text("merge_pdf  •  TUI", style="bold cyan", justify="center"),
        border_style="cyan",
        padding=(1, 4),
    ))


def main_menu() -> str:
    console.print()
    menu = Table(show_header=False, box=box.ROUNDED,
                 border_style="cyan", padding=(0, 2))
    menu.add_column("No", style="bold cyan", width=4)
    menu.add_column("Pilihan", style="white")

    items = [
        ("1", "▶  Jalankan Merge PDF"),
        ("2", "⚙  Konfigurasi Folder & Email"),
        ("3", "0  Keluar"),
    ]
    for no, label in items:
        menu.add_row(no, label)

    console.print(menu)
    console.print()
    pilihan = Prompt.ask("[cyan]Pilih menu[/cyan]",
                         choices=["0","1","2"], default="1")
    return pilihan


def menu_config():
    print_header()
    cfg = core.load_config()
    console.print(Panel("[bold]Konfigurasi[/bold]", border_style="cyan"))
    console.print()

    def ask(label, key, password=False):
        current = cfg.get(key, "")
        if isinstance(current, list):
            current = ", ".join(current)
        display = "****" if (password and current) else (current or "[dim](kosong)[/dim]")
        console.print(f"  [cyan]{label}[/cyan]  →  {display}")
        new_val = Prompt.ask(f"  [dim]Nilai baru (Enter = tetap)[/dim]",
                             default="", password=password)
        if new_val.strip():
            if key in ("to", "cc", "bcc"):
                cfg[key] = [x.strip() for x in new_val.split(",") if x.strip()]
            elif key == "digit_count":
                cfg[key] = int(new_val)
            else:
                cfg[key] = new_val.strip()

    console.print("[bold]── Folder ──[/bold]")
    ask("Folder Sumber  ", "source_dir")
    ask("Folder Output  ", "output_dir")
    console.print()
    console.print("[bold]── Email ──[/bold]")
    ask("Pengirim (Gmail)     ", "sender_email")
    ask("App Password (16 chr)", "sender_password", password=True)
    ask("Penerima TO           ", "to")
    ask("CC  (pisah koma)      ", "cc")
    ask("BCC (pisah koma)      ", "bcc")

    console.print()
    if Confirm.ask("[cyan]Simpan konfigurasi?[/cyan]", default=True):
        core.save_config(cfg)
        console.print("[green]✓ Konfigurasi disimpan.[/green]")
    else:
        console.print("[yellow]Dibatalkan.[/yellow]")
    Prompt.ask("\n[dim]Tekan Enter untuk kembali[/dim]", default="")


def menu_run():
    print_header()
    cfg = core.load_config()

    console.print(Panel(
        f"[cyan]Sumber :[/cyan] {cfg['source_dir']}\n"
        f"[cyan]Output :[/cyan] {cfg['output_dir']}",
        title="[bold]Konfigurasi Aktif[/bold]",
        border_style="cyan",
    ))
    console.print()

    if not Confirm.ask("[cyan]Mulai proses merge?[/cyan]", default=True):
        return

    console.print()
    console.rule("[cyan]Proses Merge[/cyan]")
    console.print()

    log_lines = []
    result_holder = {}
    event_lock = threading.Lock()

    def cb(event, data):
        with event_lock:
            if event == "scan":
                console.print(f"  [cyan]🔍 Scan[/cyan]  {data['total']} file PDF ditemukan di {data['source_dir']}")
            elif event == "classify":
                console.print(f"  [cyan]📂 Klasifikasi[/cyan]  "
                              f"STBA: [green]{data['stba']}[/green]  "
                              f"STATS: [green]{data['stats']}[/green]  "
                              f"Tidak dikenali: [yellow]{data['unknown']}[/yellow]")
            elif event == "pair_found":
                console.print(f"  [cyan]🔗 Pasangan[/cyan]  "
                              f"Cocok: [green]{data['pairs']}[/green]  "
                              f"Hanya STBA: [yellow]{data['only_stba']}[/yellow]  "
                              f"Hanya STATS: [yellow]{data['only_stats']}[/yellow]")
                console.print()
            elif event == "merge_ok":
                line = (f"  [green]✓[/green] [{data['key']}]  "
                        f"[white]{data['nama']}[/white]  "
                        f"[dim]→ {data['folder']}/{data['key']}.pdf[/dim]")
                console.print(line)
                log_lines.append(f"✓ {data['key']} — {data['nama']} ({data['tipe']})")
            elif event == "merge_fail":
                console.print(f"  [red]✗[/red] [{data.get('key','')}] Gagal merge")
            elif event == "file_kosong":
                console.print(f"  [yellow]⚠[/yellow]  {data['name']} → File Kosong/")
            elif event == "arsip":
                console.print()
                console.print(f"  [cyan]📦 Arsip[/cyan]  {data['jumlah']} file mentah "
                              f"dipindah ke [{data['folder']}]")
            elif event == "txt_saved":
                console.print(f"  [dim]📝 {Path(data['path']).name} disimpan[/dim]")
            elif event == "estimasi":
                console.print(f"  [cyan]💰 estimasi_biaya.txt disimpan[/cyan]")
            elif event == "done":
                result_holder.update(data)

    exc_holder = {}
    def worker():
        try:
            core.run_merge(
                cfg["source_dir"], cfg["output_dir"],
                cfg.get("digit_count", 6), cb
            )
        except Exception as e:
            exc_holder["err"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join()

    if exc_holder.get("err"):
        console.print(f"\n[red]ERROR: {exc_holder['err']}[/red]")
        Prompt.ask("\n[dim]Tekan Enter untuk kembali[/dim]", default="")
        return

    result = result_holder
    if not result:
        console.print("\n[red]Proses selesai tanpa hasil.[/red]")
        Prompt.ask("\n[dim]Tekan Enter untuk kembali[/dim]", default="")
        return

    console.print()
    console.rule("[cyan]Ringkasan Hasil[/cyan]")
    console.print()

    summary_table = Table(box=box.ROUNDED, border_style="cyan",
                          show_header=True, header_style="bold cyan")
    summary_table.add_column("Tipe Layanan", style="white")
    summary_table.add_column("Jumlah", justify="right", style="green")
    summary_table.add_column("File", style="dim")

    for folder_name, entries in sorted(result.get("summary", {}).items()):
        files = ", ".join(e[0] for e in entries[:3])
        if len(entries) > 3: files += f" (+{len(entries)-3} lagi)"
        summary_table.add_row(folder_name, str(len(entries)), files)

    console.print(summary_table)
    console.print()

    stat_table = Table(box=None, show_header=False, padding=(0,2))
    stat_table.add_column("Label", style="dim")
    stat_table.add_column("Nilai", style="bold white")
    stat_table.add_row("Merge berhasil",  f"[green]{result['success']} pasang[/green]")
    stat_table.add_row("Merge gagal",     f"[red]{result['failed']} pasang[/red]")
    stat_table.add_row("File Kosong",     f"[yellow]{result['file_kosong']} file[/yellow]")
    if result.get("folder_bulan"):
        stat_table.add_row("Diarsip ke", f"[cyan]{result['folder_bulan']}[/cyan]")
    console.print(stat_table)
    console.print()

    if not result.get("summary"):
        Prompt.ask("[dim]Tekan Enter untuk kembali[/dim]", default="")
        return

    if not cfg.get("sender_email") or not cfg.get("to"):
        console.print("[yellow]Email belum dikonfigurasi, kirim email dilewati.[/yellow]")
        Prompt.ask("\n[dim]Tekan Enter untuk kembali[/dim]", default="")
        return

    console.rule("[cyan]Konfirmasi Kirim Email[/cyan]")
    console.print()

    for folder_name, entries in sorted(result["summary"].items()):
        console.print(f"  [bold cyan]{folder_name}[/bold cyan] — {len(entries)} file:")
        for key, nama, path in entries:
            console.print(f"    [dim]•[/dim] {Path(str(path)).name}  [dim]{nama}[/dim]")
    console.print()

    if Confirm.ask("[cyan]Kirim semua file di atas melalui email?[/cyan]", default=False):
        console.print()
        console.rule("[cyan]Mengirim Email[/cyan]")
        console.print()

        def email_cb(event, data):
            if event == "email_result":
                if data["ok"]:
                    console.print(f"  [green]✓[/green] [{data['tipe']}] {data['msg']}")
                else:
                    console.print(f"  [red]✗[/red] [{data['tipe']}] {data['msg']}")

        email_result = core.do_send_emails(result["summary"], cfg, email_cb)
        console.print()
        console.print(f"  Email terkirim : [green]{email_result['ok']}[/green]  "
                      f"Gagal : [red]{email_result['fail']}[/red]")
    else:
        console.print("[yellow]Pengiriman email dibatalkan.[/yellow]")

    Prompt.ask("\n[dim]Tekan Enter untuk kembali ke menu[/dim]", default="")


def main():
    while True:
        print_header()
        pilihan = main_menu()
        if pilihan == "1":
            menu_run()
        elif pilihan == "2":
            menu_config()
        elif pilihan == "0":
            console.print("\n[cyan]Sampai jumpa![/cyan]\n")
            break

if __name__ == "__main__":
    main()
