"""
metrics.py — ANALYTICS / KPI LAYER (Phase 8)

Semua perhitungan KPI hidup di sini, bukan di app.py, supaya bisa
diuji tanpa menjalankan Streamlit.

Aturan layer ini:
  - TIDAK pernah memanggil Google Sheets API. Dataframe diterima sebagai
    argumen, jadi fungsi-fungsi di sini bisa diuji tanpa koneksi.
  - TIDAK pernah membersihkan atau menambal data. Itu tugas preparation.py.
  - TIDAK pernah membuat figure atau widget. Itu tugas charts.py / app.py.
  - Nilai yang tidak ada dikembalikan sebagai NaN / pd.NA, tidak pernah
    diganti 0 supaya "belum ada data" tidak tersamar jadi "nilainya nol".

--------------------------------------------------------------------------
ATURAN BISNIS KPI - lanjutan dari Phase 3-7
--------------------------------------------------------------------------
1. Total MR = jumlah kemunculan nama PIC di df_mr (definisi tim BD),
   bukan jumlah baris dan bukan jumlah partner unik.

2. Active Partner = kolom is_active di df_partner (ada dokumen LoA DAN
   Month End >= bulan berjalan). Kolom "Status (For new sales)" milik
   sheet tidak dipakai.

3. Conversion rate DIAMBIL dari df_conversion (nilai yang sudah dihitung
   sheet), tahap '5. contract signed'. Tidak pernah dihitung ulang.

4. Financial Revenue dan In-Kind Value TIDAK BOLEH dijumlahkan menjadi
   satu "Total Revenue". Keduanya selalu dilaporkan sebagai dua angka.

5. Document Tracker hanya untuk partner AKTIF. Dokumen partner yang
   kontraknya sudah berakhir tidak perlu ditagih lagi.

6. Expiry Tracker berbasis BULAN, karena sumber (Month End) hanya
   memberi level bulan.

7. POST-PARTNERSHIP (Partnership Survey & Partnership Report) memakai
   pembagian berbasis BULAN, bukan tanggal harian:

       BELUM WAJIB : end_month >  bulan acuan
       SUDAH WAJIB : end_month <= bulan acuan

   Alasan memakai bulan: sumber hanya memberi "Month End" setingkat bulan
   ("SEP 26"), dan tanggal hariannya adalah asumsi akhir bulan yang dibuat
   dashboard - bukan fakta dari sheet. Memakai perbandingan harian membuat
   partner yang berakhir BULAN INI tampil "Not Required Yet" sampai hari
   terakhir bulan itu, padahal laporannya sudah disiapkan. Keputusan tim
   BD: begitu bulan berakhir tiba, Partnership Report & Survey sudah
   ditagih.

   Partner yang bulan berakhirnya belum tiba berstatus "Not Required Yet"
   dan tidak pernah masuk penyebut persentase. Partner tanpa Month End
   tidak bisa dinilai sama sekali, jadi berstatus "No End Date" dan juga
   tidak ikut dihitung.

   Catatan: status AKTIF (KPI Active Partners) tetap memakai aturan lama
   end_month >= bulan acuan, jadi partner di bulan terakhirnya memang
   muncul sekaligus sebagai partner aktif DAN sebagai laporan yang sudah
   ditagih. Itu memang keadaan yang sebenarnya terjadi.

8. Satu partner dihitung SEKALI. df_partner bisa memuat nama yang sama
   lebih dari satu baris (mis. re-raise), jadi baris didedupe memakai nama
   ternormalisasi sebelum metrik post-partnership dihitung.
"""

from __future__ import annotations

import pandas as pd

from .preparation import (
    CONVERSION_STAGE,
    DOCUMENT_FIELDS,
    FUNNEL_STAGES,
    TERM_MONTH_ORDER,
    match_partner_name,
    normalize_partner_name,
)

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

# Scope df_conversion yang berarti "seluruh periode" (baris 3 di sheet).
SCOPE_ALL = "all"

# Batas kategori "segera berakhir", dalam bulan.
EXPIRY_SOON_MONTHS = 3

# Label kategori expiry. Urutan = urutan tampil, dari paling mendesak.
EXPIRY_EXPIRED = "expired"
EXPIRY_THIS_MONTH = "berakhir bulan ini"
EXPIRY_SOON = f"1-{EXPIRY_SOON_MONTHS} bulan lagi"
EXPIRY_LATER = f"lebih dari {EXPIRY_SOON_MONTHS} bulan"
EXPIRY_UNKNOWN = "tanpa data"

EXPIRY_CATEGORIES: tuple[str, ...] = (
    EXPIRY_EXPIRED,
    EXPIRY_THIS_MONTH,
    EXPIRY_SOON,
    EXPIRY_LATER,
    EXPIRY_UNKNOWN,
)

# Nama yang dipakai kalau kolom stakeholder kosong di sumber. Lebih jujur
# daripada label kosong yang tidak terbaca di grafik.
STAKEHOLDER_UNKNOWN = "(tidak diisi)"


# ---------------------------------------------------------------------------
# Konstanta post-partnership
# ---------------------------------------------------------------------------

# Status dokumen post-partnership. Sengaja bahasa Inggris supaya sama dengan
# label yang dipakai tim BD di sheet dan di UI.
POST_STATUS_COMPLETED = "Completed"
POST_STATUS_MISSING = "Missing"
POST_STATUS_NOT_REQUIRED = "Not Required Yet"
POST_STATUS_NO_END_DATE = "No End Date"
# Sumbernya tidak bisa dibaca. BEDA dari "Missing": kalau sheet-nya gagal
# dibaca, kita tidak tahu dokumennya ada atau tidak - melaporkan "Missing"
# akan membuat tim menagih partner yang sebenarnya sudah mengumpulkan.
POST_STATUS_UNAVAILABLE = "Data Unavailable"

POST_STATUSES: tuple[str, ...] = (
    POST_STATUS_COMPLETED,
    POST_STATUS_MISSING,
    POST_STATUS_NOT_REQUIRED,
    POST_STATUS_NO_END_DATE,
    POST_STATUS_UNAVAILABLE,
)

# Kunci sumber yang bisa dilaporkan tidak tersedia oleh pemanggil.
POST_SOURCE_SURVEY = "survey"
POST_SOURCE_REPORT = "report"

# Dua kewajiban post-partnership yang dilacak.
# (nama kolom status, label tampilan, kunci sumber)
POST_REQUIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("survey_status", "Partnership Survey", POST_SOURCE_SURVEY),
    ("report_status", "Partnership Report", POST_SOURCE_REPORT),
)

# Ambang urgensi kartu "segera berakhir", dalam HARI. Dipakai untuk memilih
# penekanan visual, bukan untuk menyaring baris.
EXPIRY_CRITICAL_DAYS = 7
EXPIRY_WARNING_DAYS = 30

URGENCY_CRITICAL = "critical"   # 0-7 hari
URGENCY_WARNING = "warning"     # 8-30 hari
URGENCY_NORMAL = "normal"       # lebih dari 30 hari


# ---------------------------------------------------------------------------
# Util internal
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    """Pastikan dataframe punya kolom yang dibutuhkan.

    Gagal cepat dengan pesan jelas lebih baik daripada KeyError mentah
    atau, lebih buruk, KPI yang diam-diam salah.
    """
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(f"{name} tidak punya kolom: {', '.join(missing)}")


def _empty(df: pd.DataFrame | None) -> bool:
    return df is None or df.empty


def _month_categorical(values: pd.Series) -> pd.Categorical:
    """Jadikan bulan kategori berurutan masa jabatan (February -> January)."""
    return pd.Categorical(values, categories=list(TERM_MONTH_ORDER), ordered=True)


# ---------------------------------------------------------------------------
# KPI 1 & 2 — Market Research
# ---------------------------------------------------------------------------


def get_total_mr(df_mr: pd.DataFrame) -> int:
    """Total Market Research = jumlah kemunculan nama PIC.

    Baris tanpa PIC tidak dihitung karena tidak ada nama yang muncul.
    Angka acuan hasil validasi BD di Phase 3: 1694.
    """
    if _empty(df_mr):
        return 0
    _require_columns(df_mr, ("pic_aiesec",), "df_mr")
    return int(df_mr["pic_aiesec"].notna().sum())


def get_mr_by_pic(df_mr: pd.DataFrame) -> pd.DataFrame:
    """MR per PIC, urut dari terbanyak.

    Returns:
        DataFrame kolom: pic_aiesec, total_mr, share_percent
    """
    columns = ["pic_aiesec", "total_mr", "share_percent"]
    if _empty(df_mr):
        return pd.DataFrame(columns=columns)

    _require_columns(df_mr, ("pic_aiesec",), "df_mr")
    counts = df_mr["pic_aiesec"].value_counts(dropna=True)
    total = int(counts.sum())

    result = counts.rename_axis("pic_aiesec").reset_index(name="total_mr")
    result["share_percent"] = (
        (result["total_mr"] / total * 100).round(2) if total else float("nan")
    )
    # Urutan: MR terbanyak dulu; kalau seri, alfabetis supaya stabil.
    return result.sort_values(
        ["total_mr", "pic_aiesec"], ascending=[False, True]
    ).reset_index(drop=True)


def get_mr_by_month(df_mr: pd.DataFrame) -> pd.DataFrame:
    """MR per bulan, urut masa jabatan.

    Bulan tanpa MR tetap muncul dengan nilai 0 supaya garis tren tidak
    terlihat "melompati" bulan yang kosong.

    Returns:
        DataFrame kolom: month, total_mr
    """
    columns = ["month", "total_mr"]
    if _empty(df_mr):
        return pd.DataFrame(columns=columns)

    _require_columns(df_mr, ("pic_aiesec", "month"), "df_mr")
    counted = df_mr[df_mr["pic_aiesec"].notna()]
    series = counted.groupby("month", observed=False).size()
    result = series.rename_axis("month").reset_index(name="total_mr")
    result["total_mr"] = result["total_mr"].astype(int)
    return result


# ---------------------------------------------------------------------------
# KPI 3 — Partner aktif
# ---------------------------------------------------------------------------


def get_active_partners(df_partner: pd.DataFrame) -> pd.DataFrame:
    """Subset partner yang AKTIF menurut aturan BD (ada LoA + belum berakhir)."""
    if _empty(df_partner):
        return df_partner if df_partner is not None else pd.DataFrame()
    _require_columns(df_partner, ("is_active",), "df_partner")
    return df_partner[df_partner["is_active"]].copy()


def get_active_partner_count(df_partner: pd.DataFrame) -> int:
    """Jumlah partner aktif. Angka acuan hasil validasi BD di Phase 5: 16."""
    if _empty(df_partner):
        return 0
    _require_columns(df_partner, ("is_active",), "df_partner")
    return int(df_partner["is_active"].sum())


def get_partner_count(df_partner: pd.DataFrame) -> int:
    """Jumlah seluruh partner di portofolio, aktif maupun tidak."""
    if _empty(df_partner):
        return 0
    return int(len(df_partner))


# ---------------------------------------------------------------------------
# KPI 4 — Sales funnel conversion rate
# ---------------------------------------------------------------------------


def get_conversion_rate(
    df_conversion: pd.DataFrame,
    scope: str = SCOPE_ALL,
    stage: str = CONVERSION_STAGE,
) -> float:
    """Conversion rate (persen) untuk satu scope dan satu tahap funnel.

    Nilainya DIAMBIL dari sheet, tidak dihitung ulang. Scope bisa 'all'
    (seluruh periode) atau nama bulan, mis. 'august'.

    Returns:
        float persen, atau NaN kalau sheet tidak punya angkanya.
    """
    if _empty(df_conversion):
        return float("nan")

    _require_columns(df_conversion, ("scope", "stage", "conversion_rate"), "df_conversion")
    matched = df_conversion[
        (df_conversion["scope"] == scope) & (df_conversion["stage"] == stage)
    ]
    if matched.empty:
        return float("nan")
    return float(matched["conversion_rate"].iloc[0])


def get_conversion_funnel(
    df_conversion: pd.DataFrame, scope: str = SCOPE_ALL
) -> pd.DataFrame:
    """Seluruh tahap funnel untuk satu scope, urut tahap.

    Returns:
        DataFrame kolom: stage, stage_order, conversion_rate
    """
    columns = ["stage", "stage_order", "conversion_rate"]
    if _empty(df_conversion):
        return pd.DataFrame(columns=columns)

    _require_columns(df_conversion, ("scope", "stage", "stage_order", "conversion_rate"),
                     "df_conversion")
    matched = df_conversion[df_conversion["scope"] == scope]
    if matched.empty:
        return pd.DataFrame(columns=columns)

    result = matched[columns].copy()
    # Reindex ke daftar tahap resmi supaya tahap yang hilang tetap terlihat.
    result["stage"] = pd.Categorical(
        result["stage"], categories=list(FUNNEL_STAGES), ordered=True
    )
    return result.sort_values("stage_order").reset_index(drop=True)


def get_conversion_by_month(df_conversion: pd.DataFrame) -> pd.DataFrame:
    """Conversion rate tahap KPI untuk setiap bulan, urut masa jabatan.

    Scope 'all' dikeluarkan karena itu ringkasan, bukan satu bulan.

    Returns:
        DataFrame kolom: month, conversion_rate
    """
    columns = ["month", "conversion_rate"]
    if _empty(df_conversion):
        return pd.DataFrame(columns=columns)

    _require_columns(df_conversion, ("scope", "stage", "conversion_rate"), "df_conversion")
    monthly = df_conversion[
        (df_conversion["stage"] == CONVERSION_STAGE)
        & (df_conversion["scope"] != SCOPE_ALL)
    ].copy()
    if monthly.empty:
        return pd.DataFrame(columns=columns)

    monthly = monthly.rename(columns={"scope": "month"})
    monthly["month"] = _month_categorical(monthly["month"])
    return (
        monthly[columns]
        .sort_values("month")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# KPI 5 & 6 — Financial Revenue dan In-Kind Value (SELALU TERPISAH)
# ---------------------------------------------------------------------------


def get_financial_revenue(df_financial: pd.DataFrame) -> float:
    """Total Financial Revenue (uang tunai diterima).

    Angka acuan hasil validasi Phase 6: Rp26.750.000.
    """
    if _empty(df_financial):
        return 0.0
    _require_columns(df_financial, ("revenue",), "df_financial")
    return float(df_financial["revenue"].sum())


def get_inkind_value(df_inkind: pd.DataFrame) -> float:
    """Total In-Kind Value (taksiran nilai dukungan non-tunai).

    Angka acuan hasil validasi Phase 7: Rp119.685.000.
    """
    if _empty(df_inkind):
        return 0.0
    _require_columns(df_inkind, ("inkind_value",), "df_inkind")
    return float(df_inkind["inkind_value"].sum())


def get_revenue_by_month(df_revenue: pd.DataFrame, amount_column: str) -> pd.DataFrame:
    """Rekap nilai per bulan untuk df_financial ATAU df_inkind.

    Dipakai untuk dua dataset berbeda secara terpisah; fungsi ini tidak
    pernah menggabungkan keduanya.

    Returns:
        DataFrame kolom: month, records, amount
    """
    columns = ["month", "records", "amount"]
    if _empty(df_revenue):
        return pd.DataFrame(columns=columns)

    _require_columns(df_revenue, ("month", amount_column), "dataframe revenue")
    grouped = df_revenue.groupby("month", observed=False)[amount_column].agg(
        records="count", amount="sum"
    )
    result = grouped.rename_axis("month").reset_index()
    result["records"] = result["records"].astype(int)
    return result[columns]


def get_revenue_by_partner(df_revenue: pd.DataFrame, amount_column: str) -> pd.DataFrame:
    """Rekap nilai per partner, urut dari terbesar.

    Returns:
        DataFrame kolom: partner_name, records, amount
    """
    columns = ["partner_name", "records", "amount"]
    if _empty(df_revenue):
        return pd.DataFrame(columns=columns)

    _require_columns(df_revenue, ("partner_name", amount_column), "dataframe revenue")
    grouped = df_revenue.groupby("partner_name", observed=True)[amount_column].agg(
        records="count", amount="sum"
    )
    result = grouped.rename_axis("partner_name").reset_index()
    result["records"] = result["records"].astype(int)
    return result.sort_values(
        ["amount", "partner_name"], ascending=[False, True]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# KPI 7 — Distribusi stakeholder
# ---------------------------------------------------------------------------


def get_stakeholder_distribution(
    df_partner: pd.DataFrame, active_only: bool = False
) -> pd.DataFrame:
    """Sebaran partner per stakeholder grouping.

    Args:
        active_only: True untuk menghitung hanya partner aktif.

    Returns:
        DataFrame kolom: stakeholder, partner_count, share_percent
    """
    columns = ["stakeholder", "partner_count", "share_percent"]
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)

    _require_columns(df_partner, ("stakeholder",), "df_partner")
    source = get_active_partners(df_partner) if active_only else df_partner
    if source.empty:
        return pd.DataFrame(columns=columns)

    labels = source["stakeholder"].fillna("").replace("", STAKEHOLDER_UNKNOWN)
    counts = labels.value_counts()
    total = int(counts.sum())

    result = counts.rename_axis("stakeholder").reset_index(name="partner_count")
    result["share_percent"] = (result["partner_count"] / total * 100).round(2)
    return result.sort_values(
        ["partner_count", "stakeholder"], ascending=[False, True]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# KPI 8 — Document Tracker (hanya partner aktif)
# ---------------------------------------------------------------------------


def get_document_tracker(df_partner: pd.DataFrame) -> pd.DataFrame:
    """Kelengkapan dokumen per partner AKTIF.

    Returns:
        DataFrame kolom: partner_name, stakeholder, has_proposal, has_mom,
        has_loa, has_invoice, documents_complete, documents_missing
    """
    document_columns = [f"has_{document}" for document in DOCUMENT_FIELDS]
    columns = (
        ["partner_name", "stakeholder"]
        + document_columns
        + ["documents_complete", "documents_missing"]
    )
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)

    _require_columns(
        df_partner,
        tuple(["partner_name", "stakeholder", "is_active"] + document_columns),
        "df_partner",
    )
    active = get_active_partners(df_partner)
    if active.empty:
        return pd.DataFrame(columns=columns)

    tracker = active[["partner_name", "stakeholder"] + document_columns].copy()
    tracker["documents_complete"] = tracker[document_columns].all(axis=1)
    tracker["documents_missing"] = (
        len(document_columns) - tracker[document_columns].sum(axis=1)
    ).astype(int)
    # Yang dokumennya paling banyak bolong ditaruh di atas: itu yang perlu ditagih.
    return tracker.sort_values(
        ["documents_missing", "partner_name"], ascending=[False, True]
    ).reset_index(drop=True)


def get_document_completeness(df_partner: pd.DataFrame) -> pd.DataFrame:
    """Rekap per jenis dokumen untuk partner aktif.

    Returns:
        DataFrame kolom: document, available, missing, available_percent
    """
    columns = ["document", "available", "missing", "available_percent"]
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)

    active = get_active_partners(df_partner)
    if active.empty:
        return pd.DataFrame(columns=columns)

    total = len(active)
    rows = []
    for document in DOCUMENT_FIELDS:
        available = int(active[f"has_{document}"].sum())
        rows.append(
            {
                "document": document,
                "available": available,
                "missing": total - available,
                "available_percent": round(available / total * 100, 2),
            }
        )
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------
# KPI 9 — Partnership Expiry Tracker
# ---------------------------------------------------------------------------


def categorize_expiry(months_remaining) -> str:
    """Kategori expiry berbasis bulan (sumber hanya memberi level bulan)."""
    if months_remaining is None or pd.isna(months_remaining):
        return EXPIRY_UNKNOWN
    months = int(months_remaining)
    if months < 0:
        return EXPIRY_EXPIRED
    if months == 0:
        return EXPIRY_THIS_MONTH
    if months <= EXPIRY_SOON_MONTHS:
        return EXPIRY_SOON
    return EXPIRY_LATER


def get_partnership_expiry(
    df_partner: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
    active_only: bool = False,
) -> pd.DataFrame:
    """Daftar kontrak partner urut dari yang paling dekat berakhir.

    Args:
        reference_date: acuan penghitungan sisa bulan. Kalau None, dipakai
            kolom months_remaining yang sudah dihitung saat df_partner dibangun.
        active_only: True untuk hanya menampilkan partner aktif.

    Returns:
        DataFrame kolom: partner_name, stakeholder, signed_month_raw,
        end_month_raw, end_month, months_remaining, expiry_category,
        has_loa, is_active
    """
    columns = [
        "partner_name", "stakeholder", "signed_month_raw", "end_month_raw",
        "end_month", "months_remaining", "expiry_category", "has_loa", "is_active",
    ]
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)

    _require_columns(
        df_partner,
        ("partner_name", "stakeholder", "signed_month_raw", "end_month_raw",
         "end_month", "months_remaining", "has_loa", "is_active"),
        "df_partner",
    )

    source = get_active_partners(df_partner) if active_only else df_partner.copy()
    if source.empty:
        return pd.DataFrame(columns=columns)

    result = source[[column for column in columns if column != "expiry_category"]].copy()

    if reference_date is not None:
        reference_period = pd.Timestamp(reference_date).normalize().to_period("M")
        result["months_remaining"] = pd.array(
            [
                (period - reference_period).n
                if period is not pd.NaT and not pd.isna(period)
                else pd.NA
                for period in result["end_month"]
            ],
            dtype="Int64",
        )

    result["expiry_category"] = result["months_remaining"].map(categorize_expiry)
    # Kontrak tanpa Month End ditaruh paling akhir, bukan dianggap paling dekat.
    return (
        result[columns]
        .sort_values("end_month", na_position="last")
        .reset_index(drop=True)
    )


def get_expiry_summary(
    df_partner: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
    active_only: bool = False,
) -> pd.DataFrame:
    """Jumlah kontrak per kategori expiry, urut dari paling mendesak.

    Kategori yang kosong tetap ditampilkan dengan nilai 0 supaya
    perubahan angka antar periode mudah dibaca.

    Returns:
        DataFrame kolom: expiry_category, partner_count
    """
    columns = ["expiry_category", "partner_count"]
    tracker = get_partnership_expiry(
        df_partner, reference_date=reference_date, active_only=active_only
    )
    counts = (
        tracker["expiry_category"].value_counts()
        if not tracker.empty
        else pd.Series(dtype=int)
    )
    rows = [
        {"expiry_category": category, "partner_count": int(counts.get(category, 0))}
        for category in EXPIRY_CATEGORIES
    ]
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------
# KPI 10 — Post-partnership: Partnership Survey & Partnership Report
# ---------------------------------------------------------------------------


def _resolve_reference(reference_date: pd.Timestamp | None) -> pd.Timestamp:
    """Tanggal acuan yang dipakai membandingkan tanggal akhir partnership."""
    if reference_date is None:
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(reference_date).normalize()


def deduplicate_partners(df_partner: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per partner, berdasarkan nama ternormalisasi.

    Kalau nama yang sama muncul beberapa kali (mis. partner di-raise ulang),
    baris yang disimpan adalah yang tanggal akhirnya PALING BARU. Alasannya:
    status post-partnership harus mengikuti kerja sama terakhir, bukan yang
    sudah lewat. Tanpa ini satu partner bisa terhitung dua kali di metrik.
    """
    if _empty(df_partner):
        return df_partner if df_partner is not None else pd.DataFrame()
    _require_columns(df_partner, ("partner_name",), "df_partner")

    result = df_partner.copy()
    result["partner_key"] = result["partner_name"].map(normalize_partner_name)

    sort_columns = ["partner_key"]
    ascending = [True]
    if "end_date" in result.columns:
        sort_columns.append("end_date")
        ascending.append(False)

    result = result.sort_values(sort_columns, ascending=ascending, na_position="last")
    return result.drop_duplicates(subset="partner_key", keep="first")


def _reference_period(reference_date: pd.Timestamp | None) -> pd.Period:
    """Bulan acuan. Post-partnership dinilai per BULAN, bukan per hari."""
    return _resolve_reference(reference_date).to_period("M")


def end_period_of(row) -> pd.Period | None:
    """Bulan berakhir sebuah baris partner.

    Diambil dari kolom end_month kalau ada (itu nilai asli setingkat bulan
    dari sheet). Kalau tidak ada, diturunkan dari end_date. None kalau
    keduanya kosong - artinya tanggal akhirnya tidak diketahui.
    """
    period = getattr(row, "end_month", None)
    if period is not None and not pd.isna(period):
        if isinstance(period, pd.Period):
            return period
        return pd.Timestamp(period).to_period("M")

    end_date = getattr(row, "end_date", None)
    if end_date is not None and not pd.isna(end_date):
        return pd.Timestamp(end_date).to_period("M")
    return None


def is_post_partnership_due(
    end_period, reference_date: pd.Timestamp | None = None
) -> bool:
    """True kalau Partnership Report & Survey SUDAH ditagih.

    Aturan tunggal yang dipakai seluruh dashboard:

        due  <=>  end_month <= bulan acuan

    Jadi partner yang berakhir BULAN INI sudah masuk hitungan, bukan lagi
    "Not Required Yet". Lihat aturan 7 di docstring modul.
    """
    if end_period is None or pd.isna(end_period):
        return False
    if not isinstance(end_period, pd.Period):
        end_period = pd.Timestamp(end_period).to_period("M")
    return end_period <= _reference_period(reference_date)


def get_completed_partners(
    df_partner: pd.DataFrame, reference_date: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Partner yang sudah memasuki atau melewati bulan berakhirnya.

    Partner yang bulan berakhirnya masih di depan tidak pernah masuk ke
    sini. Partner tanpa Month End juga tidak, karena bulan akhirnya tidak
    diketahui - bukan karena dianggap masih berjalan.
    """
    if _empty(df_partner):
        return df_partner if df_partner is not None else pd.DataFrame()
    _require_columns(df_partner, ("partner_name",), "df_partner")
    if "end_month" not in df_partner.columns and "end_date" not in df_partner.columns:
        raise KeyError("df_partner tidak punya kolom: end_month atau end_date")

    unique = deduplicate_partners(df_partner)
    keep = [
        is_post_partnership_due(end_period_of(row), reference_date)
        for row in unique.itertuples()
    ]
    result = unique[pd.Series(keep, index=unique.index)]
    if result.empty:
        return result
    sort_column = "end_date" if "end_date" in result.columns else "end_month"
    return result.sort_values(sort_column, ascending=False).reset_index(drop=True)


def _names_of(df: pd.DataFrame | None, column: str = "partner_name") -> list[str]:
    """Daftar nama unik pada sebuah dataframe sumber. [] kalau tidak ada."""
    if _empty(df) or column not in df.columns:
        return []
    return [str(name) for name in df[column].dropna().unique() if str(name).strip()]


def _report_lookup(df_report: pd.DataFrame | None) -> dict[str, dict]:
    """Petakan nama partner di National_1.2 ke baris ringkasnya.

    Kalau satu partner tercatat beberapa kali, baris yang SUDAH punya
    dokumen laporan menang. Laporan yang sudah ada tidak boleh hilang
    hanya karena ada baris lain yang masih kosong.
    """
    if _empty(df_report) or "partner_name" not in df_report.columns:
        return {}

    lookup: dict[str, dict] = {}
    for row in df_report.itertuples():
        name = str(getattr(row, "partner_name", "") or "")
        if not name.strip():
            continue
        entry = {
            "partner_name": name,
            "has_report": bool(getattr(row, "has_report", False)),
            "report_link_raw": getattr(row, "report_link_raw", None),
            "pic_aiesec": getattr(row, "pic_aiesec", None),
        }
        existing = lookup.get(name)
        if existing is None or (entry["has_report"] and not existing["has_report"]):
            lookup[name] = entry
    return lookup


def get_post_partnership_tracker(
    df_partner: pd.DataFrame,
    df_report: pd.DataFrame | None = None,
    df_survey: pd.DataFrame | None = None,
    reference_date: pd.Timestamp | None = None,
    unavailable: tuple[str, ...] | list[str] = (),
) -> pd.DataFrame:
    """Status Partnership Survey & Partnership Report per partner SELESAI.

    Partnership Survey  -> dicari di PSC (df_survey).
    Partnership Report  -> dicari di National_1.2 (df_report). Yang dipakai
        sebagai bukti adalah kolom dokumen laporannya (has_report), BUKAN
        sekadar nama partner ditemukan: di sumber ada partner yang tercatat
        di 1.2 tetapi kolom laporannya masih kosong.

    Args:
        df_partner: sumber tanggal akhir partnership (National_1.1).
        df_report: hasil build_df_report(). None/kosong -> semua Missing.
        df_survey: hasil build_df_survey(). None/kosong -> semua Missing.
        reference_date: tanggal acuan. Default hari ini.
        unavailable: kunci sumber yang GAGAL DIBACA (POST_SOURCE_SURVEY /
            POST_SOURCE_REPORT). Statusnya menjadi "Data Unavailable", bukan
            "Missing" - sheet kosong dan sheet gagal dibaca adalah dua
            keadaan yang berbeda dan tidak boleh disamakan.

    Returns:
        DataFrame kolom: partner_name, stakeholder, end_month_raw, end_date,
        days_since_end, survey_status, report_status, survey_source_name,
        report_source_name, report_recorded, is_fully_completed.
        Urut dari partnership yang paling baru berakhir.
    """
    columns = [
        "partner_name", "stakeholder", "end_month_raw", "end_date",
        "days_since_end", "is_final_month", "survey_status", "report_status",
        "survey_source_name", "report_source_name", "report_recorded",
        "is_fully_completed",
    ]
    completed = get_completed_partners(df_partner, reference_date=reference_date)
    if completed.empty:
        return pd.DataFrame(columns=columns)

    offline = set(unavailable or ())
    survey_offline = POST_SOURCE_SURVEY in offline
    report_offline = POST_SOURCE_REPORT in offline

    reference = _resolve_reference(reference_date)
    reference_period = _reference_period(reference_date)
    survey_names = _names_of(df_survey)
    report_rows = _report_lookup(df_report)
    report_names = list(report_rows)

    rows = []
    for record in completed.itertuples():
        partner_name = str(record.partner_name)

        survey_match = match_partner_name(partner_name, survey_names)
        report_match = match_partner_name(partner_name, report_names)
        report_entry = report_rows.get(report_match) if report_match else None
        has_report = bool(report_entry and report_entry["has_report"])

        if survey_offline:
            survey_status = POST_STATUS_UNAVAILABLE
        else:
            survey_status = (
                POST_STATUS_COMPLETED if survey_match else POST_STATUS_MISSING
            )

        if report_offline:
            report_status = POST_STATUS_UNAVAILABLE
        else:
            report_status = (
                POST_STATUS_COMPLETED if has_report else POST_STATUS_MISSING
            )

        end_date = getattr(record, "end_date", pd.NaT)
        end_period = end_period_of(record)
        # Bisa NEGATIF untuk partner yang berakhir bulan ini tapi tanggalnya
        # belum tiba. Itu wajar sejak aturan berbasis bulan dipakai, jadi
        # pemanggil harus membaca is_final_month sebelum menulis "x hari lalu".
        days_since_end = (
            int((reference - pd.Timestamp(end_date).normalize()).days)
            if end_date is not None and not pd.isna(end_date)
            else None
        )

        rows.append(
            {
                "partner_name": partner_name,
                "stakeholder": getattr(record, "stakeholder", "") or "",
                "end_month_raw": getattr(record, "end_month_raw", None),
                "end_date": end_date,
                "days_since_end": days_since_end,
                "is_final_month": bool(
                    end_period is not None and end_period == reference_period
                ),
                "survey_status": survey_status,
                "report_status": report_status,
                "survey_source_name": survey_match,
                "report_source_name": report_match,
                # Tercatat di 1.2 tapi dokumennya belum ada: dibedakan supaya
                # tindak lanjutnya jelas (menagih dokumen vs mendata partner).
                "report_recorded": report_match is not None,
                "is_fully_completed": (
                    survey_status == POST_STATUS_COMPLETED
                    and report_status == POST_STATUS_COMPLETED
                ),
            }
        )

    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values("end_date", ascending=False, na_position="last")
        .reset_index(drop=True)
    )


def _available_requirements(tracker: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Kewajiban yang sumbernya bisa dibaca pada tracker ini.

    Kewajiban yang seluruh barisnya "Data Unavailable" dikeluarkan dari
    perhitungan: kita tidak boleh menghitung persentase dari sumber yang
    tidak terbaca.
    """
    available = []
    for requirement in POST_REQUIREMENTS:
        column = requirement[0]
        if column not in tracker.columns:
            continue
        if (tracker[column] == POST_STATUS_UNAVAILABLE).all():
            continue
        available.append(requirement)
    return available


def get_post_partnership_summary(tracker: pd.DataFrame) -> dict[str, float | int]:
    """Ringkasan Completed Partnership Tracker.

    Penyebut persentase HANYA partnership yang sudah berakhir: setiap
    partnership selesai wajib satu Survey dan satu Report, jadi

        completion = (survey selesai + report selesai)
                     / (partnership selesai x jumlah kewajiban) x 100

    Partner aktif tidak pernah masuk penyebut. Kewajiban yang sumbernya
    gagal dibaca juga tidak masuk penyebut, supaya persentasenya tidak
    turun hanya karena satu sheet sedang tidak bisa diakses.

    Returns:
        dict: completed_partnerships, survey_completed, survey_missing,
        report_completed, report_missing, report_recorded_without_document,
        fully_completed, required_documents, completion_percent,
        unavailable_requirements.
    """
    if _empty(tracker):
        return {
            "completed_partnerships": 0,
            "survey_completed": 0,
            "survey_missing": 0,
            "report_completed": 0,
            "report_missing": 0,
            "report_recorded_without_document": 0,
            "fully_completed": 0,
            "required_documents": 0,
            "completion_percent": float("nan"),
            "unavailable_requirements": 0,
        }

    _require_columns(
        tracker,
        ("survey_status", "report_status", "is_fully_completed", "report_recorded"),
        "tracker post-partnership",
    )

    total = int(len(tracker))
    survey_done = int((tracker["survey_status"] == POST_STATUS_COMPLETED).sum())
    report_done = int((tracker["report_status"] == POST_STATUS_COMPLETED).sum())
    available = _available_requirements(tracker)
    required = total * len(available)
    completed_documents = sum(
        int((tracker[column] == POST_STATUS_COMPLETED).sum())
        for column, _label, _source in available
    )

    def missing_count(column: str) -> int:
        return int((tracker[column] == POST_STATUS_MISSING).sum())

    return {
        "completed_partnerships": total,
        "survey_completed": survey_done,
        "survey_missing": missing_count("survey_status"),
        "report_completed": report_done,
        "report_missing": missing_count("report_status"),
        "report_recorded_without_document": int(
            (
                tracker["report_recorded"]
                & (tracker["report_status"] == POST_STATUS_MISSING)
            ).sum()
        ),
        "fully_completed": int(tracker["is_fully_completed"].sum()),
        "required_documents": required,
        "completion_percent": (
            round(completed_documents / required * 100, 2)
            if required
            else float("nan")
        ),
        "unavailable_requirements": len(POST_REQUIREMENTS) - len(available),
    }


def get_post_partnership_completeness(tracker: pd.DataFrame) -> pd.DataFrame:
    """Rekap per jenis kewajiban, untuk grafik.

    Kewajiban yang sumbernya gagal dibaca tidak digambar, karena angka
    "0 completed" di situ akan terbaca sebagai fakta.

    Returns:
        DataFrame kolom: requirement, completed, missing, completed_percent
    """
    columns = ["requirement", "completed", "missing", "completed_percent"]
    if _empty(tracker):
        return pd.DataFrame(columns=columns)

    total = int(len(tracker))
    rows = []
    for status_column, label, _source in _available_requirements(tracker):
        completed = int((tracker[status_column] == POST_STATUS_COMPLETED).sum())
        rows.append(
            {
                "requirement": label,
                "completed": completed,
                "missing": total - completed,
                "completed_percent": round(completed / total * 100, 2) if total else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def get_partnership_document_status(
    df_partner: pd.DataFrame,
    df_report: pd.DataFrame | None = None,
    df_survey: pd.DataFrame | None = None,
    reference_date: pd.Timestamp | None = None,
    unavailable: tuple[str, ...] | list[str] = (),
) -> pd.DataFrame:
    """Status post-partnership untuk SELURUH partner.

    Dipakai section "Partnership Report & Survey Tracker" di Document
    Tracker, yang perlu menunjukkan partner mana yang memang BELUM ditagih:

        bulan berakhir sudah tiba/lewat -> Completed / Missing
        bulan berakhir masih di depan   -> Not Required Yet
        tanpa Month End                 -> No End Date
        sumber gagal dibaca             -> Data Unavailable

    Perbandingannya per BULAN (lihat aturan 7 di docstring modul), jadi
    partner yang berakhir bulan ini sudah ditagih - tidak lagi tertulis
    "Not Required Yet".

    Kolom *_submitted_early menandai partner yang mengumpulkan sebelum
    bulan berakhirnya tiba. Statusnya tetap "Not Required Yet" supaya tidak
    terbaca sebagai tagihan, tetapi progresnya tidak hilang dari laporan.

    Returns:
        DataFrame kolom: partner_name, stakeholder, end_month_raw, end_date,
        is_due, is_final_month, survey_status, report_status,
        survey_submitted_early, report_submitted_early, survey_source_name,
        report_source_name.
    """
    columns = [
        "partner_name", "stakeholder", "end_month_raw", "end_date",
        "is_due", "is_final_month", "survey_status", "report_status",
        "survey_submitted_early", "report_submitted_early",
        "survey_source_name", "report_source_name",
    ]
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)
    _require_columns(df_partner, ("partner_name",), "df_partner")
    if "end_month" not in df_partner.columns and "end_date" not in df_partner.columns:
        raise KeyError("df_partner tidak punya kolom: end_month atau end_date")

    offline = set(unavailable or ())
    survey_offline = POST_SOURCE_SURVEY in offline
    report_offline = POST_SOURCE_REPORT in offline

    reference_period = _reference_period(reference_date)
    survey_names = _names_of(df_survey)
    report_rows = _report_lookup(df_report)
    report_names = list(report_rows)

    rows = []
    for record in deduplicate_partners(df_partner).itertuples():
        partner_name = str(record.partner_name)
        end_date = getattr(record, "end_date", pd.NaT)
        end_period = end_period_of(record)

        survey_match = match_partner_name(partner_name, survey_names)
        report_match = match_partner_name(partner_name, report_names)
        report_entry = report_rows.get(report_match) if report_match else None
        has_report = bool(report_entry and report_entry["has_report"])

        if end_period is None:
            survey_status = report_status = POST_STATUS_NO_END_DATE
            is_due = False
        elif end_period <= reference_period:
            is_due = True
            survey_status = (
                POST_STATUS_COMPLETED if survey_match else POST_STATUS_MISSING
            )
            report_status = POST_STATUS_COMPLETED if has_report else POST_STATUS_MISSING
        else:
            is_due = False
            survey_status = report_status = POST_STATUS_NOT_REQUIRED

        # Sumber tidak terbaca hanya menimpa status yang seharusnya menilai
        # dokumen. "Not Required Yet" dan "No End Date" tidak bergantung pada
        # sumber itu, jadi tetap apa adanya.
        if survey_offline and is_due:
            survey_status = POST_STATUS_UNAVAILABLE
        if report_offline and is_due:
            report_status = POST_STATUS_UNAVAILABLE

        rows.append(
            {
                "partner_name": partner_name,
                "stakeholder": getattr(record, "stakeholder", "") or "",
                "end_month_raw": getattr(record, "end_month_raw", None),
                "end_date": end_date,
                "is_due": is_due,
                "is_final_month": bool(
                    end_period is not None and end_period == reference_period
                ),
                "survey_status": survey_status,
                "report_status": report_status,
                "survey_submitted_early": (not is_due) and bool(survey_match),
                "report_submitted_early": (not is_due) and has_report,
                "survey_source_name": survey_match,
                "report_source_name": report_match,
            }
        )

    result = pd.DataFrame(rows, columns=columns)
    # Yang sudah wajib ditaruh di atas, lalu yang paling dekat berakhir.
    result["_order"] = result["is_due"].map({True: 0, False: 1})
    return (
        result.sort_values(["_order", "end_date", "partner_name"], na_position="last")
        .drop(columns="_order")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# KPI 11 — Partner yang segera berakhir (berbasis HARI)
# ---------------------------------------------------------------------------


def categorize_urgency(days_remaining) -> str:
    """Tingkat urgensi kartu partner yang segera berakhir.

    Berbasis HARI, bukan bulan, karena kartu ini memang menampilkan sisa
    hari. Kategori bulanan di get_partnership_expiry() tetap dipakai untuk
    grafik ringkasan dan tidak diubah.
    """
    if days_remaining is None or pd.isna(days_remaining):
        return URGENCY_NORMAL
    days = int(days_remaining)
    if days <= EXPIRY_CRITICAL_DAYS:
        return URGENCY_CRITICAL
    if days <= EXPIRY_WARNING_DAYS:
        return URGENCY_WARNING
    return URGENCY_NORMAL


def get_expiring_partners(
    df_partner: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
    df_report: pd.DataFrame | None = None,
    within_days: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Partner aktif yang partnership-nya akan berakhir, urut paling dekat.

    Args:
        reference_date: acuan penghitungan sisa hari. Default hari ini.
        df_report: kalau diisi, PIC FROM AIESEC diambil dari National_1.2
            lewat pencocokan nama. National_1.1 tidak punya kolom itu
            (kolom "PIC" di sana adalah kontak pihak partner), jadi PIC
            dibiarkan None kalau tidak ketemu - tidak pernah dikarang.
        within_days: batasi hanya yang berakhir dalam N hari.
        limit: batasi jumlah baris.

    Returns:
        DataFrame kolom: partner_name, stakeholder, pic_aiesec, end_date,
        end_month_raw, days_remaining, months_remaining, urgency.
        Urut days_remaining ASC (paling dekat berakhir lebih dulu).
    """
    columns = [
        "partner_name", "stakeholder", "pic_aiesec", "end_date",
        "end_month_raw", "days_remaining", "months_remaining", "urgency",
    ]
    if _empty(df_partner):
        return pd.DataFrame(columns=columns)
    _require_columns(
        df_partner, ("partner_name", "end_date", "is_active"), "df_partner"
    )

    reference = _resolve_reference(reference_date)
    unique = deduplicate_partners(df_partner)
    ongoing = unique[
        unique["is_active"]
        & unique["end_date"].notna()
        & (unique["end_date"] >= reference)
    ]
    if ongoing.empty:
        return pd.DataFrame(columns=columns)

    report_rows = _report_lookup(df_report)
    report_names = list(report_rows)

    rows = []
    for record in ongoing.itertuples():
        partner_name = str(record.partner_name)
        days_remaining = int((pd.Timestamp(record.end_date).normalize() - reference).days)

        pic = None
        if report_names:
            match = match_partner_name(partner_name, report_names)
            if match:
                candidate = report_rows[match].get("pic_aiesec")
                if candidate is not None and not pd.isna(candidate):
                    pic = str(candidate).strip() or None

        rows.append(
            {
                "partner_name": partner_name,
                "stakeholder": getattr(record, "stakeholder", "") or "",
                "pic_aiesec": pic,
                "end_date": record.end_date,
                "end_month_raw": getattr(record, "end_month_raw", None),
                "days_remaining": days_remaining,
                "months_remaining": getattr(record, "months_remaining", pd.NA),
                "urgency": categorize_urgency(days_remaining),
            }
        )

    result = pd.DataFrame(rows, columns=columns)
    if within_days is not None:
        result = result[result["days_remaining"] <= int(within_days)]
    result = result.sort_values(
        ["days_remaining", "partner_name"]
    ).reset_index(drop=True)
    if limit is not None:
        result = result.head(int(limit))
    return result


# ---------------------------------------------------------------------------
# Ringkasan headline KPI
# ---------------------------------------------------------------------------


def get_kpi_summary(
    df_mr: pd.DataFrame,
    df_partner: pd.DataFrame,
    df_conversion: pd.DataFrame,
    df_financial: pd.DataFrame,
    df_inkind: pd.DataFrame,
    scope: str = SCOPE_ALL,
) -> dict[str, float | int]:
    """Kumpulkan KPI utama dalam satu dict untuk dipakai kartu dashboard.

    Sengaja TIDAK ada kunci 'total_revenue': Financial Revenue dan
    In-Kind Value tidak boleh dijumlahkan.
    """
    return {
        "total_mr": get_total_mr(df_mr),
        "partner_count": get_partner_count(df_partner),
        "active_partners": get_active_partner_count(df_partner),
        "conversion_rate": get_conversion_rate(df_conversion, scope=scope),
        "financial_revenue": get_financial_revenue(df_financial),
        "inkind_value": get_inkind_value(df_inkind),
    }
