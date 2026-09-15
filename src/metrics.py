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
"""

from __future__ import annotations

import pandas as pd

from .preparation import (
    CONVERSION_STAGE,
    DOCUMENT_FIELDS,
    FUNNEL_STAGES,
    TERM_MONTH_ORDER,
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
