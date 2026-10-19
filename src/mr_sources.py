"""mr_sources.py — SATU-SATUNYA tempat aturan sumber Market Research.

Sejak 1 Oktober 2026 data Market Research pindah ke database baru. Modul ini
memegang dua tanggung jawab yang sengaja dikumpulkan di satu tempat:

  1. PEMETAAN SKEMA database baru -> model data internal df_mr.
  2. ATURAN PEMILIHAN SUMBER menurut periode pelaporan.

--------------------------------------------------------------------------
ATURAN PEMILIHAN SUMBER (CUTOVER 1 OKTOBER 2026)
--------------------------------------------------------------------------
    periode < Oktober 2026    -> Active ESSM
    periode = Oktober 2026    -> Market Research database (baru)
    periode > Oktober 2026    -> Market Research database (baru)

Baris Active ESSM yang jatuh pada Oktober 2026 atau sesudahnya DIBUANG, dan
baris database baru yang jatuh sebelum Oktober 2026 juga DIBUANG. Jadi satu
periode pelaporan selalu dihitung dari SATU sumber dan tidak mungkin
terhitung ganda, bahkan kalau kedua sumber memuat bulan yang sama.

Keputusan ini TIDAK boleh disebar ke app.py: UI hanya memilih periode, dan
modul ini yang menentukan sumbernya.

--------------------------------------------------------------------------
MODEL DATA INTERNAL
--------------------------------------------------------------------------
Output build_df_mr() identik dengan df_mr lama, ditambah dua kolom audit:

    partner_name, pic_aiesec, date_of_mr, date_of_mr_raw, month,
    source_tab, source_row, source, period

`month` tetap nama bulan masa jabatan (huruf kecil) sebagai Categorical
berurutan, jadi semua filter periode, KPI, dan grafik yang sudah ada tetap
bekerja tanpa perubahan. `source` ("active_essm" / "mr_database") dan
`period` (pd.Period bulanan) hanya untuk audit dan pemilihan sumber.

--------------------------------------------------------------------------
ATURAN DATA DATABASE BARU
--------------------------------------------------------------------------
1. Nama kolom TIDAK diasumsikan sama dengan Active ESSM. Kolom dicari lewat
   daftar alias yang eksplisit (lihat FIELD_ALIASES). Kalau kolom wajib
   tidak ditemukan, modul ini GAGAL KERAS dengan menyebut header yang
   benar-benar ada - tidak pernah menebak kolom. Kolom "PIC Name" di
   Partnership Database adalah KONTAK DARI PIHAK PARTNER, bukan member
   AIESEC, jadi sengaja bukan alias pic_aiesec (yang dipakai "AIESEC PIC").

2. Baris header dicari di beberapa baris pertama, karena database baru boleh
   punya baris judul di atas header (mis. "PARTNERSHIP DATABASE ELDs").

3. FORMAT PER BULAN (Partnership Database BD 26.27, dipakai sejak Oktober
   2026). Tab ELDs / EwAs / SS tersusun bertumpuk per bulan, tanpa kolom
   bulan atau tanggal:

       PARTNERSHIP DATABASE ELDs                 <- judul
       OCTOBER                                   <- baris penanda bulan
       Partner Name | PIC Name | ... | AIESEC PIC | ...   <- header
       <data Oktober>
       NOVEMBER
       Partner Name | PIC Name | ... | AIESEC PIC | ...   <- header diulang
       <data November>

   Baris penanda = baris yang SATU-SATUNYA selnya berisi nama bulan
   (Inggris atau Indonesia, boleh ditambah tahun). Header yang diulang di
   tiap bagian bukan record; kolomnya berlaku untuk baris di bawahnya.

4. Sebuah baris dianggap RECORD kalau nama partner terisi dan bukan baris
   ringkasan ("total", "grand total", "subtotal", "jumlah", ...).

5. Bulan pelaporan diambil dari kolom bulan kalau ada, kalau tidak dari
   kolom tanggal, kalau tidak dari penanda bulan terdekat DI ATAS baris itu.
   Penanda tanpa tahun dibaca berurutan dari atas: penanda pertama memakai
   tahun masa jabatan, dan bulan yang lebih awal dari penanda sebelumnya
   berarti tahun berikutnya (DESEMBER -> JANUARI -> FEBRUARI = Des 2026,
   Jan 2027, Feb 2027). Baris yang bulannya tidak bisa ditentukan TIDAK
   dibuang diam-diam: jumlahnya dilaporkan sebagai diagnostik.

6. Baris database setelah akhir masa jabatan berjalan (mis. bagian
   FEBRUARI 2027) tidak ditampilkan, tetapi jumlahnya dilaporkan. Filter
   dashboard memakai nama bulan, jadi kalau ikut, baris itu tercampur dengan
   bulan bernama sama di masa jabatan ini.

7. Total MR tetap memakai definisi lama: jumlah kemunculan nama PIC. Baris
   tanpa PIC tetap dibawa (untuk audit) dan tetap tidak menambah Total MR.

8. DEDUPLIKASI (hanya untuk database baru, bukan Active ESSM):
   dua baris dibuang kalau SELURUH identitasnya sama - tab, periode, nama
   partner (dinormalisasi), PIC (dinormalisasi), tanggal mentah, DAN nomor
   record kalau kolomnya ada. Partner yang sama diteliti PIC berbeda, bulan
   berbeda, atau bernomor record berbeda tetap dihitung sebagai prospek yang
   sah. Active ESSM tetap tidak pernah di-dedupe (tiga tab = tiga tim).

Pemeriksaan read-only tanpa menulis apa pun:

    python -m src.mr_sources preflight
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

import pandas as pd

from . import preparation, sheets
from .preparation import TERM_MONTH_ORDER, clean_text, has_value, normalize_header

# ---------------------------------------------------------------------------
# Konstanta cutover
# ---------------------------------------------------------------------------

#: Hari pertama database baru menjadi sumber kebenaran.
CUTOVER_DATE = pd.Timestamp("2026-10-01")

#: Periode bulanan pertama yang dilayani database baru.
CUTOVER_PERIOD = pd.Period("2026-10", freq="M")

SOURCE_ACTIVE_ESSM = "active_essm"
SOURCE_MR_DATABASE = "mr_database"

SOURCE_LABELS = {
    SOURCE_ACTIVE_ESSM: "Active ESSM",
    SOURCE_MR_DATABASE: "Market Research database",
}

#: Kolom model data internal, berurutan.
MR_COLUMNS: tuple[str, ...] = (
    "partner_name",
    "pic_aiesec",
    "date_of_mr",
    "date_of_mr_raw",
    "month",
    "source_tab",
    "source_row",
    "source",
    "period",
)

# ---------------------------------------------------------------------------
# Pemetaan skema database baru
# ---------------------------------------------------------------------------

#: Alias header, sudah dinormalisasi (huruf kecil, spasi rapat).
#: Urutan di dalam tuple = prioritas pencocokan.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "partner_name": (
        "partner's name", "partner name", "partner", "nama partner",
        "company name", "company", "nama perusahaan", "prospect name",
        "prospect", "organization", "organisation", "institution",
        "nama institusi", "client name",
    ),
    # "pic name" / "nama pic" sengaja TIDAK ada: di Partnership Database kolom
    # "PIC Name" adalah kontak dari pihak partner. Kalau header "AIESEC PIC"
    # diganti nama, pemetaan harus gagal keras, bukan diam-diam menghitung
    # kontak partner sebagai member AIESEC.
    "pic_aiesec": (
        "pic from aiesec", "pic aiesec", "aiesec pic", "pic",
        "person in charge", "researcher", "researched by",
        "member name", "member", "nama member", "mr pic", "pic mr",
    ),
    "month": (
        "month", "months", "reporting month", "month of mr",
        "month of market research", "bulan", "bulan mr", "periode",
        "period", "reporting period",
    ),
    "date": (
        "date of mr", "date of market research", "mr date", "date",
        "tanggal", "tanggal mr", "timestamp", "submitted at",
        "submission time", "created at", "date submitted",
    ),
    "record_no": ("no", "no.", "number", "id", "record id", "mr id", "nomor"),
    "status": (
        "status", "mr status", "status mr", "prospect status", "result",
        "progress", "stage", "keterangan",
    ),
}

#: Field yang WAJIB ada supaya sebuah tab dianggap tab Market Research.
REQUIRED_FIELDS: tuple[str, ...] = ("partner_name", "pic_aiesec")

#: Minimal salah satu dari ini wajib ada untuk menentukan periode pelaporan.
PERIOD_FIELDS: tuple[str, ...] = ("month", "date")

#: Berapa baris pertama yang dipindai untuk mencari baris header.
HEADER_SEARCH_ROWS = 12

#: Nilai nama partner yang berarti baris ringkasan, bukan prospek.
SUMMARY_MARKERS = frozenset(
    {
        "total", "totals", "grand total", "sub total", "subtotal", "sum",
        "jumlah", "total mr", "summary", "rekap", "average", "rata-rata",
    }
)

#: Format tanggal eksplisit yang dicoba lebih dulu (tidak ambigu).
UNAMBIGUOUS_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
)

#: Format "bulan saja" untuk kolom bulan bertahun, mis. "OCT 26" / "2026-10".
MONTH_ONLY_FORMATS: tuple[str, ...] = ("%b %y", "%b %Y", "%B %y", "%B %Y", "%Y-%m", "%m/%Y")

_MONTH_NAME_NUMBERS = {
    name: index + 2 if index + 2 <= 12 else index + 2 - 12
    for index, name in enumerate(TERM_MONTH_ORDER)
}
_YEAR_IN_TEXT = re.compile(r"(20\d{2}|'?\d{2})")


class MRSchemaError(sheets.MRSourceError):
    """Skema database baru tidak bisa dipetakan ke model data internal.

    `missing` berisi field yang tidak ditemukan, dipakai untuk memilih baris
    header yang PALING mendekati saat melaporkan kegagalan.
    """

    def __init__(self, message: str, *, missing: tuple[str, ...] = ()):
        super().__init__(message)
        self.missing = tuple(missing)


@dataclass
class SourceLoad:
    """Hasil pembacaan satu sumber Market Research."""

    name: str
    frame: pd.DataFrame
    available: bool = True
    error: str | None = None
    error_kind: str | None = None
    notes: list[str] = field(default_factory=list)
    rows_read: int = 0
    rows_used: int = 0

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.name, self.name)


@dataclass
class MRLoadResult:
    """df_mr gabungan plus diagnostik per sumber."""

    df_mr: pd.DataFrame
    sources: dict[str, SourceLoad]
    notes: list[str] = field(default_factory=list)

    @property
    def failures(self) -> dict[str, SourceLoad]:
        return {name: load for name, load in self.sources.items() if load.error}


# ---------------------------------------------------------------------------
# Aturan pemilihan sumber
# ---------------------------------------------------------------------------


def monthly_period(value) -> pd.Period | None:
    """Ubah nilai apa pun menjadi pd.Period bulanan, atau None kalau kosong.

    None, NaT, dan NaN semuanya berarti "periode tidak diketahui". Pemeriksaan
    ini penting karena pandas mengubah None di kolom Period menjadi NaT, dan
    `NaT is not None` bernilai True - kalau dibiarkan, baris tanpa bulan bisa
    ikut terhitung ke sumber yang salah.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Period):
        return value.asfreq("M")
    try:
        return pd.Period(pd.Timestamp(value), freq="M")
    except (ValueError, TypeError):
        return None


def source_for_period(period) -> str:
    """Sumber yang berwenang untuk satu periode bulanan.

    Args:
        period: pd.Period bulanan, pd.Timestamp, atau string "YYYY-MM".
    """
    monthly = monthly_period(period)
    if monthly is None:
        raise ValueError("Periode wajib diisi untuk menentukan sumber Market Research.")
    return SOURCE_ACTIVE_ESSM if monthly < CUTOVER_PERIOD else SOURCE_MR_DATABASE


def source_for_month(month: str, today: pd.Timestamp | None = None) -> str:
    """Sumber yang berwenang untuk satu nama bulan masa jabatan."""
    from . import periods

    return source_for_period(periods.month_to_period(month, today=today))


def sources_for_selection(selection=None, today: pd.Timestamp | None = None) -> tuple[str, ...]:
    """Sumber yang dibutuhkan oleh satu pilihan periode, berurutan."""
    from . import periods

    needed = {
        source_for_month(month, today=today)
        for month in periods.normalize_months(selection)
    }
    return tuple(name for name in (SOURCE_ACTIVE_ESSM, SOURCE_MR_DATABASE) if name in needed)


# ---------------------------------------------------------------------------
# Pemetaan skema
# ---------------------------------------------------------------------------


def _alias_match(header: str, aliases: tuple[str, ...]) -> int | None:
    """Skor kecocokan header terhadap daftar alias; makin kecil makin tepat."""
    if not header:
        return None
    for index, alias in enumerate(aliases):
        if header == alias:
            return index
    squashed = re.sub(r"[^a-z0-9]", "", header)
    for index, alias in enumerate(aliases):
        if squashed == re.sub(r"[^a-z0-9]", "", alias):
            return 100 + index
    return None


def match_fields(headers: list[str]) -> dict[str, int]:
    """Petakan field internal -> index kolom berdasarkan NAMA header, tanpa validasi.

    Pencocokan dilakukan dua tahap: sama persis dulu, lalu sama setelah
    tanda baca dibuang. Header yang sama sekali tidak dikenali diabaikan.
    """
    normalized = [normalize_header(value) for value in headers]
    resolved: dict[str, int] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        best: tuple[int, int] | None = None
        for column_index, header in enumerate(normalized):
            score = _alias_match(header, aliases)
            if score is None:
                continue
            if best is None or score < best[0]:
                best = (score, column_index)
        if best is not None:
            resolved[field_name] = best[1]

    # Satu kolom tidak boleh dipakai untuk dua field sekaligus; field yang
    # lebih dulu di FIELD_ALIASES menang dan field lain dianggap tidak ada.
    seen: dict[int, str] = {}
    for field_name in list(FIELD_ALIASES):
        if field_name not in resolved:
            continue
        column_index = resolved[field_name]
        if column_index in seen:
            resolved.pop(field_name)
        else:
            seen[column_index] = field_name
    return resolved


def resolve_schema(headers: list[str], *, require_period: bool = True) -> dict[str, int]:
    """Petakan satu baris header dan pastikan field wajibnya ada.

    Args:
        require_period: False kalau bulan bisa diambil dari baris penanda
            bulan (format per bulan), jadi kolom bulan/tanggal tidak wajib.

    Raises:
        MRSchemaError: kalau kolom wajib tidak ditemukan. Pesannya menyebut
            header yang benar-benar ada supaya bisa langsung diperbaiki.
    """
    resolved = match_fields(headers)
    missing = [name for name in REQUIRED_FIELDS if name not in resolved]
    if require_period and not any(name in resolved for name in PERIOD_FIELDS):
        missing.append("month/date")
    if missing:
        present = ", ".join(repr(clean_text(value)) for value in headers if clean_text(value))
        hint = (
            " Bulan juga bisa diambil dari baris penanda yang hanya berisi nama "
            "bulan (mis. 'OCTOBER') di atas datanya."
            if "month/date" in missing
            else ""
        )
        raise MRSchemaError(
            "Skema Market Research database tidak bisa dipetakan. Kolom yang "
            f"belum ditemukan: {', '.join(missing)}. Header yang terbaca: "
            f"{present or '(kosong)'}. Tambahkan alias kolomnya di FIELD_ALIASES "
            f"(src/mr_sources.py) atau samakan nama header di spreadsheet.{hint}",
            missing=tuple(missing),
        )
    return resolved


def section_month_label(value) -> str | None:
    """Nama bulan kalau sel HANYA berisi nama bulan (boleh ditambah tahun).

    "OCTOBER", "Desember 2026", "JANUARI '27", "🌸april" -> nama bulan.
    "May Bank", "Mayora", "PARTNERSHIP DATABASE ELDs" -> None.
    """
    text = clean_text(value).casefold()
    words = re.findall(r"[a-z]+", text)
    if len(words) != 1 or words[0] not in preparation.MONTH_TOKENS:
        return None
    numbers = re.findall(r"\d+", text)
    if len(numbers) > 1 or any(len(number) not in (2, 4) for number in numbers):
        return None
    return preparation.MONTH_ALIASES.get(words[0], words[0])


def section_marker(row: list[str]) -> str | None:
    """Teks penanda bulan kalau SATU-SATUNYA sel berisi di baris ini nama bulan.

    Di Partnership Database penandanya sel gabungan A:I, jadi Google Sheets
    hanya mengembalikan nilai di kolom pertama dan sisanya kosong.
    """
    filled = [clean_text(value) for value in row if has_value(value)]
    if len(filled) == 1 and section_month_label(filled[0]) is not None:
        return filled[0]
    return None


def find_header_row(grid: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Cari baris header di beberapa baris pertama dan petakan skemanya.

    Database baru boleh punya baris judul di atas header, jadi posisi header
    tidak dipaku ke baris 1 seperti di Active ESSM. Kalau tab memakai baris
    penanda bulan, kolom bulan/tanggal tidak wajib.

    Raises:
        MRSchemaError: kalau tidak ada baris yang cocok. Pesannya memakai baris
            yang PALING mendekati header (paling sedikit kolom yang kurang),
            bukan sekadar baris pertama yang biasanya hanya judul.
    """
    sectioned = any(section_marker(row) is not None for row in grid)
    best: tuple[int, int, dict[str, int]] | None = None
    closest: tuple[int, int, MRSchemaError] | None = None
    for row_index in range(min(HEADER_SEARCH_ROWS, len(grid))):
        try:
            resolved = resolve_schema(grid[row_index], require_period=not sectioned)
        except MRSchemaError as exc:
            if closest is None or len(exc.missing) < closest[0]:
                closest = (len(exc.missing), row_index, exc)
            continue
        score = len(resolved)
        if best is None or score > best[0]:
            best = (score, row_index, resolved)
    if best is not None:
        return best[1], best[2]
    if closest is not None:
        _, row_index, exc = closest
        raise MRSchemaError(f"Baris {row_index + 1}: {exc}", missing=exc.missing)
    raise MRSchemaError("Tab Market Research database kosong: tidak ada baris header.")


def _is_summary_row(partner: str) -> bool:
    text = clean_text(partner).casefold().rstrip(":")
    return text in SUMMARY_MARKERS


def _term_month_name(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%B").casefold()


def month_name_from_text(value: str) -> str | None:
    """Nama bulan masa jabatan dari teks bebas, mis. "Oktober 2026" -> october."""
    return preparation.month_in_cell(value)


def parse_period_from_month_cell(
    value: str, fallback_year: int | None = None
) -> pd.Period | None:
    """Ubah isi kolom bulan menjadi periode bulanan bertahun.

    Mendukung "October 2026", "Oct 26", "2026-10", "10/2026", dan nama bulan
    tanpa tahun (tahunnya memakai fallback_year, mis. tahun masa jabatan).
    """
    text = clean_text(value)
    if not text:
        return None
    for date_format in MONTH_ONLY_FORMATS:
        try:
            parsed = pd.to_datetime(text, format=date_format)
        except (ValueError, TypeError):
            continue
        if parsed is not pd.NaT and not pd.isna(parsed):
            return pd.Period(parsed, freq="M")

    name = month_name_from_text(text)
    if name is None:
        return None
    month_number = _MONTH_NAME_NUMBERS.get(name)
    if month_number is None:
        return None
    year_match = _YEAR_IN_TEXT.search(text)
    if year_match:
        raw_year = year_match.group(1).lstrip("'")
        year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
        return pd.Period(year=year, month=month_number, freq="M")
    if fallback_year is None:
        return None
    # Masa jabatan dimulai February; January milik tahun berikutnya.
    year = fallback_year + 1 if month_number == 1 else fallback_year
    return pd.Period(year=year, month=month_number, freq="M")


def section_period(
    marker: str, previous: pd.Period | None, fallback_year: int | None = None
) -> pd.Period | None:
    """Periode bulanan sebuah baris penanda bulan (lihat aturan 5 di docstring).

    Tahun yang tertulis di penanda selalu dipakai. Tanpa tahun, penanda pertama
    memakai tahun masa jabatan; penanda berikutnya dibaca berurutan, jadi bulan
    yang lebih awal dari penanda sebelumnya berarti tahun berikutnya.
    """
    explicit = parse_period_from_month_cell(marker)
    if explicit is not None:
        return explicit
    month_number = _MONTH_NAME_NUMBERS.get(section_month_label(marker) or "")
    if month_number is None:
        return None
    if previous is None:
        return parse_period_from_month_cell(marker, fallback_year=fallback_year)
    candidate = pd.Period(year=previous.year, month=month_number, freq="M")
    if candidate < previous:
        candidate = pd.Period(year=previous.year + 1, month=month_number, freq="M")
    return candidate


def parse_date_cell(
    value: str, preferred_format: str | None = None, month_hint: str | None = None
) -> pd.Timestamp:
    """Urai satu sel tanggal database baru; NaT kalau tidak bisa diurai.

    Format yang tidak ambigu dicoba lebih dulu. Untuk tanggal bergaris miring
    yang ambigu (1/9/2026), keputusan diserahkan ke logika yang sudah ada di
    preparation.parse_received_date (bukti dominan per tab + petunjuk bulan).
    """
    text = clean_text(value)
    if not text:
        return pd.NaT
    for date_format in UNAMBIGUOUS_DATE_FORMATS:
        try:
            parsed = pd.to_datetime(text, format=date_format)
        except (ValueError, TypeError):
            continue
        if parsed is not pd.NaT and not pd.isna(parsed):
            return parsed
    # Timestamp Google Form: "10/5/2026 14:03:21" -> bagian tanggalnya saja.
    head = text.split(" ")[0]
    parsed, _ = preparation.parse_received_date(
        head, month_hint=month_hint, preferred_format=preferred_format
    )
    return parsed


def extract_database_records(
    grid: list[list[str]], source_tab: str, fallback_year: int | None = None
) -> tuple[list[dict], dict[str, int], list[str]]:
    """Ambil record Market Research dari satu tab database baru.

    Returns:
        (records, statistik, catatan). `statistik` memuat jumlah baris yang
        dibaca, dipakai, dan dilewati beserta alasannya, supaya tidak ada
        baris yang hilang tanpa penjelasan.
    """
    stats = {
        "rows_scanned": 0,
        "records": 0,
        "skipped_empty_partner": 0,
        "skipped_summary": 0,
        "section_markers": 0,
        "repeated_headers": 0,
        "missing_period": 0,
        "missing_pic": 0,
        "unparsed_date": 0,
    }
    notes: list[str] = []
    if not grid:
        return [], stats, ["Tab kosong (tidak ada baris sama sekali)."]

    header_index, columns = find_header_row(grid)
    first_date_index = columns.get("date")
    preferred_format = None
    if first_date_index is not None:
        preferred_format = preparation.detect_dominant_date_format(
            preparation.cell(grid, row, first_date_index)
            for row in range(header_index + 1, len(grid))
        )

    # Penanda bulan di atas header pertama (format per bulan: "OCTOBER").
    section: pd.Period | None = None
    for row_index in range(header_index):
        marker = section_marker(grid[row_index])
        if marker is not None:
            section = section_period(marker, section, fallback_year)

    statuses: set[str] = set()
    records: list[dict] = []
    for row_index in range(header_index + 1, len(grid)):
        stats["rows_scanned"] += 1
        row = grid[row_index]

        # Baris penanda bulan: ganti konteks bulan, bukan record.
        marker = section_marker(row)
        if marker is not None:
            stats["section_markers"] += 1
            section = section_period(marker, section, fallback_year)
            continue

        # Header yang diulang di tiap bagian bulan: bukan record. Kolomnya
        # berlaku untuk baris di bawahnya, jadi urutan kolom yang berbeda per
        # bagian tetap terbaca benar.
        repeated = match_fields(row)
        if all(name in repeated for name in REQUIRED_FIELDS):
            stats["repeated_headers"] += 1
            columns = repeated
            continue

        date_index = columns.get("date")
        month_index = columns.get("month")
        partner_value = preparation.cell(grid, row_index, columns["partner_name"])
        if not has_value(partner_value):
            stats["skipped_empty_partner"] += 1
            continue
        if _is_summary_row(partner_value):
            stats["skipped_summary"] += 1
            continue

        pic_value = preparation.cell(grid, row_index, columns["pic_aiesec"])
        month_value = (
            preparation.cell(grid, row_index, month_index) if month_index is not None else ""
        )
        date_value = (
            preparation.cell(grid, row_index, date_index) if date_index is not None else ""
        )
        if "status" in columns:
            status_text = clean_text(preparation.cell(grid, row_index, columns["status"]))
            if status_text:
                statuses.add(status_text)

        month_hint = month_name_from_text(month_value) if month_value else None
        if month_hint is None and section is not None:
            month_hint = _term_month_name(section.start_time)
        parsed_date = parse_date_cell(
            date_value, preferred_format=preferred_format, month_hint=month_hint
        )
        if has_value(date_value) and pd.isna(parsed_date):
            stats["unparsed_date"] += 1

        period = parse_period_from_month_cell(month_value, fallback_year=fallback_year)
        if period is None and not pd.isna(parsed_date):
            period = pd.Period(parsed_date, freq="M")
        if period is None:
            period = section
        if period is None:
            stats["missing_period"] += 1

        if not has_value(pic_value):
            stats["missing_pic"] += 1

        records.append(
            {
                "partner_name": clean_text(partner_value),
                "pic_aiesec": clean_text(pic_value) if has_value(pic_value) else None,
                "date_of_mr": parsed_date,
                "date_of_mr_raw": clean_text(date_value) if has_value(date_value) else None,
                "month": _term_month_name(period.start_time) if period is not None else None,
                "period": period,
                "source_tab": source_tab,
                "source_row": row_index + 1,
                "source": SOURCE_MR_DATABASE,
                "record_no": (
                    clean_text(preparation.cell(grid, row_index, columns["record_no"]))
                    if "record_no" in columns
                    else ""
                ),
            }
        )
    stats["records"] = len(records)

    if stats["missing_period"]:
        notes.append(
            f"{stats['missing_period']} baris di tab '{source_tab}' tidak punya bulan "
            "pelaporan yang bisa dibaca, jadi tidak masuk filter periode mana pun. "
            "Periksa kolom bulan/tanggal atau baris penanda bulan di atasnya."
        )
    if stats["unparsed_date"]:
        notes.append(
            f"{stats['unparsed_date']} tanggal di tab '{source_tab}' tidak bisa diurai "
            "dan dibiarkan kosong (tidak ditebak)."
        )
    if stats["missing_pic"]:
        notes.append(
            f"{stats['missing_pic']} baris di tab '{source_tab}' tidak punya PIC, jadi "
            "tidak menambah Total MR (aturan lama dipertahankan)."
        )
    if statuses:
        notes.append(
            f"Kolom status di tab '{source_tab}' memuat nilai: "
            f"{', '.join(sorted(statuses)[:8])}. Belum ada aturan BD yang memakai "
            "kolom ini, jadi tidak ada baris yang disaring berdasarkan status."
        )
    return records, stats, notes


def deduplicate_database_records(records: list[dict]) -> tuple[list[dict], int]:
    """Buang baris yang SELURUH identitasnya sama (lihat aturan 8 di docstring)."""
    seen: set[tuple] = set()
    unique: list[dict] = []
    removed = 0
    for record in records:
        key = (
            record["source_tab"],
            str(record.get("period")),
            preparation.normalize_partner_name(record["partner_name"]),
            clean_text(record.get("pic_aiesec")).casefold(),
            clean_text(record.get("date_of_mr_raw")).casefold(),
            clean_text(record.get("record_no")).casefold(),
        )
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        unique.append(record)
    return unique, removed


def build_df_mr_database(
    grids: list[tuple[str, list[list[str]]]] | None = None,
    *,
    fallback_year: int | None = None,
) -> tuple[pd.DataFrame, list[str], dict[str, int]]:
    """Bangun dataframe model internal dari database Market Research baru.

    Args:
        grids: pasangan (nama_tab, raw values). Kalau None, dibaca langsung
            dari spreadsheet (butuh konfigurasi + akses).
        fallback_year: tahun masa jabatan untuk kolom bulan tanpa tahun.

    Returns:
        (dataframe, catatan diagnostik, statistik).
    """
    if grids is None:
        grids = sheets.read_market_research_db_grids()

    records: list[dict] = []
    notes: list[str] = []
    totals = {"rows_scanned": 0, "records": 0, "tabs_used": 0, "duplicates_removed": 0}
    schema_errors: list[str] = []

    for title, grid in grids:
        try:
            tab_records, stats, tab_notes = extract_database_records(
                grid, title, fallback_year=fallback_year
            )
        except MRSchemaError as exc:
            schema_errors.append(f"Tab '{title}': {exc}")
            continue
        totals["rows_scanned"] += stats["rows_scanned"]
        if tab_records:
            totals["tabs_used"] += 1
        records.extend(tab_records)
        notes.extend(tab_notes)

    if not records and schema_errors:
        # Tidak ada satu tab pun yang bisa dipetakan: ini masalah skema,
        # bukan "data kosong". Jangan pernah diperlakukan sebagai nol MR.
        raise MRSchemaError(" | ".join(schema_errors))
    if schema_errors:
        notes.append(
            f"{len(schema_errors)} tab dilewati karena skemanya bukan Market Research: "
            + " | ".join(schema_errors)
        )

    records, removed = deduplicate_database_records(records)
    totals["duplicates_removed"] = removed
    totals["records"] = len(records)
    if removed:
        notes.append(
            f"{removed} baris duplikat identik (tab + bulan + partner + PIC + tanggal "
            "+ nomor record sama) tidak dihitung dua kali."
        )

    frame = pd.DataFrame.from_records(records, columns=list(MR_COLUMNS))
    return frame, notes, totals


# ---------------------------------------------------------------------------
# Penggabungan dua sumber
# ---------------------------------------------------------------------------


def _as_period_series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series([], dtype="object")
    return frame["period"]


def _prepare_active_essm(
    df_active: pd.DataFrame, today: pd.Timestamp | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Beri kolom source/period pada df_mr Active ESSM dan terapkan cutover."""
    from . import periods

    notes: list[str] = []
    frame = df_active.copy() if df_active is not None else pd.DataFrame()
    if frame.empty:
        for column in MR_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.Series(dtype="object")
        return frame[list(MR_COLUMNS)], notes

    frame["source"] = SOURCE_ACTIVE_ESSM
    frame["period"] = [
        periods.month_to_period(str(month), today=today) if pd.notna(month) else None
        for month in frame["month"]
    ]
    keep = [
        (monthly := monthly_period(period)) is not None
        and source_for_period(monthly) == SOURCE_ACTIVE_ESSM
        for period in frame["period"]
    ]
    dropped = int(len(frame) - sum(keep))
    if dropped:
        notes.append(
            f"{dropped} baris Active ESSM pada Oktober 2026 atau sesudahnya tidak "
            "dipakai: sejak 1 Oktober 2026 sumbernya adalah Market Research database "
            "(mencegah hitung ganda)."
        )
    frame = frame[keep].copy()
    for column in MR_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[list(MR_COLUMNS)], notes


def _prepare_database(
    df_database: pd.DataFrame, today: pd.Timestamp | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Terapkan cutover dan batas akhir masa jabatan pada data database baru."""
    from . import periods

    notes: list[str] = []
    frame = df_database.copy() if df_database is not None else pd.DataFrame()
    if frame.empty:
        for column in MR_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.Series(dtype="object")
        return frame[list(MR_COLUMNS)], notes

    term_end = periods.month_to_period(TERM_MONTH_ORDER[-1], today=today)
    keep: list[bool] = []
    before_cutover = after_term = 0
    for period in _as_period_series(frame):
        monthly = monthly_period(period)
        if monthly is None or source_for_period(monthly) != SOURCE_MR_DATABASE:
            before_cutover += 1
            keep.append(False)
        elif monthly > term_end:
            after_term += 1
            keep.append(False)
        else:
            keep.append(True)
    if before_cutover:
        notes.append(
            f"{before_cutover} baris Market Research database berada sebelum Oktober 2026 "
            "atau tanpa bulan yang bisa dibaca, jadi tidak dipakai: periode itu "
            "tetap milik Active ESSM."
        )
    if after_term:
        notes.append(
            f"{after_term} baris Market Research database berada setelah "
            f"{term_end.strftime('%B %Y')} (sudah masa jabatan berikutnya), jadi belum "
            "ditampilkan supaya tidak tercampur dengan bulan bernama sama di masa "
            "jabatan ini."
        )
    frame = frame[keep].copy()
    for column in MR_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[list(MR_COLUMNS)], notes


def combine_sources(
    df_active: pd.DataFrame,
    df_database: pd.DataFrame,
    *,
    today: pd.Timestamp | None = None,
    exclude_months: tuple[str, ...] = preparation.EXCLUDED_MONTHS,
) -> tuple[pd.DataFrame, list[str]]:
    """Gabungkan kedua sumber memakai aturan cutover, tanpa hitung ganda.

    Kategori bulan dan urutannya sama persis dengan df_mr lama, jadi semua
    filter, KPI, dan grafik yang sudah ada tetap berlaku.
    """
    active, active_notes = _prepare_active_essm(df_active, today=today)
    database, database_notes = _prepare_database(df_database, today=today)
    notes = active_notes + database_notes

    combined = pd.concat([active, database], ignore_index=True)
    excluded = {month.casefold() for month in exclude_months}
    if not combined.empty:
        combined = combined[~combined["month"].astype("object").isin(excluded)].copy()

    month_categories = [month for month in TERM_MONTH_ORDER if month not in excluded]
    combined["month"] = pd.Categorical(
        combined["month"].astype("object"), categories=month_categories, ordered=True
    )
    combined = combined[list(MR_COLUMNS)]
    if combined.empty:
        return combined.reset_index(drop=True), notes
    return (
        combined.sort_values(["month", "source", "source_tab", "source_row"])
        .reset_index(drop=True),
        notes,
    )


def build_df_mr(
    *,
    today: pd.Timestamp | None = None,
    active_grids: list[tuple[str, list[list[str]]]] | None = None,
    database_grids: list[tuple[str, list[list[str]]]] | None = None,
    require_database: bool | None = None,
) -> MRLoadResult:
    """Bangun df_mr gabungan dari kedua sumber beserta diagnostiknya.

    Active ESSM tetap dibaca lewat jalur lama (preparation.build_df_mr).
    Database baru dibaca lewat sheets.read_market_research_db_grids().

    Kegagalan satu sumber TIDAK dipalsukan menjadi "nol MR": frame-nya
    dikosongkan dan alasannya dikembalikan di `sources[...]`, supaya UI bisa
    menjelaskan apa yang hilang. `require_database=True` membuat kegagalan
    database baru dilempar sebagai exception (dipakai oleh preflight).
    """
    from . import periods

    today = pd.Timestamp.today().normalize() if today is None else pd.Timestamp(today)
    term_year = periods.term_start_year(today)

    # ---- Active ESSM (historis, sampai September 2026) ----
    active_load = SourceLoad(name=SOURCE_ACTIVE_ESSM, frame=pd.DataFrame())
    try:
        if active_grids is None:
            df_active = preparation.build_df_mr()
        else:
            records: list[dict] = []
            for title, grid in active_grids:
                records.extend(preparation.extract_mr_records(grid, title))
            df_active = pd.DataFrame.from_records(
                records,
                columns=[
                    "partner_name", "pic_aiesec", "date_of_mr_raw", "month",
                    "source_tab", "source_row",
                ],
            )
            if not df_active.empty:
                df_active["date_of_mr"] = preparation.parse_mr_dates(
                    df_active["date_of_mr_raw"]
                )
        active_load.frame = df_active
        active_load.rows_read = len(df_active)
    except Exception as exc:  # noqa: BLE001 - diterjemahkan jadi diagnostik aman
        active_load.available = False
        active_load.error_kind = type(exc).__name__
        active_load.error = (
            "Active ESSM (data Market Research sampai September 2026) tidak bisa dibaca."
        )
        active_load.frame = pd.DataFrame()

    # ---- Market Research database (Oktober 2026 ke atas) ----
    database_load = SourceLoad(name=SOURCE_MR_DATABASE, frame=pd.DataFrame())
    try:
        if database_grids is None and not sheets.market_research_db_configured():
            raise sheets.MRNotConfiguredError(
                "Market Research database belum dikonfigurasi, jadi data Oktober 2026 "
                f"ke atas belum tersedia. Isi {sheets.ENV_MARKET_RESEARCH_DB} dengan ID "
                "atau URL spreadsheet Market Research yang baru."
            )
        frame, notes, totals = build_df_mr_database(
            database_grids, fallback_year=term_year
        )
        database_load.frame = frame
        database_load.notes = notes
        database_load.rows_read = totals["rows_scanned"]
    except sheets.MRSourceError as exc:
        database_load.available = False
        database_load.error_kind = type(exc).__name__
        database_load.error = str(exc)
        database_load.frame = pd.DataFrame()
    except Exception as exc:  # noqa: BLE001 - jangan pernah membocorkan detail teknis
        database_load.available = False
        database_load.error_kind = type(exc).__name__
        database_load.error = (
            "Market Research database tidak bisa dibaca. Periksa konfigurasi dan "
            "akses service account."
        )
        database_load.frame = pd.DataFrame()

    if require_database and database_load.error:
        raise sheets.MRSourceError(database_load.error)

    combined, notes = combine_sources(
        active_load.frame, database_load.frame, today=today
    )
    if not combined.empty:
        used = combined["source"].value_counts()
        active_load.rows_used = int(used.get(SOURCE_ACTIVE_ESSM, 0))
        database_load.rows_used = int(used.get(SOURCE_MR_DATABASE, 0))

    if database_load.available and not database_load.error and database_load.rows_used == 0:
        database_load.notes.append(
            "Market Research database terhubung dan terbaca, tetapi belum ada baris "
            "untuk Oktober 2026 ke atas (nol baris, bukan kegagalan koneksi)."
        )

    return MRLoadResult(
        df_mr=combined,
        sources={SOURCE_ACTIVE_ESSM: active_load, SOURCE_MR_DATABASE: database_load},
        notes=notes + database_load.notes,
    )


# ---------------------------------------------------------------------------
# Preflight CLI (READ-ONLY, tidak pernah menulis)
# ---------------------------------------------------------------------------


def _preflight() -> int:
    report = sheets.market_research_db_access_report()
    print("Market Research database preflight (read-only)")
    print(f"  service account      : {report['service_account'] or 'tidak terdeteksi'}")
    print(f"  {sheets.ENV_MARKET_RESEARCH_DB} terisi : {report['configured']}")
    if not report["ok"]:
        print(f"  status               : GAGAL ({report['error_kind']})")
        print(f"  tindakan             : {report['error']}")
        return 1
    print(f"  spreadsheet          : {report['spreadsheet_title']}")
    print(f"  tab tersedia         : {', '.join(report['worksheets']) or '(tidak ada)'}")
    selected = report["selected_worksheets"]
    print(f"  tab dikonfigurasi    : {', '.join(selected) if selected else '(semua tab dipindai)'}")

    try:
        grids = sheets.read_market_research_db_grids()
    except sheets.MRSourceError as exc:
        print(f"  status               : GAGAL ({type(exc).__name__})")
        print(f"  tindakan             : {exc}")
        return 1

    from . import periods

    term_year = periods.term_start_year()
    mapped = 0
    for title, grid in grids:
        try:
            header_index, columns = find_header_row(grid)
        except MRSchemaError as exc:
            print(f"  tab '{title}': TIDAK DIPETAKAN -> {exc}")
            continue
        mapped += 1
        print(f"  tab '{title}': header baris {header_index + 1}")
        for field_name, column_index in sorted(columns.items(), key=lambda item: item[1]):
            header = clean_text(preparation.cell(grid, header_index, column_index))
            print(f"      {field_name:<13} <- kolom {column_index + 1} ({header!r})")
        if not any(name in columns for name in PERIOD_FIELDS):
            markers = [marker for row in grid if (marker := section_marker(row))]
            print(f"      {'month':<13} <- baris penanda bulan ({', '.join(markers)})")

    if not mapped:
        print("  status               : GAGAL (tidak ada tab yang skemanya cocok)")
        return 1

    frame, notes, totals = build_df_mr_database(grids, fallback_year=term_year)
    in_scope = frame[[
        (monthly := monthly_period(period)) is not None
        and source_for_period(monthly) == SOURCE_MR_DATABASE
        for period in _as_period_series(frame)
    ]] if not frame.empty else frame
    print(f"  baris dipindai       : {totals['rows_scanned']}")
    print(f"  record terpetakan    : {totals['records']}")
    print(f"  duplikat dibuang     : {totals['duplicates_removed']}")
    print(f"  record >= Okt 2026   : {len(in_scope)}")
    if not in_scope.empty:
        counts = in_scope.groupby(in_scope["period"].astype(str)).size()
        for period, count in counts.items():
            print(f"      {period}: {count} record")
    for note in notes:
        print(f"  catatan              : {note}")
    print("  status               : SIAP")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "preflight":
        return _preflight()
    print("Pemakaian: python -m src.mr_sources preflight")
    return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
