"""
periods.py — PERIOD FILTER LAYER (Phase 9)

Menerjemahkan pilihan periode dari dashboard menjadi dataframe yang sudah
tersaring, lalu menyerahkannya ke metrics.py. Tidak ada perhitungan KPI di
sini dan tidak ada akses Google Sheets.

--------------------------------------------------------------------------
ATURAN FILTER PERIODE
--------------------------------------------------------------------------
1. Satuan terkecil adalah BULAN. Sumber (baris penanda bulan, kolom MONTHS,
   "Month End" berformat "SEP 26") tidak pernah memberi level harian, jadi
   filter harian akan menyiratkan ketelitian yang tidak dimiliki data.

2. Urutan bulan mengikuti masa jabatan AIESEC: February -> ... -> January.
   Januari milik tahun berikutnya (term 2026: FEB 26 ... JAN 27).

3. METRIK ALIRAN disaring dengan MEMBUANG BARIS di luar periode:
       Total MR, Financial Revenue, In-Kind Value, partner baru.
   Metrik ini additif: jumlah tiap bulan = total seluruh periode.

4. METRIK POSISI tidak disaring per baris, melainkan dihitung ulang
   memakai TANGGAL ACUAN akhir periode:
       Active Partners, Document Tracker, Expiry Tracker.
   Alasan: "aktif" itu keadaan pada satu titik waktu, bukan kejadian yang
   bisa dijumlahkan. Kalau df_partner ikut disaring per bulan, KPI-nya
   berubah arti menjadi "partner yang DIDAFTARKAN bulan itu dan aktif" —
   angka yang tidak pernah diminta tim BD.

5. Tanggal acuan = akhir bulan terakhir yang dipilih, tapi TIDAK PERNAH
   melewati hari ini. Jadi memilih seluruh periode tetap menghasilkan
   "aktif per hari ini" (16 partner, sesuai validasi Phase 5), bukan
   proyeksi akhir Januari.

6. Conversion rate tetap DIAMBIL dari sheet:
       seluruh periode      -> scope 'all'
       satu bulan           -> scope bulan itu
       gabungan beberapa bulan (bukan seluruh periode) -> TIDAK ADA
   Untuk kasus terakhir nilainya NaN, karena sheet tidak menyediakan
   angka gabungan dan aturan BD melarang menghitungnya ulang.

7. February & March tidak ada di df_mr (dibuang di Phase 3 karena
   hasil copy-paste antar tab). Memilih kedua bulan itu tetap sah untuk
   partner dan revenue; Total MR-nya 0, bukan error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import preparation
from .metrics import SCOPE_ALL
from .preparation import TERM_MONTH_ORDER

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

# Semua bulan masa jabatan, berurutan.
TERM_MONTHS: tuple[str, ...] = tuple(TERM_MONTH_ORDER)

# Bulan pertama masa jabatan. Dipakai menentukan tahun tiap bulan.
TERM_FIRST_MONTH = TERM_MONTHS[0]

# Kuartal versi National ESSM, dikonfirmasi dari kolom `quarter` di
# National_1.3/1.4: tiga bulan berurutan mulai dari awal masa jabatan.
QUARTER_MONTHS: dict[str, tuple[str, ...]] = {
    f"QUARTER #{index + 1}": TERM_MONTHS[index * 3: index * 3 + 3]
    for index in range(4)
}

MONTH_TO_QUARTER: dict[str, str] = {
    month: quarter
    for quarter, months in QUARTER_MONTHS.items()
    for month in months
}

# Nilai yang berarti "jangan disaring".
SELECT_ALL = SCOPE_ALL

LABEL_ALL = "Seluruh periode"


# ---------------------------------------------------------------------------
# Normalisasi pilihan bulan
# ---------------------------------------------------------------------------


def normalize_months(selection: str | tuple | list | None = None) -> tuple[str, ...]:
    """Ubah pilihan periode apa pun menjadi tuple bulan berurutan.

    Menerima None, 'all', satu nama bulan, nama kuartal ('QUARTER #2'),
    atau kumpulan nama bulan. Nama bulan tidak peka huruf besar/kecil.

    Raises:
        ValueError: kalau ada nama bulan/kuartal yang tidak dikenal.
            Lebih baik gagal keras daripada diam-diam menyaring nol baris.
    """
    if selection is None:
        return TERM_MONTHS

    if isinstance(selection, str):
        selection = [selection]

    requested: list[str] = []
    for item in selection:
        text = str(item).strip()
        if not text:
            continue
        if text.casefold() == SELECT_ALL:
            return TERM_MONTHS
        upper = text.upper()
        if upper in QUARTER_MONTHS:
            requested.extend(QUARTER_MONTHS[upper])
            continue
        lower = text.casefold()
        lower = preparation.MONTH_ALIASES.get(lower, lower)
        if lower not in TERM_MONTHS:
            raise ValueError(
                f"Bulan/kuartal tidak dikenal: {item!r}. "
                f"Pilihan bulan: {', '.join(TERM_MONTHS)}. "
                f"Pilihan kuartal: {', '.join(QUARTER_MONTHS)}."
            )
        requested.append(lower)

    if not requested:
        return TERM_MONTHS

    # Urutkan menurut masa jabatan dan buang pengulangan.
    unique = set(requested)
    return tuple(month for month in TERM_MONTHS if month in unique)


def months_for_quarter(quarter: str) -> tuple[str, ...]:
    """Bulan-bulan anggota satu kuartal, mis. 'QUARTER #2' -> may/june/july."""
    key = str(quarter).strip().upper()
    if key not in QUARTER_MONTHS:
        raise ValueError(
            f"Kuartal tidak dikenal: {quarter!r}. Pilihan: {', '.join(QUARTER_MONTHS)}."
        )
    return QUARTER_MONTHS[key]


def is_full_term(months: tuple[str, ...]) -> bool:
    """True kalau pilihan mencakup seluruh masa jabatan."""
    return set(months) == set(TERM_MONTHS)


def period_label(selection: str | tuple | list | None = None) -> str:
    """Label periode yang enak dibaca manusia, untuk judul kartu/grafik."""
    months = normalize_months(selection)
    if is_full_term(months):
        return LABEL_ALL
    for quarter, quarter_months in QUARTER_MONTHS.items():
        if set(months) == set(quarter_months):
            return f"{quarter} ({months[0].title()}-{months[-1].title()})"
    if len(months) == 1:
        return months[0].title()
    # Rentang berurutan ditulis sebagai rentang; kalau bolong, disebut satu per satu.
    indexes = [TERM_MONTHS.index(month) for month in months]
    if indexes == list(range(indexes[0], indexes[-1] + 1)):
        return f"{months[0].title()}-{months[-1].title()}"
    return ", ".join(month.title() for month in months)


# ---------------------------------------------------------------------------
# Bulan -> Period (dengan tahun masa jabatan)
# ---------------------------------------------------------------------------


def term_start_year(today: pd.Timestamp | None = None) -> int:
    """Tahun dimulainya masa jabatan yang sedang berjalan.

    Masa jabatan mulai February. Jadi kalau hari ini Januari, masa jabatan
    yang berjalan dimulai tahun lalu.
    """
    today = pd.Timestamp.today().normalize() if today is None else pd.Timestamp(today)
    first_month_number = TERM_MONTHS.index(TERM_FIRST_MONTH) + 2  # february = 2
    return today.year if today.month >= first_month_number else today.year - 1


def month_to_period(month: str, today: pd.Timestamp | None = None) -> pd.Period:
    """Ubah nama bulan masa jabatan menjadi Period bulanan bertahun.

    January adalah bulan terakhir masa jabatan, jadi tahunnya + 1.
    """
    months = normalize_months(month)
    if len(months) != 1:
        raise ValueError(f"Butuh satu nama bulan, dapat: {month!r}")
    name = months[0]

    year = term_start_year(today)
    position = TERM_MONTHS.index(name)
    calendar_month = 2 + position  # february = 2, ..., december = 12
    if calendar_month > 12:
        calendar_month -= 12
        year += 1
    return pd.Period(year=year, month=calendar_month, freq="M")


def period_reference_date(
    selection: str | tuple | list | None = None,
    today: pd.Timestamp | None = None,
) -> pd.Timestamp:
    """Tanggal acuan untuk metrik POSISI: akhir bulan terakhir yang dipilih.

    Dibatasi tidak melewati hari ini (lihat aturan 5), supaya KPI "aktif"
    tidak berubah menjadi proyeksi masa depan.
    """
    today = pd.Timestamp.today().normalize() if today is None else pd.Timestamp(today).normalize()
    months = normalize_months(selection)
    last_period = month_to_period(months[-1], today=today)
    candidate = preparation.period_to_month_end(last_period)
    return min(candidate, today)


# ---------------------------------------------------------------------------
# Penyaringan dataframe
# ---------------------------------------------------------------------------


def available_months(
    df: pd.DataFrame, column: str = "month", value_column: str | None = None
) -> tuple[str, ...]:
    """Bulan yang benar-benar punya baris data, berurutan masa jabatan.

    Dipakai untuk mengisi pilihan filter: bulan yang kosong tidak perlu
    ditawarkan sebagai pilihan.

    Args:
        value_column: kalau diisi, sebuah bulan hanya dianggap punya data
            bila kolom ini terisi. Perlu untuk MR: ada baris di bawah
            penanda bulan yang kolom PIC-nya kosong, dan baris seperti itu
            tidak menambah Total MR sama sekali.
    """
    if df is None or df.empty or column not in df.columns:
        return ()
    source = df
    if value_column is not None:
        if value_column not in df.columns:
            raise KeyError(f"Dataframe tidak punya kolom {value_column!r}.")
        source = df[df[value_column].notna()]
    present = {str(value) for value in source[column].dropna().unique()}
    return tuple(month for month in TERM_MONTHS if month in present)


def filter_by_months(
    df: pd.DataFrame,
    selection: str | tuple | list | None = None,
    column: str = "month",
) -> pd.DataFrame:
    """Saring dataframe METRIK ALIRAN menurut bulan.

    Kategori bulan dipertahankan apa adanya supaya grafik tetap
    menampilkan sumbu bulan yang lengkap dan berurutan.
    """
    if df is None or df.empty:
        return df
    if column not in df.columns:
        raise KeyError(f"Dataframe tidak punya kolom {column!r} untuk filter periode.")

    months = normalize_months(selection)
    if is_full_term(months):
        return df.copy()

    mask = df[column].astype("object").isin(months)
    return df[mask].copy()


def resolve_conversion_scope(
    selection: str | tuple | list | None = None,
) -> str | None:
    """Scope df_conversion yang sah untuk pilihan periode ini.

    Returns:
        'all' untuk seluruh periode, nama bulan untuk pilihan satu bulan,
        atau None kalau sheet tidak menyediakan angkanya (lihat aturan 6).
    """
    months = normalize_months(selection)
    if is_full_term(months):
        return SCOPE_ALL
    if len(months) == 1:
        return months[0]
    return None


# ---------------------------------------------------------------------------
# Bundel hasil filter
# ---------------------------------------------------------------------------


@dataclass
class PeriodFrames:
    """Kumpulan dataframe yang sudah disesuaikan dengan satu pilihan periode.

    Attributes:
        months: bulan yang tercakup, berurutan.
        label: label periode untuk ditampilkan.
        reference_date: tanggal acuan metrik posisi.
        conversion_scope: scope df_conversion, None kalau tidak tersedia.
        df_mr, df_financial, df_inkind: sudah disaring per baris (aliran).
        df_partner: TIDAK disaring; is_active dihitung ulang per reference_date.
        df_partner_new: partner yang tercatat pada periode ini (aliran).
        df_conversion: diteruskan apa adanya; penyaringan lewat scope.
    """

    months: tuple[str, ...]
    label: str
    reference_date: pd.Timestamp
    conversion_scope: str | None
    df_mr: pd.DataFrame
    df_partner: pd.DataFrame
    df_partner_new: pd.DataFrame
    df_conversion: pd.DataFrame
    df_financial: pd.DataFrame
    df_inkind: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def apply_period(
    selection: str | tuple | list | None = None,
    *,
    df_mr: pd.DataFrame | None = None,
    df_partner: pd.DataFrame | None = None,
    df_conversion: pd.DataFrame | None = None,
    df_financial: pd.DataFrame | None = None,
    df_inkind: pd.DataFrame | None = None,
    today: pd.Timestamp | None = None,
) -> PeriodFrames:
    """Terapkan satu pilihan periode ke seluruh dataframe sekaligus.

    Ini satu-satunya tempat pilihan periode diterjemahkan, supaya semua
    KPI di dashboard pasti memakai periode yang sama.
    """
    months = normalize_months(selection)
    reference_date = period_reference_date(months, today=today)
    scope = resolve_conversion_scope(months)

    empty = pd.DataFrame()
    notes: list[str] = []

    filtered_mr = filter_by_months(df_mr if df_mr is not None else empty, months)
    filtered_financial = filter_by_months(
        df_financial if df_financial is not None else empty, months
    )
    filtered_inkind = filter_by_months(
        df_inkind if df_inkind is not None else empty, months
    )

    partner = df_partner if df_partner is not None else empty
    partner_as_of = (
        preparation.apply_reference_date(partner, reference_date)
        if not partner.empty
        else partner
    )
    partner_new = filter_by_months(partner_as_of, months)

    if scope is None:
        notes.append(
            "Conversion rate tidak tersedia untuk gabungan beberapa bulan: "
            "sheet hanya menyediakan angka per bulan dan seluruh periode, "
            "dan aturan BD melarang menghitungnya ulang."
        )
    excluded_in_selection = [
        month for month in months if month in preparation.EXCLUDED_MONTHS
    ]
    if excluded_in_selection:
        notes.append(
            f"Bulan {', '.join(excluded_in_selection)} tidak ada di data MR "
            "(dibuang di Phase 3), jadi Total MR periode ini tidak "
            "menghitung bulan tersebut."
        )

    return PeriodFrames(
        months=months,
        label=period_label(months),
        reference_date=reference_date,
        conversion_scope=scope,
        df_mr=filtered_mr,
        df_partner=partner_as_of,
        df_partner_new=partner_new,
        df_conversion=df_conversion if df_conversion is not None else empty,
        df_financial=filtered_financial,
        df_inkind=filtered_inkind,
        notes=notes,
    )
