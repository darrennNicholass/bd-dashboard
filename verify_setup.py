"""
verify_setup.py — PHASE 1: VERIFY PROJECT & CONNECTION

Tujuan: membuktikan bahwa fondasi teknis proyek sudah benar SEBELUM
kita menulis satu baris pun logika analitik.

Yang diverifikasi:
    1. Versi Python & interpreter yang dipakai
    2. Dependensi wajib bisa di-import
    3. .env terbaca            -> dilaporkan YES/NO saja
    4. credentials.json ada    -> dilaporkan YES/NO saja
    5. Autentikasi Service Account berhasil
    6. Koneksi ke Active ESSM  -> hanya judul spreadsheet
    7. Koneksi ke National Mirror -> hanya judul spreadsheet
    8. Daftar nama worksheet dari kedua sumber
    9. Sampel kecil dari SATU worksheet Active (bukti data benar-benar terbaca)

KEAMANAN:
    Script ini TIDAK PERNAH mencetak nilai URL spreadsheet maupun isi
    credentials.json. Hanya status boolean dan judul spreadsheet.

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe verify_setup.py
"""

from __future__ import annotations

import sys

from src import sheets

# Batas preview sampel: sengaja kecil supaya kolom sensitif
# (kontak, email, link dokumen) tidak ikut tercetak ke terminal.
PREVIEW_ROWS = 4
PREVIEW_COLS = 6
CELL_WIDTH = 26


def line(char: str = "-", width: int = 66) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def yes_no(flag: bool) -> str:
    return "YES" if flag else "NO"


def check_python() -> None:
    section("1. PYTHON ENVIRONMENT")
    version = sys.version.split()[0]
    print(f"Python version   : {version}")
    # sys.prefix != sys.base_prefix berarti kita sedang di virtual environment
    in_venv = sys.prefix != sys.base_prefix
    print(f"Virtual env aktif: {yes_no(in_venv)}")


def check_dependencies() -> bool:
    """Import setiap dependensi wajib satu per satu.

    Kita import manual (bukan pip list) supaya yang teruji adalah
    'benar-benar bisa dipakai', bukan sekadar 'terdaftar'.
    """
    section("2. DEPENDENCIES")
    required = ["pandas", "gspread", "google.auth", "dotenv", "plotly", "streamlit"]
    all_ok = True

    for module_name in required:
        try:
            __import__(module_name)
            print(f"{module_name:<16} OK")
        except ImportError as exc:
            all_ok = False
            print(f"{module_name:<16} MISSING  ({exc.msg})")

    return all_ok


def check_config() -> bool:
    section("3. CONFIGURATION (.env & credentials)")
    status = sheets.env_status()

    for env_name, is_loaded in status.items():
        print(f"{env_name} loaded: {yes_no(is_loaded)}")

    creds_ok = sheets.credentials_exist()
    print(f"credentials.json exists: {yes_no(creds_ok)}")
    print()
    print("(nilai URL dan isi credentials sengaja tidak ditampilkan)")

    return all(status.values()) and creds_ok


def check_authentication() -> bool:
    section("4. GOOGLE SERVICE ACCOUNT AUTHENTICATION")
    try:
        sheets.get_client()
    except Exception as exc:
        print(f"Authentication: FAILED")
        print(f"  {type(exc).__name__}: {exc}")
        return False

    print("Authentication: OK (read-only scopes)")
    for scope in sheets.SCOPES:
        print(f"  scope: {scope}")
    return True


def preview_worksheet(worksheet) -> None:
    """Cetak sampel kecil worksheet dalam bentuk tabel sederhana."""
    last_col = chr(ord("A") + PREVIEW_COLS - 1)
    rows = sheets.read_range(worksheet, f"A1:{last_col}{PREVIEW_ROWS}")

    if not rows:
        print("  (worksheet kosong / tidak ada nilai di range sampel)")
        return

    for row_number, row in enumerate(rows, start=1):
        cells = []
        for value in row[:PREVIEW_COLS]:
            text = value.strip().replace("\n", " ")
            if len(text) > CELL_WIDTH:
                text = text[: CELL_WIDTH - 3] + "..."
            cells.append(f"{text:<{CELL_WIDTH}}")
        print(f"  r{row_number} | " + " | ".join(cells))


def check_spreadsheet(label: str, opener) -> bool:
    print()
    line()
    print(f"{label}")
    line()
    try:
        spreadsheet = opener()
    except Exception as exc:
        print(f"Connection: FAILED")
        print(f"  {type(exc).__name__}: {exc}")
        return False

    print(f"Connection      : OK")
    print(f"Spreadsheet title: {spreadsheet.title}")

    names = sheets.list_worksheet_names(spreadsheet)
    print(f"Worksheet count : {len(names)}")
    print("Worksheet names :")
    for index, name in enumerate(names, start=1):
        print(f"  {index:>2}. {name}")
    return True


def check_connections() -> bool:
    section("5. SPREADSHEET CONNECTIONS & WORKSHEET INVENTORY")
    active_ok = check_spreadsheet("ACTIVE ESSM", sheets.open_active_essm)
    national_ok = check_spreadsheet(
        "NATIONAL MIRROR (satu-satunya sumber National)",
        sheets.open_national_mirror,
    )
    return active_ok and national_ok


def pick_sample_worksheet(worksheets: list) -> object:
    """Pilih worksheet untuk sampel.

    Prioritaskan tab yang namanya mengandung "Market Research" karena itulah
    data yang akan kita pakai di Phase 3. Kalau tidak ada, ambil tab pertama.
    Ini hanya pemilihan target sampel, BUKAN inspeksi struktur (itu Phase 2).
    """
    for worksheet in worksheets:
        if "market research" in worksheet.title.lower():
            return worksheet
    return worksheets[0]


def check_sample_read() -> bool:
    """Baca sampel dari SATU worksheet Active untuk membuktikan data terbaca."""
    section("6. SAMPLE READ - ONE ACTIVE ESSM WORKSHEET")
    try:
        spreadsheet = sheets.open_active_essm()
        worksheets = spreadsheet.worksheets()
        if not worksheets:
            print("Active ESSM tidak memiliki worksheet.")
            return False

        target = pick_sample_worksheet(worksheets)
        print(f"Worksheet sampel : {target.title}")
        print(f"Grid size        : {target.row_count} rows x {target.col_count} cols")
        print(f"Preview          : {PREVIEW_ROWS} baris pertama x {PREVIEW_COLS} kolom pertama")
        print()
        preview_worksheet(target)
    except Exception as exc:
        print(f"Sample read: FAILED")
        print(f"  {type(exc).__name__}: {exc}")
        return False

    return True


def main() -> int:
    print()
    line("#")
    print("PHASE 1 - PROJECT & CONNECTION VERIFICATION")
    line("#")

    check_python()

    results: dict[str, bool] = {}
    results["dependencies"] = check_dependencies()
    if not results["dependencies"]:
        print("\nBerhenti: install dependensi dulu (pip install -r requirements.txt).")
        return 1

    results["configuration"] = check_config()
    if not results["configuration"]:
        print("\nBerhenti: .env atau credentials.json belum lengkap.")
        return 1

    results["authentication"] = check_authentication()
    if not results["authentication"]:
        return 1

    results["connections"] = check_connections()
    results["sample_read"] = check_sample_read() if results["connections"] else False

    section("SUMMARY")
    for name, ok in results.items():
        print(f"{name:<16} {'PASS' if ok else 'FAIL'}")

    all_passed = all(results.values())
    print()
    print("PHASE 1 RESULT:", "ALL CHECKS PASSED" if all_passed else "SOME CHECKS FAILED")
    print()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
