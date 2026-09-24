"""
preparation.py - DATA PREPARATION & VALIDATION LAYER

Mengubah raw values dari sheets.py menjadi dataframe analitik.

Yang sudah diisi:
    df_mr                                            (Phase 3)
    df_partner, df_conversion                        (Phase 5)
    df_financial, df_inkind                          (Phase 6-7)

Perhitungan KPI atas dataframe ini ada di metrics.py, bukan di sini.

--------------------------------------------------------------------------
ATURAN BISNIS df_mr - dikonfirmasi BD setelah inspeksi Phase 2
--------------------------------------------------------------------------
1. Header row ada di baris 1. Kolom dicari lewat NAMA header, bukan
   posisi tetap, supaya tidak rusak kalau ada kolom disisipkan.

2. Sheet tersusun bertumpuk per bulan. Baris penanda ada di kolom NO
   dan berisi nama bulan (di sumber ditulis dengan emoji, mis. "(emoji)april").
   Bulan sebuah record = penanda terdekat DI ATAS baris itu.
   Alasan: DATE OF MR hanya terisi 37,9% dan sebagian diisi massal
   dengan tanggal identik, jadi tidak bisa dipakai menentukan bulan.

3. Baris dianggap RECORD kalau kolom NO berisi angka murni DAN
   nama partner terisi. Ini menyaring baris legend, baris summary,
   dan baris penanda bulan.

4. February & March DIBUANG. Isi kedua bulan itu hasil copy-paste
   ke ketiga tab (178 dari 178 kunci february identik antara EWAs
   dan SS, 351 dari 355 bahkan PIC-nya sama), jadi kalau ikut
   digabung akan terhitung ganda.

5. ELDs / EWAs / SS adalah TIGA TIM berbeda. Partner yang sama
   diteliti dua tim = dua aktivitas MR yang sah, bukan duplikat.
   Karena itu penggabungan ketiga tab TIDAK di-dedupe.

6. Total MR = jumlah kemunculan nama PIC di kolom PIC FROM AIESEC.

7. Duplikat tidak pernah dihapus otomatis. Nilai yang hilang tidak
   pernah diisi karangan. Kolom mentah tetap dibawa untuk audit.
"""

from __future__ import annotations

import re

import pandas as pd

from . import sheets

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

# Header row hasil verifikasi Phase 2 (1-based seperti tampilan Google Sheets).
HEADER_ROW_NUMBER = 1

# Kolom NO dipakai untuk dua hal: mengenali baris record (isinya angka)
# dan mengenali penanda bulan (isinya nama bulan).
NO_COLUMN_HEADER = "no"

# Nama header yang dicari, sudah dinormalisasi (huruf kecil, spasi rapat).
EXPECTED_HEADERS: dict[str, str] = {
    "no": NO_COLUMN_HEADER,
    "partner_name": "partner's name",
    "pic_aiesec": "pic from aiesec",
    "date_of_mr": "date of mr",
}

# Urutan bulan mengikuti masa jabatan AIESEC: mulai February, berakhir January.
TERM_MONTH_ORDER: tuple[str, ...] = (
    "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "january",
)

MONTH_TOKENS = frozenset(TERM_MONTH_ORDER) | frozenset(
    {
        "januari", "februari", "maret", "mei", "juni",
        "juli", "agustus", "oktober", "nopember", "desember",
    }
)

# Padanan nama bulan Indonesia -> Inggris, supaya labelnya konsisten.
MONTH_ALIASES: dict[str, str] = {
    "januari": "january", "februari": "february", "maret": "march",
    "mei": "may", "juni": "june", "juli": "july",
    "agustus": "august", "oktober": "october",
    "nopember": "november", "desember": "december",
}

# Keputusan BD: dua bulan ini tidak dipakai (lihat aturan 4 di docstring).
EXCLUDED_MONTHS: tuple[str, ...] = ("february", "march")

# Nilai error Google Sheets. Diperlakukan sebagai "tidak ada nilai".
SPREADSHEET_ERRORS = frozenset(
    {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#ERROR!"}
)

# Karakter tak terlihat yang benar-benar ada di ESSM (word joiner, zero-width
# space, BOM). Kalau dibiarkan, dua nilai yang tampak sama jadi dianggap beda.
INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

# Format tanggal yang ditemukan di sumber. Sengaja eksplisit, bukan
# tebak-tebakan otomatis, supaya salah-baca hari/bulan tidak terjadi diam-diam.
DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%m/%d/%Y")


# ---------------------------------------------------------------------------
# Util nilai sel
# ---------------------------------------------------------------------------


def clean_text(value) -> str:
    """Rapikan teks sel: buang karakter tak terlihat, rapatkan spasi.

    Nilai kosong pandas (None, NaN, pd.NA) dikembalikan sebagai string
    kosong. Tanpa ini, NaN akan berubah menjadi teks "nan" dan dianggap
    data yang valid.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(INVISIBLE_CHARS.sub("", str(value)).split())


def is_sheet_error(value: str) -> bool:
    return clean_text(value).upper() in SPREADSHEET_ERRORS


def has_value(value: str) -> bool:
    """True kalau sel benar-benar berisi data (bukan kosong, bukan error)."""
    text = clean_text(value)
    return bool(text) and text.upper() not in SPREADSHEET_ERRORS


def normalize_header(value: str) -> str:
    return clean_text(value).lower()


def is_record_number(value: str) -> bool:
    """True kalau sel kolom NO berisi angka urut murni, mis. '1', '27'."""
    return bool(re.fullmatch(r"\d+", clean_text(value)))


def month_in_cell(value: str) -> str | None:
    """Kembalikan nama bulan (Inggris) kalau sel berisi nama bulan utuh.

    Dicocokkan per kata supaya 'Mayora' tidak dibaca sebagai bulan 'May'.
    """
    for word in re.findall(r"[a-z]+", clean_text(value).lower()):
        if word in MONTH_TOKENS:
            return MONTH_ALIASES.get(word, word)
    return None


def cell(grid: list[list[str]], row_index: int, col_index: int) -> str:
    """Ambil sel dengan aman; baris di Sheets bisa lebih pendek dari header."""
    if row_index >= len(grid):
        return ""
    row = grid[row_index]
    return row[col_index] if col_index < len(row) else ""


# ---------------------------------------------------------------------------
# Resolusi kolom
# ---------------------------------------------------------------------------


def resolve_columns(headers: list[str]) -> dict[str, int]:
    """Cari index kolom berdasarkan NAMA header, bukan posisi tetap.

    Raises:
        KeyError kalau ada header wajib yang tidak ditemukan. Sengaja gagal
        keras: lebih baik berhenti daripada menghasilkan angka yang salah.
    """
    normalized = [normalize_header(name) for name in headers]
    resolved: dict[str, int] = {}
    missing: list[str] = []

    for field, expected in EXPECTED_HEADERS.items():
        try:
            resolved[field] = normalized.index(expected)
        except ValueError:
            missing.append(f"{field} (dicari header: {expected!r})")

    if missing:
        raise KeyError(
            "Header wajib tidak ditemukan di baris "
            f"{HEADER_ROW_NUMBER}: {'; '.join(missing)}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Ekstraksi record
# ---------------------------------------------------------------------------


def extract_mr_records(grid: list[list[str]], source_tab: str) -> list[dict]:
    """Ambil record Market Research dari satu tab.

    Menelusuri sheet dari atas ke bawah sambil mengingat bulan yang
    sedang berlaku (dari baris penanda). Setiap baris record diberi
    bulan tersebut.
    """
    if not grid:
        return []

    header_index = HEADER_ROW_NUMBER - 1
    columns = resolve_columns(grid[header_index])

    records: list[dict] = []
    current_month: str | None = None

    for row_index in range(header_index, len(grid)):
        no_value = cell(grid, row_index, columns["no"])

        # Baris penanda bulan: ganti konteks bulan, jangan dijadikan record.
        marker_month = month_in_cell(no_value)
        if marker_month is not None:
            current_month = marker_month
            continue

        partner_value = cell(grid, row_index, columns["partner_name"])
        if not (is_record_number(no_value) and has_value(partner_value)):
            continue

        pic_value = cell(grid, row_index, columns["pic_aiesec"])
        date_value = cell(grid, row_index, columns["date_of_mr"])

        records.append(
            {
                "partner_name": clean_text(partner_value),
                "pic_aiesec": clean_text(pic_value) if has_value(pic_value) else None,
                "date_of_mr_raw": clean_text(date_value) if has_value(date_value) else None,
                "month": current_month,
                "source_tab": source_tab,
                "source_row": row_index + 1,
            }
        )

    return records


# ---------------------------------------------------------------------------
# Parsing tanggal
# ---------------------------------------------------------------------------


def parse_mr_dates(raw: pd.Series) -> pd.Series:
    """Parse tanggal dari beberapa format yang memang ada di sumber.

    Sumber memakai campuran '2026-02-19' (ISO) dan '4/20/2026' (M/D/YYYY).
    Kita coba format satu per satu secara eksplisit. Nilai yang tidak
    cocok format apa pun menjadi NaT - tidak ditebak, tidak diisi.
    """
    parsed = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    for date_format in DATE_FORMATS:
        remaining = parsed.isna()
        if not remaining.any():
            break
        attempt = pd.to_datetime(
            raw[remaining], format=date_format, errors="coerce"
        )
        parsed[remaining] = attempt
    return parsed


# ---------------------------------------------------------------------------
# Pembangun df_mr
# ---------------------------------------------------------------------------


def build_df_mr(
    exclude_months: tuple[str, ...] = EXCLUDED_MONTHS,
) -> pd.DataFrame:
    """Bangun df_mr dari ketiga tab Market Research Active ESSM.

    Args:
        exclude_months: bulan yang dibuang. Default ('february', 'march')
            sesuai keputusan BD. Bisa dikosongkan untuk audit.

    Returns:
        DataFrame dengan kolom:
            partner_name, pic_aiesec, date_of_mr, date_of_mr_raw,
            month, source_tab, source_row
    """
    worksheets = sheets.open_active_mr_worksheets()

    records: list[dict] = []
    for worksheet in worksheets:
        grid = sheets.read_worksheet_values(worksheet)
        records.extend(extract_mr_records(grid, worksheet.title))

    df = pd.DataFrame.from_records(
        records,
        columns=[
            "partner_name",
            "pic_aiesec",
            "date_of_mr_raw",
            "month",
            "source_tab",
            "source_row",
        ],
    )

    if df.empty:
        return df

    df["date_of_mr"] = parse_mr_dates(df["date_of_mr_raw"])

    # Buang bulan yang diputuskan tidak dipakai.
    excluded = {month.lower() for month in exclude_months}
    df = df[~df["month"].isin(excluded)].copy()

    # Bulan jadi kategori berurutan supaya grafik dan tabel terurut
    # menurut masa jabatan (April -> ... -> January), bukan alfabetis.
    month_categories = [m for m in TERM_MONTH_ORDER if m not in excluded]
    df["month"] = pd.Categorical(df["month"], categories=month_categories, ordered=True)

    df = df[
        [
            "partner_name",
            "pic_aiesec",
            "date_of_mr",
            "date_of_mr_raw",
            "month",
            "source_tab",
            "source_row",
        ]
    ]
    return df.sort_values(["month", "source_tab", "source_row"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Validasi
# ---------------------------------------------------------------------------


def find_duplicate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Kumpulkan record yang persis sama pada bulan+partner+PIC+tab.

    TIDAK menghapus apa pun. Hanya melaporkan supaya bisa diperiksa manual.
    """
    keys = ["month", "partner_name", "pic_aiesec", "source_tab"]
    duplicated_mask = df.duplicated(subset=keys, keep=False)
    return df[duplicated_mask].sort_values(keys + ["source_row"])


def find_multi_pic_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Cari sel PIC yang tampaknya memuat lebih dari satu nama.

    Penting karena Total MR dihitung dari kemunculan nama PIC. Kalau satu
    sel berisi dua nama, satu baris seharusnya bernilai dua MR.
    """
    pattern = r"[,/;&]|\band\b|\bdan\b"
    pic = df["pic_aiesec"].fillna("")
    return df[pic.str.contains(pattern, case=False, regex=True, na=False)]


# ===========================================================================
# NATIONAL 1.1 - PARTNER & CONVERSION RATE (Phase 5)
# ===========================================================================
#
# ATURAN BISNIS - dikonfirmasi BD setelah inspeksi Phase 4
# ---------------------------------------------------------------------------
# 1. Header di National_1.1 BERLAPIS TIGA. Nama kolom tersebar:
#       baris 1 : PARTNER'S NAME, STAKEHODLER GROUPING, Status (For new sales)
#       baris 2 : tahap funnel (1. Prospecting ... 5. Contract Signed)
#       baris 3 : Link, Link Invoice, Month of Signed, Month end
#    Karena itu pencarian kolom dilakukan di ketiga baris, bukan satu baris.
#
# 2. Kolom dokumen semuanya berlabel "Link" (tiga kolom, nama sama).
#    Dibedakan dari tahap funnel yang berada TEPAT SEBELUMNYA:
#       Link setelah "2. Proposal Created & Sent" -> dokumen Proposal
#       Link setelah "3. Sales Meeting"           -> dokumen MoM
#       Link setelah "5. Contract Signed"         -> dokumen LoA
#    Aturan ini diverifikasi terhadap isi selnya (lihat validate_document_columns).
#
# 3. Dokumen dianggap ADA kalau selnya terisi DAN bukan penanda kosong.
#    Nilai "-" di sheet ini berarti "tidak ada", bukan nama dokumen.
#
# 4. Month of Signed & Month End hanya level BULAN, format "MON YY"
#    (mis. "FEB 26", "JAN 27"). Tidak ada tanggal harian.
#    Asumsi disetujui BD: bulan diperlakukan sebagai AKHIR bulan.
#    Jadi "SEP 26" = 30 September 2026.
#
# 5. ACTIVE PARTNER = punya dokumen LoA DAN Month End belum terlewat
#    (Month End >= bulan berjalan). Kolom "Status (For new sales)" TIDAK
#    dipakai untuk ini karena masih menandai Active walau kontrak sudah
#    berakhir. Nilainya tetap dibawa sebagai status_source untuk audit.
#
# 6. February & March TETAP dipakai di data partner (berbeda dari df_mr),
#    karena National_1.1 hanya satu tab sehingga tidak ada hitung ganda.
#
# 7. Conversion rate DIAMBIL dari sheet, tidak pernah dihitung ulang.
#    Sumbernya: nilai persen di baris ringkasan (baris 3 = seluruh periode)
#    dan di setiap baris penanda bulan.
# ---------------------------------------------------------------------------

# Baris yang memuat nama kolom di National_1.1 (1-based).
PARTNER_HEADER_ROWS = (1, 2, 3)

# Baris ringkasan seluruh periode.
PARTNER_TOTAL_ROW = 3

# Label yang dicari, sudah dinormalisasi.
PARTNER_LABELS: dict[str, str] = {
    "no": "no",
    "partner_name": "partner's name",
    "stakeholder": "stakehodler grouping",  # typo memang ada di sumber
    "sales_status": "sales status",
    "status_source": "status (for new sales)",
    "invoice": "link invoice",
    "signed_month": "month of signed",
    "end_month": "month end",
}

# Tahap funnel, sesuai sub-header baris 2. Urutan = urutan funnel.
FUNNEL_STAGES: tuple[str, ...] = (
    "1. prospecting",
    "1. outreaching",
    "2. reply",
    "2. proposal created & sent",
    "3. sales meeting",
    "4. follow-up",
    "5. contract signed",
)

# Tahap yang dipakai sebagai Sales Funnel Conversion Rate (disetujui BD).
CONVERSION_STAGE = "5. contract signed"

# Kolom dokumen berlabel "Link"; dikenali dari tahap funnel sebelumnya.
DOCUMENT_AFTER_STAGE: dict[str, str] = {
    "proposal": "2. proposal created & sent",
    "mom": "3. sales meeting",
    "loa": "5. contract signed",
}
DOCUMENT_LINK_LABEL = "link"

# Nama kolom dataframe untuk setiap tahap funnel. Dipisah dari label sumber
# supaya nama kolom tetap rapi walau label di sheet berubah/panjang.
FUNNEL_STAGE_SLUGS: dict[str, str] = {
    "1. prospecting": "stage_prospecting",
    "1. outreaching": "stage_outreaching",
    "2. reply": "stage_reply",
    "2. proposal created & sent": "stage_proposal_sent",
    "3. sales meeting": "stage_sales_meeting",
    "4. follow-up": "stage_follow_up",
    "5. contract signed": "stage_contract_signed",
}

# Semua kolom dokumen yang dilacak Document Tracker.
DOCUMENT_FIELDS: tuple[str, ...] = ("proposal", "mom", "loa", "invoice")

# Nilai yang berarti "tidak ada dokumen", bukan nama dokumen.
DOCUMENT_ABSENT_MARKERS = frozenset({"-", "--", "n/a", "na", "none", "tbc", "tba", "x"})

# Format Month of Signed / Month End di sumber: "FEB 26", "AUG 25".
MONTH_YEAR_FORMAT = "%b %y"


def load_partner_grid() -> list[list[str]]:
    """Baca National_1.1 dari mirror satu kali, kembalikan raw values."""
    spreadsheet = sheets.open_national_mirror()
    worksheet = sheets.get_worksheet(spreadsheet, sheets.NATIONAL_PARTNER_WORKSHEET)
    return sheets.read_worksheet_values(worksheet)


def _label_positions(grid: list[list[str]]) -> dict[str, list[int]]:
    """Petakan setiap label header (baris 1-3) ke daftar index kolomnya."""
    positions: dict[str, list[int]] = {}
    for row_number in PARTNER_HEADER_ROWS:
        row_index = row_number - 1
        if row_index >= len(grid):
            continue
        for col_index, raw in enumerate(grid[row_index]):
            label = normalize_header(raw)
            if label:
                positions.setdefault(label, []).append(col_index)
    return positions


def resolve_partner_columns(grid: list[list[str]]) -> dict[str, int]:
    """Cari index kolom National_1.1 dari header berlapis tiga.

    Raises:
        KeyError kalau ada kolom wajib yang tidak ditemukan. Sengaja gagal
        keras supaya tidak menghasilkan KPI dari kolom yang salah.
    """
    positions = _label_positions(grid)
    resolved: dict[str, int] = {}
    missing: list[str] = []

    for field, label in PARTNER_LABELS.items():
        found = positions.get(label)
        if not found:
            missing.append(f"{field} (label dicari: {label!r})")
            continue
        resolved[field] = found[0]

    # Tahap funnel dari sub-header baris 2.
    for stage in FUNNEL_STAGES:
        found = positions.get(stage)
        if not found:
            missing.append(f"tahap funnel {stage!r}")
            continue
        resolved[f"stage::{stage}"] = found[0]

    # Kolom dokumen "Link": ambil yang pertama SETELAH tahap acuannya.
    link_columns = sorted(positions.get(DOCUMENT_LINK_LABEL, []))
    for document, stage in DOCUMENT_AFTER_STAGE.items():
        stage_column = resolved.get(f"stage::{stage}")
        if stage_column is None:
            continue
        after = [col for col in link_columns if col > stage_column]
        if not after:
            missing.append(f"kolom dokumen {document!r} (Link setelah {stage!r})")
            continue
        resolved[document] = after[0]

    if missing:
        raise KeyError(
            "Kolom National_1.1 tidak ditemukan: " + "; ".join(missing)
        )
    return resolved


def has_document(value: str | None) -> bool:
    """True kalau sel dokumen benar-benar menunjuk sesuatu.

    "-" dan penanda sejenis berarti TIDAK ADA, bukan nama dokumen.
    """
    text = clean_text(value or "")
    if not text or text.upper() in SPREADSHEET_ERRORS:
        return False
    return text.casefold() not in DOCUMENT_ABSENT_MARKERS


def parse_month_year(value: str | None):
    """Ubah "FEB 26" menjadi Period bulanan 2026-02.

    Return pd.NaT kalau tidak bisa diurai. Tidak pernah menebak.
    """
    text = clean_text(value or "")
    if not text:
        return pd.NaT
    try:
        timestamp = pd.to_datetime(text.title(), format=MONTH_YEAR_FORMAT)
    except (ValueError, TypeError):
        return pd.NaT
    return timestamp.to_period("M")


def period_to_month_end(period):
    """Ambil hari TERAKHIR dari sebuah bulan.

    Asumsi disetujui BD: sumber hanya memberi bulan, dan bulan itu
    diperlakukan sebagai akhir bulan. "SEP 26" -> 2026-09-30.
    """
    if period is pd.NaT or pd.isna(period):
        return pd.NaT
    return period.to_timestamp(how="end").normalize()


def extract_partner_records(grid: list[list[str]]) -> list[dict]:
    """Ambil baris record partner dari National_1.1.

    Aturan record sama seperti df_mr: kolom NO berisi angka murni DAN
    nama partner terisi. Bulan diambil dari penanda section di atasnya.
    """
    columns = resolve_partner_columns(grid)

    records: list[dict] = []
    current_month: str | None = None

    for row_index in range(len(grid)):
        no_value = cell(grid, row_index, columns["no"])

        marker_month = month_in_cell(no_value)
        if marker_month is not None:
            current_month = marker_month
            continue

        partner_value = cell(grid, row_index, columns["partner_name"])
        if not (is_record_number(no_value) and has_value(partner_value)):
            continue

        record: dict = {
            "partner_name": clean_text(partner_value),
            "stakeholder": clean_text(cell(grid, row_index, columns["stakeholder"])),
            "sales_status": clean_text(cell(grid, row_index, columns["sales_status"])),
            "status_source": clean_text(cell(grid, row_index, columns["status_source"])),
            "month": current_month,
            "source_row": row_index + 1,
        }

        # Flag tahap funnel (TRUE/FALSE di sumber), disimpan apa adanya.
        for stage in FUNNEL_STAGES:
            raw = clean_text(cell(grid, row_index, columns[f"stage::{stage}"]))
            record[FUNNEL_STAGE_SLUGS[stage]] = raw or None
        record["contract_signed"] = (
            clean_text(cell(grid, row_index, columns[f"stage::{CONVERSION_STAGE}"])).upper()
            == "TRUE"
        )

        # Dokumen: simpan nilai mentah DAN hasil penilaian ada/tidak.
        for document in DOCUMENT_FIELDS:
            raw = clean_text(cell(grid, row_index, columns[document]))
            record[f"{document}_raw"] = raw or None
            record[f"has_{document}"] = has_document(raw)

        record["signed_month_raw"] = (
            clean_text(cell(grid, row_index, columns["signed_month"])) or None
        )
        record["end_month_raw"] = (
            clean_text(cell(grid, row_index, columns["end_month"])) or None
        )

        records.append(record)

    return records


def apply_reference_date(
    df: pd.DataFrame, reference_date: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Hitung ulang kolom yang bergantung pada TANGGAL ACUAN.

    Status aktif adalah KPI point-in-time: "aktif" hanya punya arti
    relatif terhadap satu tanggal. Fungsi ini dipisahkan supaya
    dashboard bisa bertanya "siapa yang aktif per akhir Agustus?"
    tanpa menarik ulang data dari Sheets.

    Kolom yang dihitung ulang: is_ongoing, is_active, months_remaining.
    Kolom lain tidak disentuh. Dataframe asli tidak diubah.
    """
    if df.empty:
        return df

    if reference_date is None:
        reference_date = pd.Timestamp.today().normalize()

    required = ("end_month", "has_loa")
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise KeyError(f"df_partner tidak punya kolom: {', '.join(missing)}")

    result = df.copy()
    reference_period = pd.Timestamp(reference_date).normalize().to_period("M")

    def not_yet_ended(period) -> bool:
        if period is pd.NaT or pd.isna(period):
            return False
        return period >= reference_period

    result["is_ongoing"] = result["end_month"].map(not_yet_ended)

    # ATURAN BD: partner aktif = ada LoA DAN kontrak belum berakhir.
    result["is_active"] = result["has_loa"] & result["is_ongoing"]

    result["months_remaining"] = pd.array(
        [
            (period - reference_period).n
            if period is not pd.NaT and not pd.isna(period)
            else pd.NA
            for period in result["end_month"]
        ],
        dtype="Int64",
    )
    # Disimpan sebagai teks ISO, bukan Timestamp: Streamlit mencoba
    # men-serialisasi df.attrs ke JSON saat menampilkan tabel, dan Timestamp
    # membuatnya memunculkan peringatan setiap kali.
    result.attrs["reference_date"] = str(pd.Timestamp(reference_date).normalize().date())
    return result


def build_df_partner(
    grid: list[list[str]] | None = None,
    reference_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Bangun df_partner dari National_1.1.

    Args:
        grid: raw values National_1.1. Kalau None, dibaca dari mirror.
        reference_date: tanggal acuan untuk menentukan partner aktif.
            Default hari ini. Dibuat parameter supaya bisa diuji.

    Returns:
        DataFrame berisi identitas partner, status dokumen, periode
        kontrak, dan kolom turunan is_active.
    """
    if grid is None:
        grid = load_partner_grid()

    df = pd.DataFrame.from_records(extract_partner_records(grid))
    if df.empty:
        return df

    df["signed_month"] = df["signed_month_raw"].map(parse_month_year)
    df["end_month"] = df["end_month_raw"].map(parse_month_year)
    df["end_date"] = df["end_month"].map(period_to_month_end)

    df = apply_reference_date(df, reference_date)

    month_categories = list(TERM_MONTH_ORDER)
    df["month"] = pd.Categorical(df["month"], categories=month_categories, ordered=True)

    return df.sort_values(["month", "source_row"]).reset_index(drop=True)


def validate_document_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Uji apakah kolom dokumen benar-benar berisi dokumen yang diharapkan.

    Aturan pemetaan kolom "Link" berbasis posisi (lihat aturan 2). Uji ini
    mencocokkan isi selnya dengan kata kunci yang seharusnya muncul, supaya
    salah-petakan tidak lolos diam-diam.
    """
    expectation = {
        "proposal": "proposal",
        "mom": "mom",
        "loa": r"loa|contract|signed|partner",
    }
    rows = []
    for document, pattern in expectation.items():
        raw = df[f"{document}_raw"].dropna()
        filled = raw[raw.map(has_document)]
        matched = filled.str.contains(pattern, case=False, regex=True)
        rows.append(
            {
                "kolom": document,
                "terisi": len(filled),
                "cocok_kata_kunci": int(matched.sum()),
                "tidak_cocok": int((~matched).sum()),
                "contoh_tidak_cocok": filled[~matched].head(3).tolist(),
            }
        )
    return pd.DataFrame(rows)


# ===========================================================================
# NATIONAL 1.3 & 1.4 - FINANCIAL REVENUE & IN-KIND VALUE (Phase 6 & 7)
# ===========================================================================
#
# Kedua tab strukturnya IDENTIK, jadi parser-nya dibagi.
#
# ATURAN BISNIS
# ---------------------------------------------------------------------------
# 1. Header berlapis dua: baris 1 (NO, MONTHS, PARTNER'S NAME) dan
#    baris 2 (Date Received, Total Revenue / In Kind Value, Proof).
#    Baris 3 adalah baris TOTAL, bukan record.
#
# 2. Sheet dibagi per KUARTAL (bukan per bulan seperti tab lain).
#    Penanda: kolom MONTHS berisi "QUARTER #n". Total kuartal tersimpan
#    di kolom nilai pada baris penanda itu.
#
# 3. Bulan sebuah record diambil dari KOLOM "MONTHS", bukan dari penanda
#    section dan bukan dari Date Received. Alasan: kolom MONTHS selalu
#    terisi, sedangkan Date Received ada yang kosong dan formatnya campur.
#
# 4. Date Received formatnya TIDAK KONSISTEN di sumber. Ditemukan
#    "5/31/2026" (M/D/Y) dan "13/8/2026" (D/M/Y) di tab yang sama.
#    Karena itu tanggal diurai dengan mencoba kedua format, lalu
#    hasilnya dicocokkan dengan kolom MONTHS. Setiap keputusan dicatat
#    di kolom date_note supaya bisa diaudit - tidak ada yang ditebak diam-diam.
#
# 5. Nilai uang di sumber berformat "Rp26,750,000.00". Diubah ke angka,
#    nilai mentahnya tetap disimpan.
#
# 6. Financial Revenue dan In-Kind Value TIDAK PERNAH dijumlahkan menjadi
#    satu "Total Revenue". Keduanya metrik terpisah.
# ---------------------------------------------------------------------------

# Baris TOTAL keseluruhan di kedua tab (1-based).
REVENUE_TOTAL_ROW = 3

# Baris yang memuat nama kolom.
REVENUE_HEADER_ROWS = (1, 2)

FINANCIAL_LABELS: dict[str, str] = {
    "no": "no",
    "month": "months",
    "partner_name": "partner's name",
    "date_received": "date received",
    "amount": "total revenue",
    "proof": "proof",
}

INKIND_LABELS: dict[str, str] = {
    "no": "no",
    "month": "months",
    "partner_name": "partner's name",
    "date_received": "date received",
    "type": "type of in-kind",
    "description": "in kind list",
    "amount": "in kind value",
    "proof": "proof",
}

# Format tanggal yang dicoba untuk Date Received, berurutan.
RECEIVED_DATE_FORMATS: tuple[str, ...] = ("%m/%d/%Y", "%d/%m/%Y")

# Pola angka uang.
MONEY_US_STYLE = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$")   # 26,750,000.00
MONEY_ID_STYLE = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$")   # 26.750.000,00
MONEY_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
CURRENCY_PREFIX = re.compile(r"^(rp|idr)\s*", re.IGNORECASE)


def parse_money(value: str | None) -> float:
    """Ubah nilai uang berformat menjadi angka.

    Menangani dua gaya penulisan secara eksplisit, bukan menebak:
        "Rp26,750,000.00" -> 26750000.0   (ribuan koma, desimal titik)
        "Rp26.750.000,00" -> 26750000.0   (ribuan titik, desimal koma)

    Nilai yang tidak cocok pola apa pun menjadi NaN supaya bisa
    dilaporkan, bukan diam-diam dianggap nol.
    """
    text = clean_text(value or "")
    if not text:
        return float("nan")
    text = CURRENCY_PREFIX.sub("", text).replace(" ", "")
    if text in {"-", "--"}:
        return float("nan")

    if MONEY_US_STYLE.match(text):
        return float(text.replace(",", ""))
    if MONEY_ID_STYLE.match(text):
        return float(text.replace(".", "").replace(",", "."))
    if MONEY_PLAIN.match(text):
        return float(text)
    return float("nan")


def _valid_date_interpretations(text: str) -> dict[str, pd.Timestamp]:
    """Kembalikan semua tafsiran tanggal yang valid untuk sebuah teks."""
    parsed: dict[str, pd.Timestamp] = {}
    for date_format in RECEIVED_DATE_FORMATS:
        try:
            timestamp = pd.to_datetime(text, format=date_format)
        except (ValueError, TypeError):
            continue
        # pandas bisa mengembalikan NaT untuk tanggal tak valid
        # (mis. "5/31/2026" dibaca sebagai D/M/Y -> bulan 31) tanpa
        # melempar error, jadi hasilnya harus disaring di sini.
        if timestamp is pd.NaT or pd.isna(timestamp):
            continue
        parsed[date_format] = timestamp
    return parsed


def detect_dominant_date_format(raw_values) -> str:
    """Tentukan format tanggal yang dominan dipakai di SEBUAH tab.

    Caranya memakai bukti dari data itu sendiri: hanya nilai yang
    tafsirannya TUNGGAL yang dihitung sebagai bukti. Contohnya
    "13/8/2026" hanya mungkin D/M/Y, jadi itu bukti kuat.

    Nilai ambigu seperti "1/9/2026" tidak ikut memilih, justru
    nilai itulah yang nanti diputuskan memakai format dominan ini.

    Kenapa perlu: tab In-Kind memakai D/M/Y untuk 8 nilai yang jelas,
    jadi menebak M/D/Y untuk nilai ambigu di tab itu akan salah bulan.
    """
    votes = {date_format: 0 for date_format in RECEIVED_DATE_FORMATS}
    for raw in raw_values:
        text = clean_text(raw)
        if not text:
            continue
        parsed = _valid_date_interpretations(text)
        if len(parsed) == 1:
            votes[next(iter(parsed))] += 1

    best = max(votes, key=lambda fmt: votes[fmt])
    # Kalau tidak ada bukti sama sekali, pakai urutan default.
    return best if votes[best] else RECEIVED_DATE_FORMATS[0]


def format_label(date_format: str) -> str:
    return "M/D/Y" if date_format == "%m/%d/%Y" else "D/M/Y"


def parse_received_date(
    raw: str | None,
    month_hint: str | None = None,
    preferred_format: str | None = None,
):
    """Urai Date Received yang formatnya campur di sumber.

    Strategi, dari bukti terkuat ke terlemah:
        1. Kalau hanya satu format yang valid, pakai itu.
        2. Kalau dua-duanya valid (mis. "2/10/2026" bisa Feb-10 atau Okt-2),
           pilih yang bulannya cocok dengan kolom MONTHS.
        3. Kalau masih ambigu, pakai format DOMINAN di tab tersebut
           (lihat detect_dominant_date_format), bukan tebakan tetap.

    Returns:
        (timestamp_atau_NaT, catatan) - catatan selalu menjelaskan
        keputusan yang diambil supaya bisa diaudit.
    """
    text = clean_text(raw)
    if not text:
        return pd.NaT, "kosong"

    parsed = _valid_date_interpretations(text)
    if not parsed:
        return pd.NaT, "gagal diurai"

    if len(parsed) == 1:
        date_format, timestamp = next(iter(parsed.items()))
        return timestamp, f"hanya cocok {format_label(date_format)}"

    hint = clean_text(month_hint).lower()
    if hint:
        for date_format, timestamp in parsed.items():
            if timestamp.strftime("%B").lower() == hint:
                return (
                    timestamp,
                    f"ambigu, dipilih {format_label(date_format)} (cocok kolom MONTHS)",
                )

    fallback = preferred_format or RECEIVED_DATE_FORMATS[0]
    if fallback not in parsed:
        fallback = next(iter(parsed))
    return (
        parsed[fallback],
        f"ambigu, dipakai format dominan tab ({format_label(fallback)})",
    )


def load_revenue_grid(worksheet_name: str) -> list[list[str]]:
    """Baca satu tab revenue (National_1.3 atau National_1.4) dari mirror."""
    spreadsheet = sheets.open_national_mirror()
    worksheet = sheets.get_worksheet(spreadsheet, worksheet_name)
    return sheets.read_worksheet_values(worksheet)


def _resolve_revenue_columns(
    grid: list[list[str]], labels: dict[str, str]
) -> dict[str, int]:
    """Cari index kolom dari header baris 1-2."""
    positions: dict[str, int] = {}
    for row_number in REVENUE_HEADER_ROWS:
        row_index = row_number - 1
        if row_index >= len(grid):
            continue
        for col_index, raw in enumerate(grid[row_index]):
            label = normalize_header(raw)
            if label and label not in positions:
                positions[label] = col_index

    resolved: dict[str, int] = {}
    missing: list[str] = []
    for field, label in labels.items():
        if label in positions:
            resolved[field] = positions[label]
        else:
            missing.append(f"{field} (label dicari: {label!r})")

    if missing:
        raise KeyError("Kolom tidak ditemukan: " + "; ".join(missing))
    return resolved


def _extract_revenue_records(
    grid: list[list[str]], labels: dict[str, str]
) -> list[dict]:
    """Ambil record dari tab revenue, sekaligus mencatat kuartalnya."""
    columns = _resolve_revenue_columns(grid, labels)
    optional_fields = ("type", "description", "proof")

    records: list[dict] = []
    current_quarter: str | None = None

    for row_index in range(len(grid)):
        no_value = clean_text(cell(grid, row_index, columns["no"]))
        month_value = clean_text(cell(grid, row_index, columns["month"]))

        # Baris penanda kuartal: kolom MONTHS berisi "QUARTER #n".
        if month_value.upper().startswith("QUARTER"):
            current_quarter = month_value.upper()
            continue

        partner_value = cell(grid, row_index, columns["partner_name"])
        if not (is_record_number(no_value) and has_value(partner_value)):
            continue

        month_label = month_value.lower()
        record: dict = {
            "month_raw": month_value or None,
            "month": MONTH_ALIASES.get(month_label, month_label) or None,
            "partner_name": clean_text(partner_value),
            "quarter": current_quarter,
            "date_received_raw": clean_text(cell(grid, row_index, columns["date_received"]))
            or None,
            "amount_raw": clean_text(cell(grid, row_index, columns["amount"])) or None,
            "source_row": row_index + 1,
        }
        for field in optional_fields:
            if field in columns:
                record[field] = clean_text(cell(grid, row_index, columns[field])) or None

        records.append(record)

    return records


def _build_revenue_frame(
    grid: list[list[str]], labels: dict[str, str], amount_column: str
) -> pd.DataFrame:
    """Rangka bersama untuk df_financial dan df_inkind."""
    df = pd.DataFrame.from_records(_extract_revenue_records(grid, labels))
    if df.empty:
        return df

    df[amount_column] = df["amount_raw"].map(parse_money)

    # Format tanggal ditentukan dari bukti di tab ini sendiri, bukan
    # diasumsikan sama untuk semua tab.
    dominant_format = detect_dominant_date_format(df["date_received_raw"])

    parsed = [
        parse_received_date(raw, hint, preferred_format=dominant_format)
        for raw, hint in zip(df["date_received_raw"], df["month_raw"])
    ]
    df["date_received"] = [item[0] for item in parsed]
    df["date_note"] = [item[1] for item in parsed]
    df.attrs["dominant_date_format"] = format_label(dominant_format)

    df["month"] = pd.Categorical(
        df["month"], categories=list(TERM_MONTH_ORDER), ordered=True
    )

    return df.sort_values(["month", "source_row"]).reset_index(drop=True)


def build_df_financial(grid: list[list[str]] | None = None) -> pd.DataFrame:
    """Bangun df_financial dari National_1.3 (Financial Revenue)."""
    if grid is None:
        grid = load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
    return _build_revenue_frame(grid, FINANCIAL_LABELS, "revenue")


def build_df_inkind(grid: list[list[str]] | None = None) -> pd.DataFrame:
    """Bangun df_inkind dari National_1.4 (In-Kind Value)."""
    if grid is None:
        grid = load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
    return _build_revenue_frame(grid, INKIND_LABELS, "inkind_value")


def read_sheet_totals(
    grid: list[list[str]], labels: dict[str, str]
) -> dict[str, float]:
    """Ambil total yang SUDAH dihitung sheet, untuk cek silang.

    Angka ini tidak dipakai sebagai KPI - hanya pembanding terhadap
    hasil penjumlahan kita sendiri.
    """
    columns = _resolve_revenue_columns(grid, labels)
    amount_col = columns["amount"]
    month_col = columns["month"]

    totals: dict[str, float] = {
        "TOTAL": parse_money(cell(grid, REVENUE_TOTAL_ROW - 1, amount_col))
    }
    for row_index in range(len(grid)):
        month_value = clean_text(cell(grid, row_index, month_col))
        if month_value.upper().startswith("QUARTER"):
            totals[month_value.upper()] = parse_money(cell(grid, row_index, amount_col))
    return totals


def build_df_conversion(grid: list[list[str]] | None = None) -> pd.DataFrame:
    """Ambil Sales Funnel Conversion Rate dari sheet, apa adanya.

    Sumber menyimpan persentase per tahap funnel di:
        - baris 3            -> seluruh periode (scope = 'all')
        - baris penanda bulan -> bulan tersebut

    TIDAK ada perhitungan ulang di sini. Nilai yang tidak ada dibiarkan NaN.

    Returns:
        DataFrame kolom: scope, stage, stage_order, conversion_rate
    """
    if grid is None:
        grid = load_partner_grid()

    columns = resolve_partner_columns(grid)

    scopes: list[tuple[str, int]] = [("all", PARTNER_TOTAL_ROW - 1)]
    for row_index in range(len(grid)):
        marker_month = month_in_cell(cell(grid, row_index, columns["no"]))
        if marker_month is not None:
            scopes.append((marker_month, row_index))

    def to_rate(text: str) -> float:
        cleaned = clean_text(text).replace("%", "").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return float("nan")

    rows = []
    for scope, row_index in scopes:
        for order, stage in enumerate(FUNNEL_STAGES, start=1):
            raw = cell(grid, row_index, columns[f"stage::{stage}"])
            rows.append(
                {
                    "scope": scope,
                    "stage": stage,
                    "stage_order": order,
                    "conversion_rate": to_rate(raw),
                }
            )

    return pd.DataFrame(rows)



# ===========================================================================
# PENCOCOKAN NAMA PARTNER (dipakai lintas sheet)
# ===========================================================================
#
# Nama partner adalah SATU-SATUNYA kunci yang tersedia untuk menghubungkan
# National_1.1 (sumber tanggal akhir partnership), National_1.2 (Post-
# Partnership Report), dan PSC (Partnership Survey). Tidak ada ID partner.
#
# Karena itu pencocokan dibuat toleran terhadap perbedaan penulisan yang
# TERBUKTI ada di sumber, tanpa menjadi fuzzy matching agresif:
#
#   National_1.1            National_1.2 / PSC
#   ----------------------  --------------------------------
#   "Gen Z Outfit"          "GenZ Outfit Official"
#   "Hangry"                "Hangry Indonesia"
#   "Mandaya Hospital"      "Mandaya Royal Hospital Puri"
#   "PT Paragon Corp"       "Paragon corp"
#   "Jiwater"               "Ji Water (PT.Panca Tirta Prigen)"
#   "BumiBaik"              "Bumibaik"
#
# ATURAN PENCOCOKAN - berurutan dari bukti terkuat ke terlemah:
#   1. Nama ternormalisasi identik.
#   2. Nama tanpa spasi/tanda baca identik  -> menangani "Gen Z" vs "GenZ".
#   3. Nama tanpa spasi salah satu memuat yang lain, minimal
#      PARTNER_MATCH_MIN_SQUASH karakter -> menangani "Jiwater" di dalam
#      "Ji Water (PT Panca Tirta Prigen)".
#   4. Kumpulan kata penting salah satu adalah himpunan bagian dari yang
#      lain -> menangani "Mandaya Hospital" di "Mandaya Royal Hospital Puri".
#
# YANG SENGAJA TIDAK DIPAKAI: jarak Levenshtein / rasio kemiripan. Itu bisa
# menyatukan dua partner berbeda (mis. "DMAC Chicken Gunung Sahari" dengan
# "DMAC Chicken Crunch") dan kesalahan seperti itu sulit terlihat di
# dashboard. Lebih baik satu partner tidak ketemu daripada dua partner
# tertukar.
# ---------------------------------------------------------------------------

# Kata yang tidak membedakan identitas perusahaan. Dibuang sebelum
# membandingkan kumpulan kata, supaya "PT Paragon Corp" dan "Paragon corp"
# sama-sama tinggal {"paragon"}.
PARTNER_NAME_GENERIC_TOKENS = frozenset(
    {
        "pt", "cv", "tbk", "persero", "pte", "plc",
        "inc", "ltd", "llc", "co", "corp", "corporation", "company",
        "group", "holding", "holdings", "grup",
        "indonesia", "official", "the", "and", "dan",
    }
)

# Panjang minimal untuk aturan 3. Di bawah ini, potongan nama terlalu pendek
# untuk dipakai sebagai bukti (mis. "cove", "beex", "bca").
PARTNER_MATCH_MIN_SQUASH = 5

# Panjang minimal gabungan kata penting untuk aturan 4, supaya kata pendek
# seperti "se" atau "pik" tidak pernah dipakai sebagai satu-satunya bukti.
PARTNER_MATCH_MIN_TOKEN_TEXT = 4

# Semua yang bukan huruf/angka dianggap pemisah kata: titik, koma, apostrof,
# tanda kurung, dan garis miring semuanya muncul di nama partner di sumber.
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_partner_name(value) -> str:
    """Bentuk baku nama partner: huruf kecil, tanpa tanda baca, spasi rapat.

    "  PT. Paragon  Corp " -> "pt paragon corp"
    """
    text = clean_text(value).casefold()
    if not text:
        return ""
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def squash_partner_name(value) -> str:
    """Nama partner tanpa spasi sama sekali: "Gen Z Outfit" -> "genzoutfit"."""
    return normalize_partner_name(value).replace(" ", "")


def partner_name_tokens(value) -> frozenset[str]:
    """Kumpulan kata PENTING pada nama partner.

    Kata generik (PT, Corp, Group, Indonesia, ...) dibuang. Kalau setelah
    dibuang tidak ada kata yang tersisa - misalnya nama partner hanya
    "PT Indonesia" - seluruh kata dikembalikan apa adanya, supaya nama itu
    tidak berubah menjadi kunci kosong yang cocok dengan segalanya.
    """
    tokens = normalize_partner_name(value).split()
    if not tokens:
        return frozenset()
    significant = [token for token in tokens if token not in PARTNER_NAME_GENERIC_TOKENS]
    return frozenset(significant or tokens)


def partner_names_match(left, right) -> bool:
    """True kalau kedua nama menunjuk partner yang sama.

    Lihat ATURAN PENCOCOKAN di komentar section ini. Fungsi ini simetris:
    partner_names_match(a, b) selalu sama dengan partner_names_match(b, a).
    """
    left_norm = normalize_partner_name(left)
    right_norm = normalize_partner_name(right)
    if not left_norm or not right_norm:
        return False

    # 1. identik setelah normalisasi
    if left_norm == right_norm:
        return True

    left_squash = left_norm.replace(" ", "")
    right_squash = right_norm.replace(" ", "")

    # 2. identik setelah spasi dibuang
    if left_squash == right_squash:
        return True

    # 3. salah satu memuat yang lain, dengan panjang minimal
    shorter, longer = sorted((left_squash, right_squash), key=len)
    if len(shorter) >= PARTNER_MATCH_MIN_SQUASH and shorter in longer:
        return True

    # 4. kumpulan kata penting: himpunan bagian
    left_tokens = partner_name_tokens(left)
    right_tokens = partner_name_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    smaller = left_tokens if len(left_tokens) <= len(right_tokens) else right_tokens
    bigger = right_tokens if smaller is left_tokens else left_tokens
    if not smaller <= bigger:
        return False
    return len("".join(sorted(smaller))) >= PARTNER_MATCH_MIN_TOKEN_TEXT


def match_partner_name(name, candidates) -> str | None:
    """Cari satu nama pada daftar kandidat. Kembalikan nama kandidat aslinya.

    Kandidat diurutkan lebih dulu: yang paling mirip panjangnya dengan nama
    yang dicari diperiksa lebih awal, jadi kalau ada dua kandidat yang
    sama-sama lolos aturan, yang dipilih adalah yang paling dekat - bukan
    yang kebetulan berada di baris paling atas.

    Returns:
        Nama kandidat yang cocok, atau None kalau tidak ada.
    """
    target = normalize_partner_name(name)
    if not target:
        return None

    ordered = sorted(
        (str(candidate) for candidate in candidates if clean_text(candidate)),
        key=lambda candidate: (
            abs(len(normalize_partner_name(candidate)) - len(target)),
            normalize_partner_name(candidate),
        ),
    )
    for candidate in ordered:
        if partner_names_match(target, candidate):
            return candidate
    return None


# ===========================================================================
# NATIONAL 1.2 - POST-PARTNERSHIP REPORT
# ===========================================================================
#
# ATURAN BISNIS - hasil inspeksi struktur sheet, bukan asumsi
# ---------------------------------------------------------------------------
# 1. Header BERLAPIS EMPAT:
#       baris 1 : NO, PARTNER'S NAME, STAKEHODLER GROUPING, PIC FROM AIESEC,
#                 STATUS (for Continued Partner), Partnership Period,
#                 During-Experience Partnership Flow, Post-Experience
#       baris 2 : A. Deals Implementation, B. Partner Engagement,
#                 A. Partnership Survey
#       baris 3 : Active/Break/Finish, Start/End, 1. SnD Implementation,
#                 2. Event/Program Finished, Post-Partnership Survey, ...
#       baris 4 : Name/Email/Phone dan label "Link"
#
# 2. Kolom DOKUMEN laporan berlabel "Link" dan tidak punya nama sendiri di
#    baris 1-3. Dikenali lewat posisi: kolom "Link" PERTAMA setelah tahap
#    "2. Event/Program Finished". Isinya nama/berkas laporan, contoh:
#       "Hangry - Post-Partnership Report.pdf"
#       "Kawan Lama Group - Booklet Partnership Report 2627 (1).pdf"
#
# 3. Baris dianggap RECORD kalau kolom NO berisi angka murni DAN nama
#    partner terisi. Ini membuang baris penanda bulan ("(emoji)february"),
#    baris "Summary Semester #1"/"Summary Quarter #1", baris rekap
#    stakeholder, dan baris nomor kosong yang memang banyak di sheet ini.
#
# 4. KEHADIRAN NAMA PARTNER DI SHEET INI BUKAN BUKTI LAPORAN SUDAH ADA.
#    Terbukti di data: "Kinsuke Ramen" tercatat sebagai record lengkap
#    tetapi kolom Link-nya kosong, sedangkan lima partner lain punya berkas
#    laporan. Jadi indikator yang dipakai adalah ISI KOLOM LINK
#    (has_report), bukan sekadar "nama ditemukan" - keduanya tetap dibawa
#    sebagai kolom terpisah supaya bisa diaudit.
#
# 5. Kolom "Post-Partnership Survey" di sheet ini ikut dibawa sebagai
#    catatan, tetapi status survey di dashboard DIAMBIL DARI PSC, karena
#    PSC adalah sumber jawaban surveinya.
# ---------------------------------------------------------------------------

# Baris yang memuat nama kolom di National_1.2 (1-based).
REPORT_HEADER_ROWS = (1, 2, 3, 4)

# Label yang dicari, sudah dinormalisasi (huruf kecil, spasi rapat).
REPORT_LABELS: dict[str, str] = {
    "no": "no",
    "partner_name": "partner's name",
    "stakeholder": "stakehodler grouping",  # typo memang ada di sumber
    "pic_aiesec": "pic from aiesec",
    "status_continued": "status (for continued partner)",
    "period_start": "start",
    "period_end": "end",
    "snd_implementation": "1. snd implementation",
    "event_finished": "2. event/program finished",
    "survey_flag": "post-partnership survey",
}

# Tahap acuan untuk menemukan kolom "Link" berisi laporan (lihat aturan 2).
REPORT_LINK_AFTER_LABEL = "2. event/program finished"
REPORT_LINK_LABEL = "link"


def load_report_grid() -> list[list[str]] | None:
    """Baca National_1.2 dari mirror. None kalau tabnya tidak ada."""
    return sheets.try_read_national_values(sheets.NATIONAL_REPORT_WORKSHEET)


def _report_label_positions(grid: list[list[str]]) -> dict[str, list[int]]:
    """Petakan label header National_1.2 (baris 1-4) ke daftar index kolomnya."""
    positions: dict[str, list[int]] = {}
    for row_number in REPORT_HEADER_ROWS:
        row_index = row_number - 1
        if row_index >= len(grid):
            continue
        for col_index, raw in enumerate(grid[row_index]):
            label = normalize_header(raw)
            if label:
                positions.setdefault(label, []).append(col_index)
    return positions


def resolve_report_columns(grid: list[list[str]]) -> dict[str, int]:
    """Cari index kolom National_1.2 dari header berlapis empat.

    Raises:
        KeyError kalau ada kolom wajib yang tidak ditemukan. Sengaja gagal
        keras: lebih baik section post-partnership menampilkan pesan error
        yang jelas daripada melaporkan "Missing" untuk semua partner karena
        membaca kolom yang salah.
    """
    positions = _report_label_positions(grid)
    resolved: dict[str, int] = {}
    missing: list[str] = []

    for field, label in REPORT_LABELS.items():
        found = positions.get(label)
        if not found:
            missing.append(f"{field} (label dicari: {label!r})")
            continue
        resolved[field] = found[0]

    anchor = resolved.get("event_finished")
    if anchor is not None:
        after = [col for col in sorted(positions.get(REPORT_LINK_LABEL, [])) if col > anchor]
        if after:
            resolved["report_link"] = after[0]
        else:
            missing.append(
                f"kolom dokumen laporan ({REPORT_LINK_LABEL!r} setelah "
                f"{REPORT_LINK_AFTER_LABEL!r})"
            )

    if missing:
        raise KeyError("Kolom National_1.2 tidak ditemukan: " + "; ".join(missing))
    return resolved


def extract_report_records(grid: list[list[str]]) -> list[dict]:
    """Ambil baris record Post-Partnership Report dari National_1.2."""
    columns = resolve_report_columns(grid)

    records: list[dict] = []
    current_month: str | None = None

    for row_index in range(len(grid)):
        no_value = cell(grid, row_index, columns["no"])

        marker_month = month_in_cell(no_value)
        if marker_month is not None:
            current_month = marker_month
            continue

        partner_value = cell(grid, row_index, columns["partner_name"])
        if not (is_record_number(no_value) and has_value(partner_value)):
            continue

        report_raw = clean_text(cell(grid, row_index, columns["report_link"]))
        survey_flag_raw = clean_text(cell(grid, row_index, columns["survey_flag"]))

        records.append(
            {
                "partner_name": clean_text(partner_value),
                "stakeholder": clean_text(cell(grid, row_index, columns["stakeholder"])),
                "pic_aiesec": clean_text(cell(grid, row_index, columns["pic_aiesec"]))
                or None,
                "status_continued": clean_text(
                    cell(grid, row_index, columns["status_continued"])
                )
                or None,
                "period_start_raw": clean_text(
                    cell(grid, row_index, columns["period_start"])
                )
                or None,
                "period_end_raw": clean_text(cell(grid, row_index, columns["period_end"]))
                or None,
                "snd_implementation": clean_text(
                    cell(grid, row_index, columns["snd_implementation"])
                ).upper()
                == "TRUE",
                "event_finished": clean_text(
                    cell(grid, row_index, columns["event_finished"])
                ).upper()
                == "TRUE",
                "report_link_raw": report_raw or None,
                # Indikator yang dipakai dashboard (lihat aturan 4).
                "has_report": has_document(report_raw),
                "survey_flag_raw": survey_flag_raw or None,
                "survey_flag": survey_flag_raw.upper() == "TRUE",
                "month": current_month,
                "source_row": row_index + 1,
            }
        )

    return records


def build_df_report(grid: list[list[str]] | None = None) -> pd.DataFrame:
    """Bangun df_report dari National_1.2 (Post-Partnership Report).

    Args:
        grid: raw values National_1.2. Kalau None, dibaca dari mirror.

    Returns:
        DataFrame kolom: partner_name, partner_key, stakeholder, pic_aiesec,
        status_continued, period_start_raw, period_end_raw,
        snd_implementation, event_finished, report_link_raw, has_report,
        survey_flag_raw, survey_flag, month, source_row.

        DataFrame KOSONG (tanpa kolom) kalau tab tidak ada atau tidak punya
        satu pun record - pemanggil membedakannya lewat df.empty.
    """
    if grid is None:
        grid = load_report_grid()
    if not grid:
        return pd.DataFrame()

    df = pd.DataFrame.from_records(extract_report_records(grid))
    if df.empty:
        return pd.DataFrame()

    df["partner_key"] = df["partner_name"].map(normalize_partner_name)
    df["month"] = pd.Categorical(
        df["month"], categories=list(TERM_MONTH_ORDER), ordered=True
    )
    return df.sort_values(["month", "source_row"]).reset_index(drop=True)


# ===========================================================================
# PSC - PARTNERSHIP SURVEY
# ===========================================================================
#
# ATURAN BISNIS - hasil inspeksi struktur sheet
# ---------------------------------------------------------------------------
# 1. PSC adalah hasil Google Form: satu baris = satu responden, bukan satu
#    baris = satu partner. Satu partner bisa mengisi lebih dari sekali.
#
# 2. Baris 1 hanya judul ("AIESEC in BINUS"). Header pertanyaan ada di baris
#    berikutnya dan dikenali dari sel "Timestamp", bukan dari nomor baris
#    tetap - kalau BD menambah baris judul, parser tidak ikut rusak.
#
# 3. Nama perusahaan ada di kolom pertanyaan "What is the institution/company
#    that you represent?". Kolom dicari lewat KATA KUNCI pada pertanyaannya,
#    karena teks pertanyaan Google Form panjang dan mudah diedit.
#
# 4. Baris rekap (persentase "Summary" di kolom kanan) tidak punya nama
#    perusahaan, jadi otomatis tersaring oleh aturan "nama perusahaan
#    harus terisi".
#
# 5. Nama perusahaan di sini ditulis versi responden, bukan versi ESSM
#    ("GenZ Outfit Official" vs "Gen Z Outfit"). Penghubungnya adalah
#    partner_names_match() di section sebelumnya.
# ---------------------------------------------------------------------------

# Baris maksimal yang diperiksa saat mencari baris header.
SURVEY_HEADER_SEARCH_ROWS = 6

SURVEY_TIMESTAMP_LABEL = "timestamp"

# Kata kunci pertanyaan. Dicari sebagai substring pada header ternormalisasi.
SURVEY_PARTNER_KEYWORDS: tuple[str, ...] = ("institution/company", "institution", "company")
SURVEY_RESPONDENT_KEYWORDS: tuple[str, ...] = ("what is your name", "your name")

# Format timestamp Google Form di sheet ini: "6/19/2026 14:40:20".
SURVEY_TIMESTAMP_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",
)


def load_survey_grid() -> list[list[str]] | None:
    """Baca PSC dari mirror. None kalau tabnya tidak ada."""
    return sheets.try_read_national_values(sheets.NATIONAL_SURVEY_WORKSHEET)


def _first_column_containing(
    header: list[str], keywords: tuple[str, ...]
) -> int | None:
    """Index kolom pertama yang header-nya memuat salah satu kata kunci."""
    normalized = [normalize_header(value) for value in header]
    for keyword in keywords:
        for col_index, label in enumerate(normalized):
            if keyword in label:
                return col_index
    return None


def resolve_survey_columns(grid: list[list[str]]) -> dict[str, int]:
    """Cari baris header dan index kolom PSC.

    Returns:
        dict dengan kunci: header_row (0-based), partner_name, dan - kalau
        ada - timestamp serta respondent.

    Raises:
        KeyError kalau baris header atau kolom nama perusahaan tidak ada.
    """
    header_row: int | None = None
    for row_index in range(min(SURVEY_HEADER_SEARCH_ROWS, len(grid))):
        labels = [normalize_header(value) for value in grid[row_index]]
        if SURVEY_TIMESTAMP_LABEL in labels:
            header_row = row_index
            break

    if header_row is None:
        raise KeyError(
            "Baris header PSC tidak ditemukan: tidak ada sel "
            f"{SURVEY_TIMESTAMP_LABEL!r} pada "
            f"{SURVEY_HEADER_SEARCH_ROWS} baris pertama."
        )

    header = grid[header_row]
    partner_column = _first_column_containing(header, SURVEY_PARTNER_KEYWORDS)
    if partner_column is None:
        raise KeyError(
            "Kolom nama perusahaan di PSC tidak ditemukan (kata kunci dicari: "
            f"{', '.join(SURVEY_PARTNER_KEYWORDS)})."
        )

    resolved = {"header_row": header_row, "partner_name": partner_column}

    timestamp_column = _first_column_containing(header, (SURVEY_TIMESTAMP_LABEL,))
    if timestamp_column is not None:
        resolved["timestamp"] = timestamp_column

    respondent_column = _first_column_containing(header, SURVEY_RESPONDENT_KEYWORDS)
    if respondent_column is not None:
        resolved["respondent"] = respondent_column

    return resolved


def parse_survey_timestamp(value: str | None):
    """Urai timestamp Google Form. NaT kalau tidak cocok format apa pun."""
    text = clean_text(value or "")
    if not text:
        return pd.NaT
    for timestamp_format in SURVEY_TIMESTAMP_FORMATS:
        try:
            parsed = pd.to_datetime(text, format=timestamp_format)
        except (ValueError, TypeError):
            continue
        if parsed is not pd.NaT and not pd.isna(parsed):
            return parsed
    return pd.NaT


def extract_survey_records(grid: list[list[str]]) -> list[dict]:
    """Ambil respons survey dari PSC. Satu dict = satu responden."""
    columns = resolve_survey_columns(grid)
    header_row = columns["header_row"]

    records: list[dict] = []
    for row_index in range(header_row + 1, len(grid)):
        partner_value = cell(grid, row_index, columns["partner_name"])
        if not has_value(partner_value):
            continue

        record = {
            "partner_name": clean_text(partner_value),
            "respondent": None,
            "submitted_at_raw": None,
            "source_row": row_index + 1,
        }
        if "respondent" in columns:
            record["respondent"] = (
                clean_text(cell(grid, row_index, columns["respondent"])) or None
            )
        if "timestamp" in columns:
            record["submitted_at_raw"] = (
                clean_text(cell(grid, row_index, columns["timestamp"])) or None
            )
        records.append(record)

    return records


def build_df_survey(grid: list[list[str]] | None = None) -> pd.DataFrame:
    """Bangun df_survey dari PSC (Partnership Survey).

    Args:
        grid: raw values PSC. Kalau None, dibaca dari mirror.

    Returns:
        DataFrame kolom: partner_name, partner_key, respondent,
        submitted_at, submitted_at_raw, source_row.

        DataFrame KOSONG kalau tab tidak ada atau belum ada satu pun respons.
    """
    if grid is None:
        grid = load_survey_grid()
    if not grid:
        return pd.DataFrame()

    df = pd.DataFrame.from_records(extract_survey_records(grid))
    if df.empty:
        return pd.DataFrame()

    df["partner_key"] = df["partner_name"].map(normalize_partner_name)
    df["submitted_at"] = df["submitted_at_raw"].map(parse_survey_timestamp)
    return df.sort_values("source_row").reset_index(drop=True)
