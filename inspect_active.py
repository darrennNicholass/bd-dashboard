"""
inspect_active.py - PHASE 2: INSPECT ACTIVE ESSM

Tujuan: MENGETAHUI struktur asli ketiga tab Market Research sebelum
menulis satu baris pun kode transformasi.

Yang dijawab script ini, per tab:
    A. Bentuk baris-baris teratas  -> di baris berapa header sebenarnya?
    B. Header row mana yang dipakai
    C. Daftar lengkap nama kolom pada header row itu
    D. Kolom mana yang berisi Partner Name / PIC From AIESEC / Date of MR
    E. Profil isi kolom target     -> berapa terisi, berapa #REF!, contoh nilai
    F. Uji asumsi "#REF! = baris kosong"
    G. Format tanggal: tampilan (FORMATTED) vs nilai mentah (UNFORMATTED)

Script ini TIDAK melakukan cleaning, TIDAK membuat dataframe, dan
TIDAK menghapus apa pun. Murni membaca dan melaporkan.

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe inspect_active.py
"""

from __future__ import annotations

import re
import sys

from src import sheets

# Berapa baris teratas yang kita CETAK untuk memahami bentuk sheet.
ROW_DUMP_ROWS = 45

# Berapa baris teratas yang dicari sebagai kandidat header row.
HEADER_SCAN_ROWS = 12

# Nilai error Google Sheets. Menurut BD, #REF! di tab MR berarti
# "baris ini tidak ada recordnya", BUKAN data yang rusak.
# Asumsi itu diuji di bagian F.
SPREADSHEET_ERRORS = (
    "#REF!",
    "#N/A",
    "#VALUE!",
    "#DIV/0!",
    "#NAME?",
    "#NULL!",
    "#ERROR!",
)

# Kata kunci untuk menemukan kolom yang kita butuhkan.
# Sengaja longgar: lebih baik menemukan beberapa kandidat lalu dilaporkan,
# daripada memakai satu tebakan diam-diam.
TARGET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "partner_name": ("partner",),
    "pic_aiesec": ("pic",),
    "date_of_mr": ("date",),
}

MAX_CELLS_PER_ROW = 12
CELL_WIDTH = 20
SAMPLE_VALUES = 5

# Nama bulan (Inggris & Indonesia) untuk mengenali baris pemisah section.
MONTH_TOKENS = frozenset(
    {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "januari", "februari", "maret", "mei", "juni",
        "juli", "agustus", "oktober", "nopember", "desember",
    }
)

# Kolom "NO" (nomor urut record). Dipakai untuk membedakan baris RECORD
# dari baris struktural (legend, summary, section header).
NO_COLUMN_INDEX = 1  # 0-based -> kolom B


# ---------------------------------------------------------------------------
# Util tampilan
# ---------------------------------------------------------------------------


def line(char: str = "-", width: int = 74) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def short(value: str, width: int = CELL_WIDTH) -> str:
    """Rapikan nilai sel untuk ditampilkan di terminal."""
    text = " ".join(value.split())
    if len(text) > width:
        text = text[: width - 3] + "..."
    return text


def is_blank(value: str) -> bool:
    return not value.strip()


def is_error(value: str) -> bool:
    return value.strip().upper() in SPREADSHEET_ERRORS


def is_real_value(value: str) -> bool:
    """Nilai yang benar-benar data: tidak kosong dan bukan error Sheets."""
    return not is_blank(value) and not is_error(value)


def normalize_header(value: str) -> str:
    """Samakan bentuk header supaya bisa dicocokkan: huruf kecil, spasi rapat."""
    return " ".join(value.split()).lower()


def cell(grid: list[list[str]], row_index: int, col_index: int) -> str:
    """Ambil sel dengan aman. Baris di Google Sheets bisa lebih pendek dari header."""
    if row_index >= len(grid):
        return ""
    row = grid[row_index]
    if col_index >= len(row):
        return ""
    return row[col_index]


# ---------------------------------------------------------------------------
# A + B. Struktur baris atas & deteksi header row
# ---------------------------------------------------------------------------


def show_top_rows(grid: list[list[str]]) -> None:
    print()
    print(f"A. BENTUK BARIS 1-{ROW_DUMP_ROWS} (hanya sel yang terisi)")
    line()
    for row_index in range(min(ROW_DUMP_ROWS, len(grid))):
        row = grid[row_index]
        filled = [
            f"{sheets.column_letter(i + 1)}={short(value)!r}"
            for i, value in enumerate(row)
            if not is_blank(value)
        ]
        label = f"row {row_index + 1:>3} ({len(filled):>2} terisi)"
        if not filled:
            print(f"{label} : (kosong)")
            continue
        shown = filled[:MAX_CELLS_PER_ROW]
        suffix = f"  ... +{len(filled) - len(shown)} lagi" if len(filled) > len(shown) else ""
        print(f"{label} : " + "  ".join(shown) + suffix)


def detect_header_row(grid: list[list[str]]) -> int:
    """Cari header row berdasarkan KATA KUNCI, bukan jumlah sel terisi.

    Kenapa bukan 'baris paling penuh': di ESSM ada baris ringkasan angka
    yang justru paling penuh, dan itu bukan header.

    Baris dianggap kandidat header kalau mengandung nama-nama kolom yang
    kita cari (partner / pic / date). Semakin banyak kelompok kata kunci
    yang cocok, semakin kuat kandidatnya.
    Return: index 0-based.
    """
    print()
    print("B. DETEKSI HEADER ROW (berbasis kata kunci)")
    line()

    scored: list[tuple[int, int, list[str]]] = []
    for row_index in range(min(HEADER_SCAN_ROWS, len(grid))):
        normalized = [normalize_header(v) for v in grid[row_index]]
        matched = [
            target
            for target, keywords in TARGET_KEYWORDS.items()
            if any(keyword in name for keyword in keywords for name in normalized)
        ]
        if matched:
            scored.append((len(matched), row_index, matched))

    if not scored:
        print("Tidak ada baris yang mengandung kata kunci target.")
        print("Fallback: memakai baris 1.")
        return 0

    for count, row_index, matched in scored:
        print(f"  baris {row_index + 1:>2} : cocok {count} kelompok -> {matched}")

    # Skor tertinggi menang; kalau seri, ambil baris paling atas.
    best_count = max(count for count, _, _ in scored)
    best_index = min(row_index for count, row_index, _ in scored if count == best_count)

    print()
    print(f"Header row dipakai : baris {best_index + 1}")
    return best_index


# ---------------------------------------------------------------------------
# C. Daftar kolom
# ---------------------------------------------------------------------------


def show_headers(headers: list[str]) -> None:
    print()
    print("C. DAFTAR KOLOM PADA HEADER ROW (kolom kosong dilewati)")
    line()
    print(f"{'idx':>4}  {'col':<4}  header")
    for index, name in enumerate(headers, start=1):
        if is_blank(name):
            continue
        print(f"{index:>4}  {sheets.column_letter(index):<4}  {short(name, 46)}")


# ---------------------------------------------------------------------------
# D. Kandidat kolom target
# ---------------------------------------------------------------------------


def find_candidates(headers: list[str]) -> dict[str, list[int]]:
    """Cari kolom yang namanya mengandung kata kunci. Return index 0-based."""
    candidates: dict[str, list[int]] = {}
    for target, keywords in TARGET_KEYWORDS.items():
        matches = [
            col_index
            for col_index, name in enumerate(headers)
            if any(keyword in normalize_header(name) for keyword in keywords)
        ]
        candidates[target] = matches

    print()
    print("D. KANDIDAT KOLOM TARGET")
    line()
    for target, matches in candidates.items():
        if not matches:
            print(f"{target:<14}: TIDAK ADA KANDIDAT  <-- perlu diperiksa manual")
            continue
        described = ", ".join(
            f"{sheets.column_letter(i + 1)}={short(headers[i], 34)!r}" for i in matches
        )
        flag = "" if len(matches) == 1 else "   <-- lebih dari satu kandidat"
        print(f"{target:<14}: {described}{flag}")
    return candidates


# ---------------------------------------------------------------------------
# E. Profil kolom
# ---------------------------------------------------------------------------


def profile_column(grid: list[list[str]], header_index: int, col_index: int) -> dict:
    """Hitung statistik sederhana satu kolom pada area data."""
    values = [
        cell(grid, row_index, col_index)
        for row_index in range(header_index + 1, len(grid))
    ]
    real = [v for v in values if is_real_value(v)]

    samples: list[str] = []
    for value in real:
        cleaned = " ".join(value.split())
        if cleaned not in samples:
            samples.append(cleaned)
        if len(samples) >= SAMPLE_VALUES:
            break

    return {
        "scanned": len(values),
        "real": len(real),
        "blank": sum(1 for v in values if is_blank(v)),
        "error": sum(1 for v in values if is_error(v)),
        "distinct_real": len({" ".join(v.split()) for v in real}),
        "samples": samples,
    }


def show_column_profiles(
    grid: list[list[str]],
    header_index: int,
    headers: list[str],
    candidates: dict[str, list[int]],
) -> dict[str, int | None]:
    """Profilkan setiap kandidat dan pilih kolom terbaik per target."""
    print()
    print("E. PROFIL KOLOM KANDIDAT (area data = di bawah header row)")
    line()

    chosen: dict[str, int | None] = {}
    for target, matches in candidates.items():
        if not matches:
            chosen[target] = None
            continue

        best_index = None
        best_real = -1
        for col_index in matches:
            stats = profile_column(grid, header_index, col_index)
            letter = sheets.column_letter(col_index + 1)
            print(f"[{target}] kolom {letter} = {short(headers[col_index], 40)!r}")
            print(
                f"    baris discan {stats['scanned']:>5} |"
                f" berisi data {stats['real']:>5} |"
                f" kosong {stats['blank']:>5} |"
                f" error/#REF! {stats['error']:>5}"
            )
            print(f"    nilai unik   {stats['distinct_real']:>5}")
            print(f"    contoh       {stats['samples']}")
            print()
            if stats["real"] > best_real:
                best_index, best_real = col_index, stats["real"]

        chosen[target] = best_index
        if len(matches) > 1:
            print(
                f"    -> untuk {target} dipilih kolom "
                f"{sheets.column_letter(best_index + 1)} (paling banyak berisi data)"
            )
            print()

    return chosen


# ---------------------------------------------------------------------------
# F. Uji asumsi "#REF! = baris kosong"
# ---------------------------------------------------------------------------


def test_ref_assumption(
    grid: list[list[str]],
    header_index: int,
    chosen: dict[str, int | None],
) -> None:
    """Cek apakah baris ber-#REF! benar-benar tidak punya data sama sekali.

    Kalau ada baris yang #REF! di kolom target TAPI berisi data di kolom lain,
    berarti #REF! bukan sekadar 'baris kosong' dan harus dibahas ulang.
    """
    print()
    print("F. UJI ASUMSI: '#REF! berarti baris tanpa record'")
    line()

    target_cols = [i for i in chosen.values() if i is not None]
    if not target_cols:
        print("Dilewati: tidak ada kolom target yang teridentifikasi.")
        return

    fully_blank = 0
    error_and_otherwise_empty = 0
    error_but_has_other_data = []
    has_real_target_data = 0

    for row_index in range(header_index + 1, len(grid)):
        row_values = grid[row_index]
        target_values = [cell(grid, row_index, i) for i in target_cols]

        if all(is_blank(v) for v in row_values):
            fully_blank += 1
            continue

        if any(is_real_value(v) for v in target_values):
            has_real_target_data += 1
            continue

        # Kolom target tidak punya data nyata. Apakah kolom LAIN punya?
        others_with_data = [
            (sheets.column_letter(i + 1), value)
            for i, value in enumerate(row_values)
            if i not in target_cols and is_real_value(value)
        ]
        if others_with_data:
            error_but_has_other_data.append((row_index + 1, others_with_data[:3]))
        else:
            error_and_otherwise_empty += 1

    print(f"Baris benar-benar kosong total        : {fully_blank}")
    print(f"Baris dengan data di kolom target     : {has_real_target_data}")
    print(f"Baris kosong/#REF! di semua kolom     : {error_and_otherwise_empty}")
    print(f"Baris TANPA data target tapi ADA data")
    print(f"  di kolom lain                       : {len(error_but_has_other_data)}")

    if error_but_has_other_data:
        print()
        print("  ASUMSI TIDAK BERSIH - contoh baris yang perlu dilihat manual:")
        for row_number, examples in error_but_has_other_data[:5]:
            described = ", ".join(f"{col}={short(val)!r}" for col, val in examples)
            print(f"    baris {row_number}: {described}")
    else:
        print()
        print("  ASUMSI TERKONFIRMASI untuk tab ini:")
        print("  setiap baris tanpa data target juga tidak punya data di kolom lain.")


# ---------------------------------------------------------------------------
# G. Format tanggal
# ---------------------------------------------------------------------------


def compare_date_rendering(worksheet, header_index: int, date_col: int | None) -> None:
    """Bandingkan nilai tanggal versi tampilan vs nilai mentah Sheets.

    Kenapa penting: kalau kolomnya benar-benar tipe DATE, UNFORMATTED akan
    memberi angka serial (mis. 46071). Kalau isinya teks bebas, UNFORMATTED
    tetap teks. Ini menentukan cara parsing di Phase 3.
    """
    print()
    print("G. FORMAT KOLOM TANGGAL: FORMATTED vs UNFORMATTED")
    line()

    if date_col is None:
        print("Dilewati: kolom tanggal belum teridentifikasi.")
        return

    letter = sheets.column_letter(date_col + 1)
    start = header_index + 2  # baris data pertama, 1-based
    end = start + 60
    a1_range = f"{letter}{start}:{letter}{end}"

    formatted = sheets.read_range(worksheet, a1_range)
    unformatted = sheets.read_range(worksheet, a1_range, "UNFORMATTED_VALUE")

    def flatten(rows: list[list[str]]) -> list[str]:
        return [row[0] if row else "" for row in rows]

    formatted_values = flatten(formatted)
    unformatted_values = flatten(unformatted)

    print(f"Kolom {letter}, range {a1_range}")
    print()
    print(f"  {'FORMATTED (tampilan)':<28} | UNFORMATTED (mentah)")
    line()
    shown = 0
    for display, raw in zip(formatted_values, unformatted_values):
        if not is_real_value(display) and not is_real_value(str(raw)):
            continue
        print(f"  {short(str(display), 26):<28} | {short(str(raw), 26)}")
        shown += 1
        if shown >= SAMPLE_VALUES * 2:
            break

    if shown == 0:
        print("  (tidak ada nilai tanggal pada range ini)")


# ---------------------------------------------------------------------------
# H. Struktur section bulan & baris record
# ---------------------------------------------------------------------------


def months_in_cell(value: str) -> list[str]:
    """Ambil nama bulan yang muncul sebagai KATA UTUH dalam sebuah sel.

    Dicocokkan per kata, bukan substring, supaya 'Mayora' tidak
    salah dikenali sebagai bulan 'May'.
    """
    words = re.findall(r"[a-z]+", value.lower())
    return [word for word in words if word in MONTH_TOKENS]


def is_record_number(value: str) -> bool:
    """True kalau sel kolom NO berisi angka urut murni, mis. '1', '27'."""
    return bool(re.fullmatch(r"\d+", value.strip()))


def analyze_sections(
    grid: list[list[str]],
    header_index: int,
    chosen: dict[str, int | None],
) -> None:
    """Petakan section bulan dan hitung baris record di dalam tiap section.

    Hipotesis yang diuji:
        - sheet disusun bertumpuk per bulan, dipisahkan baris penanda bulan
        - baris RECORD = kolom NO berisi angka DAN nama partner terisi
        - baris lain (legend / summary / section header) bukan record
    """
    print()
    print("H. STRUKTUR SECTION BULAN & IDENTIFIKASI BARIS RECORD")
    line()

    partner_col = chosen.get("partner_name")
    pic_col = chosen.get("pic_aiesec")
    date_col = chosen.get("date_of_mr")

    if partner_col is None:
        print("Dilewati: kolom nama partner belum teridentifikasi.")
        return

    # 1. Temukan semua baris penanda bulan di kolom NO.
    markers: list[tuple[int, str, str]] = []
    for row_index in range(header_index, len(grid)):
        value = cell(grid, row_index, NO_COLUMN_INDEX)
        found = months_in_cell(value)
        if found:
            markers.append((row_index, found[0], " ".join(value.split())))

    print(f"Baris penanda bulan ditemukan di kolom {sheets.column_letter(NO_COLUMN_INDEX + 1)}: {len(markers)}")
    for row_index, month, raw in markers:
        print(f"  baris {row_index + 1:>5} : bulan={month:<10} teks asli={raw!r}")

    if not markers:
        print("  Tidak ada penanda bulan. Sheet mungkin datar (flat).")
        return

    # 2. Hitung record per section. Section berakhir di penanda bulan berikutnya.
    print()
    print("Jumlah baris record per section")
    print("  (record = kolom NO berisi angka DAN nama partner terisi)")
    line()
    print(f"  {'bulan':<12} {'baris':<16} {'record':>7} {'ada PIC':>8} {'ada tanggal':>12}")

    boundaries = [row for row, _, _ in markers] + [len(grid)]
    total_records = 0
    total_with_pic = 0
    total_with_date = 0

    for position, (marker_row, month, _) in enumerate(markers):
        start = marker_row + 1
        end = boundaries[position + 1]

        records = 0
        with_pic = 0
        with_date = 0
        for row_index in range(start, end):
            no_value = cell(grid, row_index, NO_COLUMN_INDEX)
            partner_value = cell(grid, row_index, partner_col)
            if not (is_record_number(no_value) and is_real_value(partner_value)):
                continue
            records += 1
            if pic_col is not None and is_real_value(cell(grid, row_index, pic_col)):
                with_pic += 1
            if date_col is not None and is_real_value(cell(grid, row_index, date_col)):
                with_date += 1

        total_records += records
        total_with_pic += with_pic
        total_with_date += with_date
        span = f"{start + 1}-{end}"
        print(f"  {month:<12} {span:<16} {records:>7} {with_pic:>8} {with_date:>12}")

    line()
    print(f"  {'TOTAL':<12} {'':<16} {total_records:>7} {total_with_pic:>8} {total_with_date:>12}")


def audit_date_column(
    grid: list[list[str]],
    header_index: int,
    headers: list[str],
    chosen: dict[str, int | None],
) -> None:
    """Tampilkan SETIAP nilai nyata di kolom tanggal, apa adanya.

    Tujuannya menjawab satu pertanyaan: apakah DATE OF MR benar-benar diisi
    oleh tim, dan kalau diisi, formatnya seperti apa.
    """
    print()
    print("I. AUDIT ISI KOLOM DATE OF MR")
    line()

    date_col = chosen.get("date_of_mr")
    partner_col = chosen.get("partner_name")
    if date_col is None:
        print("Dilewati: kolom tanggal belum teridentifikasi.")
        return

    letter = sheets.column_letter(date_col + 1)
    print(f"Kolom {letter} = {short(headers[date_col], 40)!r}")
    print()

    found = []
    for row_index in range(header_index + 1, len(grid)):
        value = cell(grid, row_index, date_col)
        if not is_real_value(value):
            continue
        no_value = cell(grid, row_index, NO_COLUMN_INDEX)
        partner_value = cell(grid, row_index, partner_col) if partner_col is not None else ""
        is_record = is_record_number(no_value) and is_real_value(partner_value)
        found.append((row_index + 1, " ".join(value.split()), is_record))

    records_with_date = [item for item in found if item[2]]
    others = [item for item in found if not item[2]]

    print(f"Total sel terisi di kolom ini      : {len(found)}")
    print(f"  - pada baris RECORD              : {len(records_with_date)}")
    print(f"  - pada baris NON-record          : {len(others)}")
    print("    (baris non-record = summary/section header, angkanya bukan tanggal)")
    print()

    if records_with_date:
        print("Nilai tanggal pada baris record (maks 25):")
        for row_number, value, _ in records_with_date[:25]:
            print(f"    baris {row_number:>5} : {value!r}")
    else:
        print("TIDAK ADA satu pun baris record yang punya tanggal.")

    if others:
        print()
        print("Contoh nilai pada baris non-record (maks 8):")
        for row_number, value, _ in others[:8]:
            print(f"    baris {row_number:>5} : {value!r}")


def extract_records_for_inspection(
    grid: list[list[str]],
    header_index: int,
    chosen: dict[str, int | None],
) -> list[dict]:
    """Ambil record memakai aturan hasil inspeksi, HANYA untuk keperluan audit.

    Ini bukan df_mr. Tujuannya satu: membandingkan isi antar tab supaya
    kita tahu apakah ketiga tab boleh digabung tanpa menghitung ganda.

    Aturan:
        month   = penanda bulan terdekat DI ATAS baris
        record  = kolom NO berisi angka DAN nama partner terisi
    """
    partner_col = chosen.get("partner_name")
    pic_col = chosen.get("pic_aiesec")
    date_col = chosen.get("date_of_mr")
    if partner_col is None:
        return []

    records: list[dict] = []
    current_month = None

    for row_index in range(header_index, len(grid)):
        no_value = cell(grid, row_index, NO_COLUMN_INDEX)

        found_months = months_in_cell(no_value)
        if found_months:
            current_month = found_months[0]
            continue

        partner_value = cell(grid, row_index, partner_col)
        if not (is_record_number(no_value) and is_real_value(partner_value)):
            continue

        records.append(
            {
                "row": row_index + 1,
                "month": current_month,
                "partner_name": " ".join(partner_value.split()),
                "pic_aiesec": " ".join(cell(grid, row_index, pic_col).split())
                if pic_col is not None
                else "",
                "date_of_mr": " ".join(cell(grid, row_index, date_col).split())
                if date_col is not None
                else "",
            }
        )

    return records


def normalize_partner(name: str) -> str:
    """Samakan nama partner untuk pembandingan.

    Karakter tak terlihat (word joiner U+2060, zero-width space, dsb.)
    memang ditemukan di sheet ini, jadi harus dibuang dulu.
    """
    cleaned = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", name)
    return " ".join(cleaned.split()).casefold()


def compare_tabs(results: list[dict]) -> None:
    """Cek apakah antar tab ada record yang sama (potensi hitung ganda)."""
    section("J. UJI TUMPANG TINDIH ANTAR TAB")
    print("Pertanyaan: kalau ketiga tab digabung, apakah ada record terhitung dua kali?")

    usable = [r for r in results if r.get("records")]
    if len(usable) < 2:
        print("Butuh minimal 2 tab untuk dibandingkan. Jalankan tanpa filter tab.")
        return

    print()
    print("Jumlah record per tab (aturan inspeksi):")
    for result in usable:
        print(f"  {result['worksheet']:<28} {len(result['records']):>6}")

    # Kunci pembanding: bulan + nama partner. PIC sengaja tidak dipakai
    # supaya perbedaan penulisan PIC tidak menyembunyikan duplikat.
    keyed = {
        result["worksheet"]: {
            (r["month"], normalize_partner(r["partner_name"])) for r in result["records"]
        }
        for result in usable
    }

    print()
    print("Irisan pasangan tab (kunci = bulan + nama partner):")
    names = list(keyed)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            left, right = names[i], names[j]
            shared = keyed[left] & keyed[right]
            smaller = min(len(keyed[left]), len(keyed[right])) or 1
            percent = len(shared) / smaller * 100
            print(f"  {left}  vs  {right}")
            print(f"    kunci sama : {len(shared)}  ({percent:.1f}% dari tab yang lebih kecil)")
            if shared:
                for example in sorted(shared)[:5]:
                    print(f"      contoh: bulan={example[0]!r} partner={example[1]!r}")

    all_keys = set().union(*keyed.values())
    total_rows = sum(len(keyed[name]) for name in names)
    print()
    print(f"Total kunci kalau ditumpuk apa adanya : {total_rows}")
    print(f"Total kunci unik                      : {len(all_keys)}")
    print(f"Selisih (potensi hitung ganda)        : {total_rows - len(all_keys)}")

    _compare_overlap_detail(usable)


def _compare_overlap_detail(usable: list[dict]) -> None:
    """Bedakan 'partner sama tapi PIC beda' dari 'benar-benar record kembar'.

    Kenapa ini penting:
      - partner sama, PIC BEDA  -> mungkin dua aktivitas MR yang sah
                                   (produk/tim berbeda meneliti partner sama)
      - partner sama, PIC SAMA  -> indikasi copy-paste antar tab
    Keduanya butuh perlakuan berbeda, jadi tidak boleh disamakan.
    """
    print()
    print("Rincian: apakah PIC-nya juga sama?")
    line()

    indexed = {
        result["worksheet"]: _index_by_key(result["records"]) for result in usable
    }
    names = list(indexed)

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            left, right = names[i], names[j]
            shared = set(indexed[left]) & set(indexed[right])
            same_pic = 0
            diff_pic = 0
            diff_examples = []
            for key in shared:
                pics_left = indexed[left][key]
                pics_right = indexed[right][key]
                if pics_left == pics_right:
                    same_pic += 1
                else:
                    diff_pic += 1
                    if len(diff_examples) < 4:
                        diff_examples.append((key, sorted(pics_left), sorted(pics_right)))

            print(f"  {left}  vs  {right}")
            print(f"    kunci sama total          : {len(shared)}")
            print(f"    PIC IDENTIK (kembar)      : {same_pic}")
            print(f"    PIC BERBEDA (mungkin sah) : {diff_pic}")
            for key, pics_left, pics_right in diff_examples:
                print(f"      contoh {key[0]}/{key[1]!r}: {pics_left} vs {pics_right}")
            print()

    print("Irisan per bulan (jumlah kunci yang muncul di lebih dari satu tab):")
    line()
    months_seen: dict[str, dict[str, int]] = {}
    for name in names:
        for month, _partner in indexed[name]:
            months_seen.setdefault(month or "(tanpa bulan)", {})
            months_seen[month or "(tanpa bulan)"][name] = (
                months_seen[month or "(tanpa bulan)"].get(name, 0) + 1
            )

    key_to_tabs: dict[tuple, list[str]] = {}
    for name in names:
        for key in indexed[name]:
            key_to_tabs.setdefault(key, []).append(name)

    per_month_dupes: dict[str, int] = {}
    for key, tabs in key_to_tabs.items():
        if len(tabs) > 1:
            label = key[0] or "(tanpa bulan)"
            per_month_dupes[label] = per_month_dupes.get(label, 0) + 1

    header_line = f"  {'bulan':<14}" + "".join(f"{short(n, 10):>12}" for n in names) + f"{'>1 tab':>10}"
    print(header_line)
    for month in months_seen:
        counts = "".join(f"{months_seen[month].get(n, 0):>12}" for n in names)
        print(f"  {month:<14}{counts}{per_month_dupes.get(month, 0):>10}")


def _index_by_key(records: list[dict]) -> dict[tuple, set[str]]:
    """Kelompokkan record: (bulan, partner) -> himpunan PIC."""
    index: dict[tuple, set[str]] = {}
    for record in records:
        key = (record["month"], normalize_partner(record["partner_name"]))
        index.setdefault(key, set()).add(record["pic_aiesec"].casefold())
    return index


def audit_mr_flag(
    grid: list[list[str]],
    header_index: int,
    headers: list[str],
    chosen: dict[str, int | None],
) -> None:
    """Periksa kolom funnel 'A. MR' yang berisi TRUE/FALSE.

    Pertanyaan yang dijawab: apakah "Total Market Research" itu
      (a) jumlah baris record, atau
      (b) jumlah record dengan flag MR = TRUE
    Kalau semua record TRUE, kedua definisi sama dan tidak jadi masalah.
    Kalau berbeda, ini keputusan bisnis yang harus ditanyakan.
    """
    print()
    print("K. AUDIT KOLOM FLAG FUNNEL 'A. MR' (TRUE/FALSE)")
    line()

    partner_col = chosen.get("partner_name")
    if partner_col is None:
        print("Dilewati: kolom nama partner belum teridentifikasi.")
        return

    flag_cols = [
        index
        for index, name in enumerate(headers)
        if re.fullmatch(r"[a-z]\.\s*mr", normalize_header(name))
    ]
    if not flag_cols:
        print("Tidak ada kolom berjudul pola '<huruf>. MR' di header row.")
        return

    for col_index in flag_cols:
        letter = sheets.column_letter(col_index + 1)
        print(f"Kolom {letter} = {headers[col_index]!r}")

        counter: dict[str, int] = {}
        records = 0
        for row_index in range(header_index, len(grid)):
            no_value = cell(grid, row_index, NO_COLUMN_INDEX)
            if months_in_cell(no_value):
                continue
            partner_value = cell(grid, row_index, partner_col)
            if not (is_record_number(no_value) and is_real_value(partner_value)):
                continue
            records += 1
            raw = " ".join(cell(grid, row_index, col_index).split()).upper()
            label = raw if raw else "(kosong)"
            counter[label] = counter.get(label, 0) + 1

        print(f"    total baris record : {records}")
        for label, count in sorted(counter.items(), key=lambda item: -item[1]):
            share = count / records * 100 if records else 0
            print(f"    {label:<12} {count:>6}  ({share:.1f}%)")
        print()


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------


def inspect_tab(worksheet) -> dict:
    section(f"TAB: {worksheet.title}")
    print(f"Grid size (deklarasi Sheets) : {worksheet.row_count} rows x {worksheet.col_count} cols")

    grid = sheets.read_worksheet_values(worksheet)
    widest = max((len(row) for row in grid), default=0)
    print(f"Grid terisi (setelah trim)   : {len(grid)} rows x {widest} cols")

    if not grid:
        print("Worksheet kosong. Tidak ada yang bisa diinspeksi.")
        return {"worksheet": worksheet.title, "header_row": None, "chosen": {}}

    show_top_rows(grid)
    header_index = detect_header_row(grid)
    headers = grid[header_index]

    show_headers(headers)
    candidates = find_candidates(headers)
    chosen = show_column_profiles(grid, header_index, headers, candidates)
    test_ref_assumption(grid, header_index, chosen)
    compare_date_rendering(worksheet, header_index, chosen.get("date_of_mr"))
    analyze_sections(grid, header_index, chosen)
    audit_date_column(grid, header_index, headers, chosen)
    audit_mr_flag(grid, header_index, headers, chosen)

    return {
        "worksheet": worksheet.title,
        "header_row": header_index + 1,
        "headers": headers,
        "chosen": chosen,
        "records": extract_records_for_inspection(grid, header_index, chosen),
    }


def show_final_mapping(results: list[dict]) -> None:
    section("RINGKASAN: USULAN MAPPING KOLOM UNTUK df_mr")
    print("Belum diterapkan. Ini usulan untuk divalidasi sebelum Phase 3.")
    print()
    for result in results:
        print(f"{result['worksheet']}")
        if result["header_row"] is None:
            print("    (tab kosong)")
            continue
        print(f"    header row : {result['header_row']}")
        headers = result.get("headers", [])
        for target, col_index in result["chosen"].items():
            if col_index is None:
                print(f"    {target:<14}-> BELUM KETEMU")
                continue
            letter = sheets.column_letter(col_index + 1)
            print(f"    {target:<14}-> {letter}  {short(headers[col_index], 40)!r}")
        print()


def main() -> int:
    print()
    line("#")
    print("PHASE 2 - INSPECT ACTIVE ESSM (3 TAB MARKET RESEARCH)")
    line("#")
    print("Mode: baca & laporkan saja. Tidak ada cleaning, tidak ada transformasi.")

    try:
        worksheets = sheets.open_active_mr_worksheets()
    except Exception as exc:
        print(f"\nGagal membuka tab Market Research: {type(exc).__name__}: {exc}")
        return 1

    # Argumen opsional: filter substring nama tab, mis. "ELDs".
    # Berguna saat iterasi supaya tidak menarik ketiga tab setiap kali.
    if len(sys.argv) > 1:
        needle = sys.argv[1].lower()
        worksheets = [ws for ws in worksheets if needle in ws.title.lower()]
        print(f"Filter tab aktif: {sys.argv[1]!r} -> {len(worksheets)} tab")
        if not worksheets:
            print("Tidak ada tab yang cocok dengan filter.")
            return 1

    results = [inspect_tab(worksheet) for worksheet in worksheets]
    compare_tabs(results)
    show_final_mapping(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
