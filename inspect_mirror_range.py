"""
inspect_mirror_range.py - PERIKSA KONFIGURASI IMPORTRANGE DI NATIONAL MIRROR

Tujuan: mengetahui RANGE mana yang sekarang ditarik mirror dari National ESSM,
supaya kita tahu persis apa yang harus diperlebar ketika ada kolom yang
belum ikut tertarik.

KEAMANAN - ini yang membuat script ini aman dijalankan:
    Rumus IMPORTRANGE memuat URL/ID spreadsheet National ESSM asli.
    Script ini HANYA mencetak bagian RANGE-nya. URL sumber disensor dan
    tidak pernah ditampilkan, disimpan, atau ditulis ke file mana pun.

Script ini juga tidak pernah membuka National ESSM asli. Yang dibaca
hanyalah teks rumus yang tersimpan di mirror.

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe inspect_mirror_range.py
"""

from __future__ import annotations

import re
import sys

from src import sheets

# Menangkap argumen kedua IMPORTRANGE (range-nya).
# Argumen pertama (URL/ID) sengaja tidak pernah ditangkap ke variabel
# yang dicetak.
IMPORTRANGE_PATTERN = re.compile(
    r"IMPORTRANGE\(\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[A-Za-z0-9_$!.]+)"  # arg 1: URL/ID atau referensi sel
    r"\s*[,;]\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([A-Za-z0-9_$!.]+))"  # arg 2: range
    r"\s*\)",
    re.IGNORECASE,
)

HAS_IMPORTRANGE = re.compile(r"IMPORTRANGE", re.IGNORECASE)


def line(char: str = "-", width: int = 74) -> None:
    print(char * width)


def extract_range(formula: str) -> str:
    """Ambil HANYA bagian range dari rumus IMPORTRANGE."""
    match = IMPORTRANGE_PATTERN.search(formula)
    if not match:
        return "(range tidak bisa diurai - kemungkinan memakai referensi sel)"
    quoted, single, reference = match.groups()
    if quoted is not None:
        return quoted
    if single is not None:
        return single
    return f"(range diambil dari referensi sel: {reference})"


def describe_range(range_text: str) -> str:
    """Terjemahkan range jadi keterangan kolom terakhir yang ikut tertarik."""
    match = re.match(
        r"^(?:'?[^'!]+'?!)?([A-Z]+)(\d*):([A-Z]+)(\d*)$",
        range_text.strip(),
        re.IGNORECASE,
    )
    if not match:
        return ""
    start_col, _start_row, end_col, end_row = match.groups()
    end_number = column_number(end_col.upper())
    baris = f"sampai baris {end_row}" if end_row else "semua baris"
    return (
        f"kolom {start_col.upper()} sampai {end_col.upper()} "
        f"(= {end_number} kolom), {baris}"
    )


def column_number(letters: str) -> int:
    """A -> 1, Z -> 26, AA -> 27, BZ -> 78."""
    total = 0
    for char in letters:
        total = total * 26 + (ord(char) - ord("A") + 1)
    return total


def main() -> int:
    print()
    line("#")
    print("PERIKSA RANGE IMPORTRANGE DI NATIONAL MIRROR")
    line("#")
    print("URL spreadsheet sumber TIDAK ditampilkan - hanya range-nya.")

    try:
        spreadsheet = sheets.open_national_mirror()
    except Exception as exc:
        print(f"\nGagal membuka National Mirror: {type(exc).__name__}: {exc}")
        return 1

    for worksheet in spreadsheet.worksheets():
        print()
        line("=")
        print(f"TAB: {worksheet.title}")
        line("=")

        try:
            formulas = sheets.read_worksheet_formulas(worksheet)
        except Exception as exc:
            print(f"  Gagal membaca rumus: {type(exc).__name__}: {exc}")
            continue

        found = 0
        for row_index, row in enumerate(formulas):
            for col_index, formula in enumerate(row):
                if not formula or not HAS_IMPORTRANGE.search(str(formula)):
                    continue
                found += 1
                position = f"{sheets.column_letter(col_index + 1)}{row_index + 1}"
                range_text = extract_range(str(formula))
                keterangan = describe_range(range_text)
                print(f"  sel {position}")
                print(f"    range ditarik : {range_text}")
                if keterangan:
                    print(f"    artinya       : {keterangan}")

        if not found:
            print("  Tidak ada rumus IMPORTRANGE di tab ini.")
            print("  Kemungkinan isinya hasil paste-values, bukan mirror live.")

    print()
    line("#")
    print("Selesai. Tidak ada URL sumber yang dicetak.")
    line("#")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
