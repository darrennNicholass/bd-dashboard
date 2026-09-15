"""
preview_watchlist.py - alat cek cepat tampilan panel "Perlu perhatian".

Mengambil blok CSS langsung dari app.py, lalu merendernya dengan nama partner
panjang di kolom sempit. Tidak menjalankan Streamlit dan tidak menyentuh
Google Sheets, jadi hasilnya keluar dalam hitungan detik.

    .venv/Scripts/python.exe preview_watchlist.py
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "shot_watchlist.html"

# Baris uji: sengaja memakai nama terpanjang yang ada di data plus satu nama
# yang lebih panjang lagi, untuk memastikan teks tidak menabrak tag durasi.
ROWS = [
    ("PT Sasa Inti", "FMCG & FnB · berakhir SEP 26", "bulan ini", "#5B6BF7"),
    ("DMAC Chicken Gunung Sahari", "FMCG & FnB · berakhir SEP 26", "bulan ini", "#5B6BF7"),
    ("Kabobs Premium Kebab Nusantara Jaya", "E-Commerce and Retail · berakhir OCT 26",
     "1 bln lagi", "#2E9BF0"),
    ("BumiBaik", "Education · berakhir SEP 26", "bulan ini", "#5B6BF7"),
    ("Gen Z Outfit", "FMCG & FnB · berakhir JUN 26", "3 bln lewat", "#16357E"),
]


def extract_style() -> str:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r"STYLE = \"\"\"(.*?)\"\"\"", source, re.DOTALL)
    if not match:
        raise SystemExit("Blok STYLE tidak ditemukan di app.py")
    return match.group(1)


def initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def main() -> None:
    rows = "".join(
        '<div class="bd-row">'
        f'<div class="bd-avatar" style="background:{color}">{initials(name)}</div>'
        '<div class="bd-row-main">'
        f'<div class="bd-row-name">{html.escape(name)}</div>'
        f'<div class="bd-row-meta">{html.escape(meta)}</div>'
        "</div>"
        f'<span class="bd-row-tag">{html.escape(tag)}</span>'
        "</div>"
        for name, meta, tag, color in ROWS
    )
    # Tiga lebar kolom: sempit (layar 1366), sedang, dan lega (layar 1680).
    panels = "".join(
        f'<div style="width:{width}px">'
        '<div data-testid="stVerticalBlockBorderWrapper" '
        'style="padding:14px 16px;background:#fff;border:1px solid #EDF0F8;'
        'border-radius:20px">'
        f'<p class="bd-card-title">Perlu perhatian</p>'
        f'<p class="bd-card-sub">Kontrak terdekat berakhir · kolom {width}px</p>'
        f'<div class="bd-list">{rows}</div>'
        "</div></div>"
        for width in (280, 330, 420)
    )
    OUTPUT.write_text(
        '<!doctype html><html lang="id"><head><meta charset="utf-8">'
        f"<style>{extract_style().replace('<style>', '').replace('</style>', '')}</style>"
        "<style>body{background:#F4F6FC;margin:24px;display:flex;gap:24px;"
        "align-items:flex-start}</style></head><body>"
        f"{panels}</body></html>",
        encoding="utf-8",
    )
    print(f"Ditulis: {OUTPUT.name}")


if __name__ == "__main__":
    main()
