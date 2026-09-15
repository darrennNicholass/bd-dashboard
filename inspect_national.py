"""
inspect_national.py - PHASE 4: INSPECT NATIONAL MIRROR

Struktur National mirror belum diketahui sama sekali, jadi script ini
TIDAK menebak posisi apa pun. Cara kerjanya: mencari kata kunci di
SELURUH grid, lalu melaporkan di mana saja kata itu muncul.

Yang dilaporkan per worksheet:
    A. Ukuran grid
    B. Dump baris teratas (sel yang terisi + huruf kolomnya)
    C. Peta kata kunci di seluruh grid -> di sini letak field kita
    D. Kandidat header row (baris yang memuat banyak kelompok kata kunci)
    E. Daftar lengkap kolom pada header row terpilih
    F. Profil isi kolom kandidat
    G. Penanda bulan di seluruh grid
    H. Sel bernilai persen (kandidat Sales Funnel Conversion Rate)
    I. Sel bernilai uang (kandidat revenue / in-kind value)

Script ini hanya MEMBACA dan MELAPORKAN. Tidak ada transformasi,
tidak ada dataframe, tidak ada penghapusan.

ATURAN GOVERNANCE:
    Hanya menyentuh National MIRROR (NATIONAL_SOURCE_URL).
    National ESSM asli tidak pernah diakses.

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe inspect_national.py            # National_1.1
    .venv/Scripts/python.exe inspect_national.py 1.3        # tab lain
    .venv/Scripts/python.exe inspect_national.py all        # semua tab
"""

from __future__ import annotations

import re
import sys

from src import sheets

DEFAULT_WORKSHEET = sheets.NATIONAL_PARTNER_WORKSHEET  # National_1.1

ROW_DUMP_ROWS = 40
HEADER_SCAN_ROWS = 15
MAX_CELLS_PER_ROW = 14
CELL_WIDTH = 22
SAMPLE_VALUES = 6
MAX_HITS_PER_KEYWORD = 12

SPREADSHEET_ERRORS = frozenset(
    {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#ERROR!"}
)

INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

# Kelompok kata kunci untuk SEMUA tab National (1.1, 1.3, 1.4).
# Sengaja satu set: kita belum tahu field mana ada di tab mana.
KEYWORD_GROUPS: dict[str, tuple[str, ...]] = {
    # National 1.1 - partner & sales funnel
    "partner_name": ("partner",),
    # Di sumber tertulis "STAKEHODLER GROUPING" (typo). Dicocokkan keduanya.
    "stakeholder": ("stakeholder", "stakehodler", "grouping"),
    "active_status": ("active", "inactive"),
    "proposal": ("proposal",),
    "mom": ("mom", "minutes of meeting"),
    "loa": ("loa", "contract", "moa", "agreement"),
    "invoice": ("invoice", "inv."),
    "signed": ("signed",),
    "month_end": ("month end", "end date", "expired", "expiry", "ending"),
    "conversion_rate": ("conversion", "funnel"),
    # National 1.3 / 1.4 - revenue & in-kind
    "month": ("month",),
    "date_received": ("date received", "received"),
    "revenue": ("revenue",),
    "in_kind": ("in-kind", "in kind", "inkind"),
    "value": ("value",),
    "type": ("type",),
    "proof": ("proof",),
}

MONTH_TOKENS = frozenset(
    {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "januari", "februari", "maret", "mei", "juni",
        "juli", "agustus", "oktober", "nopember", "desember",
    }
)

PERCENT_PATTERN = re.compile(r"^\d{1,3}([.,]\d+)?\s*%$")
MONEY_PATTERN = re.compile(r"^(rp|idr)\s*[\d.,]+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------


def line(char: str = "-", width: int = 78) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def clean(value: str) -> str:
    if value is None:
        return ""
    return " ".join(INVISIBLE_CHARS.sub("", str(value)).split())


def short(value: str, width: int = CELL_WIDTH) -> str:
    text = clean(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def is_error(value: str) -> bool:
    return clean(value).upper() in SPREADSHEET_ERRORS


def has_value(value: str) -> bool:
    text = clean(value)
    return bool(text) and text.upper() not in SPREADSHEET_ERRORS


def normalize(value: str) -> str:
    return clean(value).lower()


def cell(grid: list[list[str]], row_index: int, col_index: int) -> str:
    if row_index >= len(grid):
        return ""
    row = grid[row_index]
    return row[col_index] if col_index < len(row) else ""


def months_in_cell(value: str) -> list[str]:
    words = re.findall(r"[a-z]+", normalize(value))
    return [w for w in words if w in MONTH_TOKENS]


# ---------------------------------------------------------------------------
# A + B
# ---------------------------------------------------------------------------


def show_size(worksheet, grid: list[list[str]]) -> None:
    widest = max((len(row) for row in grid), default=0)
    print(f"Grid deklarasi Sheets  : {worksheet.row_count} rows x {worksheet.col_count} cols")
    print(f"Grid terisi (trim)     : {len(grid)} rows x {widest} cols")
    filled = sum(1 for row in grid for value in row if has_value(value))
    errors = sum(1 for row in grid for value in row if is_error(value))
    print(f"Sel berisi data        : {filled}")
    print(f"Sel error (#REF! dsb.) : {errors}")


def show_top_rows(grid: list[list[str]]) -> None:
    print()
    print(f"B. DUMP BARIS 1-{ROW_DUMP_ROWS} (hanya sel terisi)")
    line()
    for row_index in range(min(ROW_DUMP_ROWS, len(grid))):
        row = grid[row_index]
        filled = [
            f"{sheets.column_letter(i + 1)}={short(value)!r}"
            for i, value in enumerate(row)
            if clean(value)
        ]
        label = f"row {row_index + 1:>3} ({len(filled):>3} terisi)"
        if not filled:
            print(f"{label} : (kosong)")
            continue
        shown = filled[:MAX_CELLS_PER_ROW]
        suffix = f"  ... +{len(filled) - len(shown)} lagi" if len(filled) > len(shown) else ""
        print(f"{label} : " + "  ".join(shown) + suffix)


def show_rows_full(grid: list[list[str]], row_numbers: list[int], label: str) -> None:
    """Cetak baris tertentu SECARA PENUH, tanpa dipotong.

    Dipakai untuk membaca baris header berlapis dan baris section header,
    di mana justru sel-sel di kolom jauh yang penting.
    """
    print()
    print(label)
    line()
    for row_number in row_numbers:
        row_index = row_number - 1
        if row_index >= len(grid):
            continue
        filled = [
            f"{sheets.column_letter(i + 1)}={clean(value)!r}"
            for i, value in enumerate(grid[row_index])
            if clean(value)
        ]
        print(f"row {row_number} ({len(filled)} terisi):")
        for chunk_start in range(0, len(filled), 6):
            print("    " + "  ".join(filled[chunk_start : chunk_start + 6]))
        print()


def find_section_header_rows(grid: list[list[str]]) -> list[int]:
    """Nomor baris (1-based) yang berisi penanda bulan di kolom NO (B)."""
    rows: list[int] = []
    for row_index, row in enumerate(grid):
        value = row[1] if len(row) > 1 else ""
        if months_in_cell(value):
            rows.append(row_index + 1)
    return rows


# ---------------------------------------------------------------------------
# C. Peta kata kunci di seluruh grid
# ---------------------------------------------------------------------------


def map_keywords(grid: list[list[str]]) -> dict[str, list[tuple[int, int, str]]]:
    """Cari setiap kata kunci di SELURUH grid, bukan cuma baris 1.

    Ini inti dari 'jangan menebak posisi': kita biarkan data yang
    menunjukkan di mana field-nya berada.
    """
    print()
    print("C. PETA KATA KUNCI DI SELURUH GRID")
    line()

    hits: dict[str, list[tuple[int, int, str]]] = {group: [] for group in KEYWORD_GROUPS}

    for row_index, row in enumerate(grid):
        for col_index, value in enumerate(row):
            text = normalize(value)
            if not text or len(text) > 60:
                continue
            for group, keywords in KEYWORD_GROUPS.items():
                if any(keyword in text for keyword in keywords):
                    hits[group].append((row_index + 1, col_index + 1, clean(value)))

    for group, found in hits.items():
        if not found:
            print(f"{group:<16}: tidak ditemukan")
            continue
        print(f"{group:<16}: {len(found)} kemunculan")
        for row_number, col_number, value in found[:MAX_HITS_PER_KEYWORD]:
            letter = sheets.column_letter(col_number)
            print(f"    {letter}{row_number:<6} {short(value, 46)!r}")
        if len(found) > MAX_HITS_PER_KEYWORD:
            print(f"    ... +{len(found) - MAX_HITS_PER_KEYWORD} lagi")

    return hits


# ---------------------------------------------------------------------------
# D + E. Header row
# ---------------------------------------------------------------------------


def detect_header_row(grid: list[list[str]]) -> int:
    """Kandidat header row = baris yang memuat paling banyak kelompok kata kunci."""
    print()
    print("D. KANDIDAT HEADER ROW")
    line()

    scored: list[tuple[int, int, list[str]]] = []
    for row_index in range(min(HEADER_SCAN_ROWS, len(grid))):
        normalized = [normalize(v) for v in grid[row_index]]
        matched = [
            group
            for group, keywords in KEYWORD_GROUPS.items()
            if any(keyword in name for keyword in keywords for name in normalized)
        ]
        if matched:
            scored.append((len(matched), row_index, matched))

    if not scored:
        print(f"Tidak ada kandidat di {HEADER_SCAN_ROWS} baris pertama. Fallback: baris 1.")
        return 0

    for count, row_index, matched in sorted(scored, key=lambda item: -item[0]):
        print(f"  baris {row_index + 1:>3} : {count} kelompok -> {matched}")

    best_count = max(count for count, _, _ in scored)
    best_index = min(row_index for count, row_index, _ in scored if count == best_count)
    print()
    print(f"Header row dipakai : baris {best_index + 1}")
    return best_index


def show_headers(headers: list[str]) -> None:
    print()
    print("E. DAFTAR KOLOM PADA HEADER ROW")
    line()
    print(f"{'idx':>4}  {'col':<4}  header")
    for index, name in enumerate(headers, start=1):
        if not clean(name):
            continue
        print(f"{index:>4}  {sheets.column_letter(index):<4}  {short(name, 52)}")


# ---------------------------------------------------------------------------
# F. Profil kolom
# ---------------------------------------------------------------------------


def profile_columns(grid: list[list[str]], header_index: int, headers: list[str]) -> None:
    """Profilkan SETIAP kolom yang punya nama header, supaya isinya terlihat."""
    print()
    print("F. PROFIL ISI TIAP KOLOM (area di bawah header row)")
    line()

    for col_index, name in enumerate(headers):
        if not clean(name):
            continue

        values = [
            cell(grid, row_index, col_index)
            for row_index in range(header_index + 1, len(grid))
        ]
        real = [clean(v) for v in values if has_value(v)]
        if not real:
            print(f"{sheets.column_letter(col_index + 1):<4} {short(name, 30):<32} (kosong)")
            continue

        distinct = list(dict.fromkeys(real))
        letter = sheets.column_letter(col_index + 1)
        print(f"{letter:<4} {short(name, 30):<32} terisi={len(real):<5} unik={len(distinct):<5}")
        print(f"     contoh: {[short(v, 24) for v in distinct[:SAMPLE_VALUES]]}")


# ---------------------------------------------------------------------------
# G + H + I
# ---------------------------------------------------------------------------


def find_month_markers(grid: list[list[str]]) -> None:
    print()
    print("G. PENANDA BULAN DI SELURUH GRID")
    line()

    found: list[tuple[str, str, str]] = []
    for row_index, row in enumerate(grid):
        for col_index, value in enumerate(row):
            text = clean(value)
            if not text or len(text) > 30:
                continue
            months = months_in_cell(text)
            if months:
                letter = sheets.column_letter(col_index + 1)
                found.append((f"{letter}{row_index + 1}", months[0], text))

    print(f"Total sel memuat nama bulan : {len(found)}")
    for position, month, text in found[:40]:
        print(f"    {position:<8} {month:<10} {text!r}")
    if len(found) > 40:
        print(f"    ... +{len(found) - 40} lagi")


def find_percent_cells(grid: list[list[str]]) -> None:
    print()
    print("H. SEL BERNILAI PERSEN (kandidat Sales Funnel Conversion Rate)")
    line()

    found = []
    for row_index, row in enumerate(grid):
        for col_index, value in enumerate(row):
            text = clean(value)
            if PERCENT_PATTERN.match(text):
                letter = sheets.column_letter(col_index + 1)
                found.append((f"{letter}{row_index + 1}", text))

    print(f"Total sel berformat persen : {len(found)}")
    for position, text in found[:40]:
        print(f"    {position:<8} {text}")
    if len(found) > 40:
        print(f"    ... +{len(found) - 40} lagi")


def find_money_cells(grid: list[list[str]]) -> None:
    print()
    print("I. SEL BERNILAI UANG (kandidat revenue / in-kind value)")
    line()

    found = []
    for row_index, row in enumerate(grid):
        for col_index, value in enumerate(row):
            text = clean(value)
            if MONEY_PATTERN.match(text):
                letter = sheets.column_letter(col_index + 1)
                found.append((f"{letter}{row_index + 1}", text))

    print(f"Total sel berformat uang : {len(found)}")
    for position, text in found[:30]:
        print(f"    {position:<8} {text}")
    if len(found) > 30:
        print(f"    ... +{len(found) - 30} lagi")


def profile_partner_records(grid: list[list[str]]) -> None:
    """Hitung baris record partner memakai aturan yang sama seperti df_mr.

    Aturan: kolom NO berisi angka murni DAN nama partner terisi.
    Kolom dicari lewat nama header di baris 1, bukan posisi tetap.
    """
    print()
    print("J. PROFIL BARIS RECORD PARTNER")
    line()

    if not grid:
        return

    header_row1 = [normalize(v) for v in grid[0]]

    def find_col(expected: str) -> int | None:
        try:
            return header_row1.index(expected)
        except ValueError:
            return None

    columns = {
        "no": find_col("no"),
        "partner_name": find_col("partner's name"),
        "stakeholder": find_col("stakehodler grouping"),
        "active_status": find_col("status (for new sales)"),
    }
    for field, index in columns.items():
        position = sheets.column_letter(index + 1) if index is not None else "TIDAK KETEMU"
        print(f"  {field:<14} -> {position}")

    if columns["no"] is None or columns["partner_name"] is None:
        print("  Tidak bisa lanjut: kolom NO atau nama partner tidak ditemukan.")
        return

    current_month = None
    per_month: dict[str, int] = {}
    stakeholders: dict[str, int] = {}
    statuses: dict[str, int] = {}
    total = 0

    for row_index in range(len(grid)):
        no_value = cell(grid, row_index, columns["no"])
        found = months_in_cell(no_value)
        if found:
            current_month = found[0]
            continue

        partner_value = cell(grid, row_index, columns["partner_name"])
        if not (re.fullmatch(r"\d+", clean(no_value)) and has_value(partner_value)):
            continue

        total += 1
        label = current_month or "(tanpa bulan)"
        per_month[label] = per_month.get(label, 0) + 1

        if columns["stakeholder"] is not None:
            value = clean(cell(grid, row_index, columns["stakeholder"])) or "(kosong)"
            stakeholders[value] = stakeholders.get(value, 0) + 1
        if columns["active_status"] is not None:
            value = clean(cell(grid, row_index, columns["active_status"])) or "(kosong)"
            statuses[value] = statuses.get(value, 0) + 1

    print()
    print(f"  Total baris record partner : {total}")
    print()
    print("  Per bulan:")
    for month, count in per_month.items():
        print(f"    {month:<14} {count:>5}")
    print()
    print("  Distribusi STAKEHODLER GROUPING:")
    for value, count in sorted(stakeholders.items(), key=lambda item: -item[1]):
        print(f"    {short(value, 34):<36} {count:>5}")
    print()
    print("  Distribusi Status (For new sales):")
    for value, count in sorted(statuses.items(), key=lambda item: -item[1]):
        print(f"    {short(value, 34):<36} {count:>5}")


def show_funnel_rates(grid: list[list[str]]) -> None:
    """Tarik nilai persen funnel dari baris summary dan tiap section bulan.

    Kolom funnel dikenali dari sub-header di baris 2 (mis. '5. Contract Signed').
    Nilai persennya diambil apa adanya - TIDAK dihitung ulang.
    """
    print()
    print("K. NILAI PERSEN FUNNEL PER BULAN (diambil apa adanya dari sumber)")
    line()

    if len(grid) < 3:
        return

    stage_columns = [
        (index, clean(name))
        for index, name in enumerate(grid[1])
        if re.match(r"^\d\.\s", clean(name))
    ]
    if not stage_columns:
        print("  Tidak ada kolom tahap funnel di baris 2.")
        return

    print("  Kolom tahap funnel (dari sub-header baris 2):")
    for index, name in stage_columns:
        print(f"    {sheets.column_letter(index + 1):<4} {name}")

    rows_of_interest = [(3, "TOTAL (baris 3)")]
    for row_number in find_section_header_rows(grid):
        months = months_in_cell(cell(grid, row_number - 1, 1))
        rows_of_interest.append((row_number, months[0] if months else "?"))

    print()
    header = f"  {'baris/bulan':<20}" + "".join(
        f"{short(name, 11):>13}" for _, name in stage_columns
    )
    print(header)
    line()
    for row_number, label in rows_of_interest:
        values = "".join(
            f"{clean(cell(grid, row_number - 1, index)) or '-':>13}"
            for index, _ in stage_columns
        )
        print(f"  {label:<20}{values}")


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------


def inspect_worksheet(worksheet) -> None:
    section(f"WORKSHEET: {worksheet.title}")

    grid = sheets.read_worksheet_values(worksheet)
    print("A. UKURAN GRID")
    line()
    show_size(worksheet, grid)

    if not grid:
        print("\nWorksheet kosong.")
        return

    show_top_rows(grid)
    show_rows_full(grid, [1, 2, 3], "B2. BARIS HEADER BERLAPIS (penuh, tanpa dipotong)")

    section_rows = find_section_header_rows(grid)
    if section_rows:
        show_rows_full(
            grid,
            section_rows[:3],
            "B3. BARIS SECTION HEADER BULAN (penuh) - 3 pertama",
        )

    map_keywords(grid)
    header_index = detect_header_row(grid)
    headers = grid[header_index]
    show_headers(headers)
    profile_columns(grid, header_index, headers)
    find_month_markers(grid)
    find_percent_cells(grid)
    find_money_cells(grid)
    profile_partner_records(grid)
    show_funnel_rates(grid)


def main() -> int:
    print()
    line("#")
    print("PHASE 4 - INSPECT NATIONAL MIRROR")
    line("#")
    print("Sumber: National MIRROR saja. National ESSM asli tidak diakses.")
    print("Mode  : baca & laporkan. Tidak ada transformasi.")

    try:
        spreadsheet = sheets.open_national_mirror()
    except Exception as exc:
        print(f"\nGagal membuka National Mirror: {type(exc).__name__}: {exc}")
        return 1

    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WORKSHEET
    worksheets = spreadsheet.worksheets()

    if target.lower() != "all":
        worksheets = [ws for ws in worksheets if target.lower() in ws.title.lower()]
        if not worksheets:
            available = ", ".join(sheets.list_worksheet_names(spreadsheet))
            print(f"\nTidak ada tab yang cocok dengan {target!r}. Tersedia: {available}")
            return 1

    print(f"Target: {[ws.title for ws in worksheets]}")

    for worksheet in worksheets:
        inspect_worksheet(worksheet)

    return 0


if __name__ == "__main__":
    sys.exit(main())
