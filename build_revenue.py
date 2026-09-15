"""
build_revenue.py - PHASE 6 & 7: BUILD & VALIDATE df_financial + df_inkind

National_1.3 (Financial Revenue) dan National_1.4 (In-Kind Value)
strukturnya identik, jadi keduanya dibangun dengan parser yang sama.

Financial Revenue dan In-Kind Value TIDAK PERNAH dijumlahkan menjadi
satu angka. Keduanya dilaporkan terpisah.

Yang dilaporkan untuk masing-masing:
    1. Bentuk dataframe + seluruh record (datanya sedikit, jadi ditampilkan penuh)
    2. Kualitas tanggal (format campur di sumber)
    3. Total menurut hitungan kita
    4. CEK SILANG terhadap total yang sudah dihitung sheet
    5. Rekap per bulan
    6. Rekap per partner

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_revenue.py
"""

from __future__ import annotations

import sys

import pandas as pd

from src import preparation, sheets

pd.set_option("display.width", 170)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 40)


def line(char: str = "-", width: int = 82) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def rupiah(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"Rp{value:,.0f}"


def report_dataset(
    title: str,
    df: pd.DataFrame,
    amount_column: str,
    grid: list[list[str]],
    labels: dict[str, str],
    extra_columns: list[str],
) -> float:
    """Laporkan satu dataset revenue. Return total hasil hitungan kita."""
    section(title)

    if df.empty:
        print("Dataset kosong.")
        return 0.0

    # -- 1. bentuk & isi -------------------------------------------------
    print(f"Shape : {df.shape[0]} baris x {df.shape[1]} kolom")
    print()
    print("Kolom dan tipe data:")
    for column, dtype in df.dtypes.items():
        print(f"  {column:<20} {dtype}")

    print()
    print("SELURUH RECORD:")
    display_columns = (
        ["month", "quarter", "partner_name"]
        + extra_columns
        + ["date_received_raw", "date_received", amount_column, "source_row"]
    )
    shown = df[display_columns].copy()
    if "description" in shown.columns:
        # Deskripsi in-kind bisa ratusan karakter; dipotong agar tabel terbaca.
        shown["description"] = shown["description"].str.slice(0, 44) + "..."
    print(shown.to_string(index=False))

    # -- 2. kualitas tanggal ---------------------------------------------
    print()
    print("KUALITAS TANGGAL (format di sumber tidak konsisten):")
    print(f"  format dominan terdeteksi di tab ini : {df.attrs.get('dominant_date_format', '?')}")
    print()
    for note, count in df["date_note"].value_counts().items():
        print(f"  {note:<48} {count:>3}")

    mismatch = df[
        df["date_received"].notna()
        & (
            df["date_received"].dt.strftime("%B").str.lower()
            != df["month"].astype(str).str.lower()
        )
    ]
    print()
    print(f"Record yang bulan Date Received BEDA dari kolom MONTHS : {len(mismatch)}")
    if not mismatch.empty:
        print(
            mismatch[["partner_name", "month", "date_received_raw", "date_received"]]
            .to_string(index=False)
        )

    # -- 3 & 4. total dan cek silang -------------------------------------
    our_total = float(df[amount_column].sum())
    sheet_totals = preparation.read_sheet_totals(grid, labels)

    print()
    print("TOTAL:")
    print(f"  hitungan kita dari {len(df)} record : {rupiah(our_total)}")
    print(f"  angka TOTAL di sheet (baris 3)      : {rupiah(sheet_totals.get('TOTAL'))}")
    difference = our_total - sheet_totals.get("TOTAL", float("nan"))
    status = "COCOK" if abs(difference) < 0.01 else f"BEDA {rupiah(abs(difference))}"
    print(f"  hasil cek silang                    : {status}")

    print()
    print("CEK SILANG PER KUARTAL:")
    print(f"  {'kuartal':<12}{'hitungan kita':>20}{'total di sheet':>20}{'status':>10}")
    line()
    by_quarter = df.groupby("quarter", observed=True)[amount_column].sum()
    for quarter in ["QUARTER #1", "QUARTER #2", "QUARTER #3", "QUARTER #4"]:
        ours = float(by_quarter.get(quarter, 0.0))
        theirs = sheet_totals.get(quarter, float("nan"))
        ok = "COCOK" if not pd.isna(theirs) and abs(ours - theirs) < 0.01 else "BEDA"
        print(f"  {quarter:<12}{rupiah(ours):>20}{rupiah(theirs):>20}{ok:>10}")

    # -- 5. per bulan -----------------------------------------------------
    print()
    print("PER BULAN:")
    monthly = df.groupby("month", observed=True)[amount_column].agg(["count", "sum"])
    print(f"  {'bulan':<14}{'record':>8}{'nilai':>20}")
    line()
    for month, row in monthly.iterrows():
        print(f"  {str(month):<14}{int(row['count']):>8}{rupiah(row['sum']):>20}")
    line()
    print(f"  {'TOTAL':<14}{len(df):>8}{rupiah(our_total):>20}")

    # -- 6. per partner ---------------------------------------------------
    print()
    print("PER PARTNER:")
    by_partner = (
        df.groupby("partner_name", observed=True)[amount_column]
        .agg(["count", "sum"])
        .sort_values("sum", ascending=False)
    )
    print(f"  {'partner':<32}{'record':>8}{'nilai':>20}")
    line()
    for partner, row in by_partner.iterrows():
        print(f"  {partner:<32}{int(row['count']):>8}{rupiah(row['sum']):>20}")

    # -- kualitas nilai ---------------------------------------------------
    failed = df[df[amount_column].isna()]
    print()
    print(f"Nilai uang gagal diurai : {len(failed)}")
    if not failed.empty:
        print(failed[["partner_name", "amount_raw"]].to_string(index=False))

    return our_total


def main() -> int:
    print()
    line("#")
    print("PHASE 6 & 7 - BUILD & VALIDATE df_financial + df_inkind")
    line("#")
    print()
    print("Aturan yang dipakai:")
    print("  bulan record  : dari kolom MONTHS (selalu terisi)")
    print("  Date Received : format campur M/D/Y & D/M/Y, diurai + dicatat")
    print("  nilai uang    : 'Rp26,750,000.00' -> 26750000.0")
    print("  Financial dan In-Kind TIDAK PERNAH dijumlahkan jadi satu")

    try:
        financial_grid = preparation.load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
        inkind_grid = preparation.load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
        df_financial = preparation.build_df_financial(financial_grid)
        df_inkind = preparation.build_df_inkind(inkind_grid)
    except Exception as exc:
        print(f"\nGagal membangun data revenue: {type(exc).__name__}: {exc}")
        return 1

    financial_total = report_dataset(
        "PHASE 6 - df_financial (National_1.3, FINANCIAL REVENUE)",
        df_financial,
        "revenue",
        financial_grid,
        preparation.FINANCIAL_LABELS,
        extra_columns=["proof"],
    )

    inkind_total = report_dataset(
        "PHASE 7 - df_inkind (National_1.4, IN-KIND VALUE)",
        df_inkind,
        "inkind_value",
        inkind_grid,
        preparation.INKIND_LABELS,
        extra_columns=["type", "description"],
    )

    section("RINGKASAN DUA KPI TERPISAH")
    print("Sesuai aturan bisnis, kedua angka ini TIDAK boleh dijumlahkan.")
    print()
    print(f"  FINANCIAL REVENUE : {rupiah(financial_total)}")
    print(f"  IN-KIND VALUE     : {rupiah(inkind_total)}")
    print()
    print("  (Financial = uang tunai yang benar-benar diterima.")
    print("   In-Kind    = taksiran nilai dukungan non-tunai.)")

    print()
    line("#")
    print("df_financial & df_inkind SIAP DIVALIDASI.")
    line("#")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
