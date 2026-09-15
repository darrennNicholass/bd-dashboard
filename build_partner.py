"""
build_partner.py - PHASE 5: BUILD & VALIDATE df_partner + df_conversion

Membangun data partner dari National_1.1 lalu memvalidasinya supaya bisa
dicek silang dengan ESSM sebelum dipakai jadi KPI.

Yang dilaporkan:
     1. Kolom yang berhasil diresolusi dari header berlapis tiga
     2. Bentuk df_partner + contoh baris
     3. Jumlah partner per bulan
     4. Uji pemetaan kolom dokumen (apakah Link yang benar?)
     5. Kelengkapan dokumen per jenis
     6. Periode kontrak: Month of Signed & Month End
     7. ACTIVE PARTNERS menurut aturan BD (ada LoA + kontrak belum berakhir)
     8. Perbandingan dengan kolom Status bawaan sheet
     9. Distribusi stakeholder
    10. Document Tracker (hanya partner aktif)
    11. Expiry Tracker
    12. df_conversion

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_partner.py
"""

from __future__ import annotations

import sys

import pandas as pd

from src import preparation

pd.set_option("display.width", 150)
pd.set_option("display.max_columns", 40)


def line(char: str = "-", width: int = 78) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def tick(flag: bool) -> str:
    return "ADA" if flag else "-"


def main() -> int:
    print()
    line("#")
    print("PHASE 5 - BUILD & VALIDATE df_partner + df_conversion")
    line("#")

    reference_date = pd.Timestamp.today().normalize()
    print()
    print("Aturan yang dipakai (dikonfirmasi BD di Phase 4):")
    print("  active partner : ada dokumen LoA DAN Month End >= bulan berjalan")
    print("  Month End      : level bulan, diperlakukan sebagai AKHIR bulan")
    print("  dokumen ada    : sel terisi DAN bukan '-'")
    print("  conversion     : diambil dari sheet, tidak dihitung ulang")
    print(f"  tanggal acuan  : {reference_date.date()} (bulan {reference_date.to_period('M')})")

    try:
        grid = preparation.load_partner_grid()
        columns = preparation.resolve_partner_columns(grid)
        df = preparation.build_df_partner(grid, reference_date=reference_date)
        df_conversion = preparation.build_df_conversion(grid)
    except Exception as exc:
        print(f"\nGagal membangun data partner: {type(exc).__name__}: {exc}")
        return 1

    if df.empty:
        print("\ndf_partner kosong. Periksa aturan ekstraksi.")
        return 1

    # ------------------------------------------------------------------
    section("1. RESOLUSI KOLOM DARI HEADER BERLAPIS")
    from src.sheets import column_letter

    for field, index in sorted(columns.items(), key=lambda item: item[1]):
        print(f"  {field:<34} -> {column_letter(index + 1)}")

    # ------------------------------------------------------------------
    section("2. BENTUK df_partner")
    print(f"Shape : {df.shape[0]} baris x {df.shape[1]} kolom")
    print()
    print("Kolom:")
    for column, dtype in df.dtypes.items():
        print(f"  {column:<22} {dtype}")

    # ------------------------------------------------------------------
    section("3. PARTNER PER BULAN")
    per_month = df.groupby("month", observed=False).size()
    for month, count in per_month.items():
        if count:
            print(f"  {str(month):<14} {count:>4}")
    line()
    print(f"  {'TOTAL':<14} {len(df):>4}")

    # ------------------------------------------------------------------
    section("4. UJI PEMETAAN KOLOM DOKUMEN")
    print("Kolom dokumen di sheet semuanya berlabel 'Link'. Pemetaan dibuat")
    print("dari tahap funnel sebelumnya, lalu diuji terhadap isi selnya.")
    print()
    print(preparation.validate_document_columns(df).to_string(index=False))

    # ------------------------------------------------------------------
    section("5. KELENGKAPAN DOKUMEN (seluruh 35 partner)")
    print(f"  {'dokumen':<12}{'ADA':>6}{'tidak':>8}")
    line()
    for document in preparation.DOCUMENT_FIELDS:
        ada = int(df[f"has_{document}"].sum())
        print(f"  {document:<12}{ada:>6}{len(df) - ada:>8}")

    # ------------------------------------------------------------------
    section("6. PERIODE KONTRAK")
    print(f"Partner dengan Month of Signed : {int(df['signed_month'].notna().sum())}")
    print(f"Partner dengan Month End       : {int(df['end_month'].notna().sum())}")
    print(f"Gagal parse Month of Signed    : "
          f"{int(df['signed_month_raw'].notna().sum() - df['signed_month'].notna().sum())}")
    print(f"Gagal parse Month End          : "
          f"{int(df['end_month_raw'].notna().sum() - df['end_month'].notna().sum())}")
    print()
    print("Sebaran Month End (mentah -> hasil parse -> tanggal akhir bulan):")
    sample = (
        df[df["end_month"].notna()]
        .groupby(["end_month_raw", "end_month"], observed=True)
        .agg(jumlah=("partner_name", "size"), akhir_bulan=("end_date", "first"))
        .reset_index()
        .sort_values("end_month")
    )
    print(sample.to_string(index=False))

    # ------------------------------------------------------------------
    section("7. ACTIVE PARTNERS (aturan BD)")
    active = df[df["is_active"]]
    print(f"Partner dengan LoA                       : {int(df['has_loa'].sum())}")
    print(f"Partner kontrak belum berakhir           : {int(df['is_ongoing'].sum())}")
    print()
    print(f"ACTIVE PARTNERS (LoA + belum berakhir)   : {len(active)}")
    print()
    print("Daftar partner aktif:")
    print(
        active[["partner_name", "stakeholder", "end_month_raw", "months_remaining"]]
        .to_string(index=False)
    )

    print()
    print("Partner yang kontraknya belum berakhir TAPI tidak punya LoA")
    print("(karena itu tidak dihitung aktif):")
    excluded = df[df["is_ongoing"] & ~df["has_loa"]]
    if excluded.empty:
        print("  (tidak ada)")
    else:
        print(
            excluded[["partner_name", "end_month_raw", "stage_contract_signed", "status_source"]]
            .to_string(index=False)
        )

    # ------------------------------------------------------------------
    section("8. BANDING DENGAN KOLOM Status BAWAAN SHEET")
    print("Kolom 'Status (For new sales)' TIDAK dipakai untuk KPI, hanya audit.")
    print()
    crosstab = pd.crosstab(
        df["status_source"], df["is_active"], margins=True, margins_name="TOTAL"
    )
    print(crosstab.to_string())
    print()
    mismatch = df[(df["status_source"].str.casefold() == "active") & ~df["is_active"]]
    print(f"Ditandai Active di sheet tapi TIDAK aktif menurut aturan BD: {len(mismatch)}")
    if not mismatch.empty:
        print(
            mismatch[["partner_name", "end_month_raw", "has_loa", "months_remaining"]]
            .to_string(index=False)
        )

    # ------------------------------------------------------------------
    section("9. DISTRIBUSI STAKEHOLDER")
    print("Seluruh partner:")
    for value, count in df["stakeholder"].value_counts().items():
        print(f"  {value:<34} {count:>4}")
    print()
    print("Hanya partner aktif:")
    for value, count in active["stakeholder"].value_counts().items():
        print(f"  {value:<34} {count:>4}")

    # ------------------------------------------------------------------
    section("10. DOCUMENT TRACKER (hanya partner aktif)")
    tracker = active[
        ["partner_name", "has_proposal", "has_mom", "has_loa", "has_invoice"]
    ].copy()
    for document in preparation.DOCUMENT_FIELDS:
        tracker[document.upper()] = tracker[f"has_{document}"].map(tick)
    print(
        tracker[["partner_name", "PROPOSAL", "MOM", "LOA", "INVOICE"]].to_string(index=False)
    )

    # ------------------------------------------------------------------
    section("11. EXPIRY TRACKER")
    print("Granularitas sumber hanya bulan, jadi kategori berbasis bulan.")
    print()
    expiry = df[df["end_month"].notna()].copy()

    def categorize(months_remaining) -> str:
        if pd.isna(months_remaining):
            return "tanpa data"
        if months_remaining < 0:
            return "expired"
        if months_remaining == 0:
            return "berakhir bulan ini"
        if months_remaining <= 3:
            return "1-3 bulan lagi"
        return "lebih dari 3 bulan"

    expiry["kategori"] = expiry["months_remaining"].map(categorize)
    order = ["expired", "berakhir bulan ini", "1-3 bulan lagi", "lebih dari 3 bulan"]
    print("Ringkasan:")
    counts = expiry["kategori"].value_counts()
    for label in order:
        if label in counts:
            print(f"  {label:<22} {counts[label]:>4}")
    print()
    print("Urut dari yang paling dekat berakhir:")
    print(
        expiry.sort_values("end_month")[
            ["partner_name", "signed_month_raw", "end_month_raw", "months_remaining",
             "kategori", "has_loa"]
        ].to_string(index=False)
    )

    # ------------------------------------------------------------------
    section("12. df_conversion (diambil dari sheet, bukan dihitung)")
    pivot = df_conversion.pivot_table(
        index="scope", columns="stage", values="conversion_rate", observed=True
    )
    ordered_stages = list(preparation.FUNNEL_STAGES)
    pivot = pivot.reindex(columns=ordered_stages)
    print(pivot.to_string())
    print()
    print(f"Tahap yang dipakai sebagai KPI: {preparation.CONVERSION_STAGE!r}")
    kpi = df_conversion[df_conversion["stage"] == preparation.CONVERSION_STAGE]
    print()
    print(f"  {'periode':<14}{'conversion rate':>18}")
    line()
    for _, row in kpi.iterrows():
        rate = row["conversion_rate"]
        shown = "-" if pd.isna(rate) else f"{rate:.2f}%"
        print(f"  {row['scope']:<14}{shown:>18}")

    print()
    line("#")
    print("df_partner & df_conversion SIAP DIVALIDASI.")
    line("#")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
