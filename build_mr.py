"""
build_mr.py - PHASE 3: BUILD & VALIDATE df_mr

Membangun df_mr dari 3 tab Market Research, lalu MEMVALIDASI hasilnya
supaya bisa dicek silang dengan ESSM sebelum dipakai menghitung KPI.

Yang dilaporkan:
    1. Jumlah record per tab sumber
    2. Bentuk df_mr (kolom & tipe data) + contoh baris
    3. Total MR menurut definisi BD (kemunungan nama PIC)
    4. MR per PIC
    5. MR per bulan
    6. MR per bulan x tab
    7. Kualitas data: PIC kosong, tanggal kosong/gagal parse
    8. Duplikat dalam tab yang sama (dilaporkan, TIDAK dihapus)
    9. Sel PIC yang memuat lebih dari satu nama
   10. Cek silang: apakah february & march benar sudah keluar

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_mr.py
"""

from __future__ import annotations

import sys

import pandas as pd

from src import preparation

pd.set_option("display.width", 120)
pd.set_option("display.max_columns", 20)


def line(char: str = "-", width: int = 74) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def total_mr(df: pd.DataFrame) -> int:
    """Total MR = jumlah kemunculan nama PIC (definisi BD).

    Baris tanpa PIC tidak dihitung, karena tidak ada nama yang muncul.
    """
    return int(df["pic_aiesec"].notna().sum())


def main() -> int:
    print()
    line("#")
    print("PHASE 3 - BUILD & VALIDATE df_mr")
    line("#")

    print()
    print("Aturan yang dipakai (dikonfirmasi BD di Phase 2):")
    print("  month     : dari baris penanda bulan, BUKAN dari DATE OF MR")
    print("  record    : kolom NO berisi angka DAN nama partner terisi")
    print(f"  dibuang   : bulan {list(preparation.EXCLUDED_MONTHS)}")
    print("  dedupe    : TIDAK, karena ELDs/EWAs/SS adalah 3 tim berbeda")
    print("  Total MR  : jumlah kemunculan nama PIC")

    try:
        df = preparation.build_df_mr()
    except Exception as exc:
        print(f"\nGagal membangun df_mr: {type(exc).__name__}: {exc}")
        return 1

    if df.empty:
        print("\ndf_mr kosong. Periksa aturan ekstraksi.")
        return 1

    # ------------------------------------------------------------------
    section("1. JUMLAH RECORD PER TAB SUMBER")
    per_tab = df.groupby("source_tab", observed=True).size()
    for tab, count in per_tab.items():
        print(f"  {tab:<28} {count:>6}")
    line()
    print(f"  {'TOTAL baris df_mr':<28} {len(df):>6}")

    # ------------------------------------------------------------------
    section("2. BENTUK df_mr")
    print(f"Shape : {df.shape[0]} baris x {df.shape[1]} kolom")
    print()
    print("Kolom dan tipe data:")
    for column, dtype in df.dtypes.items():
        print(f"  {column:<16} {dtype}")
    print()
    print("5 baris pertama:")
    print(df.head().to_string(index=False))

    # ------------------------------------------------------------------
    section("3. TOTAL MARKET RESEARCH")
    total = total_mr(df)
    print(f"Total baris record di df_mr        : {len(df)}")
    print(f"Baris yang PIC-nya terisi          : {total}")
    print(f"Baris tanpa PIC (tidak dihitung)   : {len(df) - total}")
    print()
    print(f"TOTAL MR = {total}")

    # ------------------------------------------------------------------
    section("4. MR PER PIC (urut terbanyak)")
    by_pic = df["pic_aiesec"].value_counts(dropna=True)
    print(f"Jumlah PIC unik : {by_pic.size}")
    print()
    print(f"  {'PIC':<24} {'MR':>6}")
    line()
    for pic, count in by_pic.items():
        print(f"  {pic:<24} {count:>6}")
    line()
    print(f"  {'TOTAL':<24} {by_pic.sum():>6}")

    # ------------------------------------------------------------------
    section("5. MR PER BULAN")
    by_month = (
        df[df["pic_aiesec"].notna()]
        .groupby("month", observed=False)
        .size()
    )
    print(f"  {'bulan':<14} {'MR':>6}")
    line()
    for month, count in by_month.items():
        print(f"  {str(month):<14} {count:>6}")
    line()
    print(f"  {'TOTAL':<14} {by_month.sum():>6}")

    # ------------------------------------------------------------------
    section("6. MR PER BULAN x TAB")
    pivot = pd.crosstab(
        df[df["pic_aiesec"].notna()]["month"],
        df[df["pic_aiesec"].notna()]["source_tab"],
        margins=True,
        margins_name="TOTAL",
    )
    print(pivot.to_string())

    # ------------------------------------------------------------------
    section("7. KUALITAS DATA")
    missing_pic = df[df["pic_aiesec"].isna()]
    print(f"Record tanpa PIC                       : {len(missing_pic)}")
    if not missing_pic.empty:
        print("  contoh (maks 8):")
        print(
            missing_pic[["month", "partner_name", "source_tab", "source_row"]]
            .head(8)
            .to_string(index=False)
        )

    print()
    no_raw_date = df["date_of_mr_raw"].isna()
    failed_parse = df["date_of_mr"].isna() & ~no_raw_date
    print(f"Record tanpa DATE OF MR di sumber      : {int(no_raw_date.sum())}")
    print(f"Record punya tanggal tapi gagal parse  : {int(failed_parse.sum())}")
    print(f"Record dengan tanggal valid            : {int(df['date_of_mr'].notna().sum())}")
    if failed_parse.any():
        print("  nilai yang gagal diparse (maks 10):")
        print(df.loc[failed_parse, "date_of_mr_raw"].value_counts().head(10).to_string())
    print()
    print("Catatan: tanggal TIDAK dipakai untuk menentukan bulan,")
    print("jadi tanggal yang kosong tidak membuat record hilang.")

    # ------------------------------------------------------------------
    section("8. DUPLIKAT DALAM TAB YANG SAMA (tidak dihapus)")
    print("Definisi: bulan + nama partner + PIC + tab yang sama, muncul >1 baris.")
    print("Artinya: satu orang tercatat me-MR partner yang sama, di bulan yang")
    print("sama, di tab yang sama, lebih dari sekali.")
    print()
    duplicates = preparation.find_duplicate_records(df)
    print(f"Baris yang terlibat duplikat : {len(duplicates)}")
    if not duplicates.empty:
        groups = duplicates.groupby(
            ["month", "partner_name", "pic_aiesec", "source_tab"], observed=True
        ).size()
        print(f"Kelompok duplikat            : {groups.size}")
        print(f"Kelebihan baris              : {len(duplicates) - groups.size}")
        print()
        print("Contoh kelompok terbesar (maks 5):")
        for key, count in groups.sort_values(ascending=False).head(5).items():
            month, partner, pic, tab = key
            print(f"  {count}x | {month} | {partner} | PIC={pic} | {tab}")
            rows = duplicates[
                (duplicates["month"] == month)
                & (duplicates["partner_name"] == partner)
                & (duplicates["pic_aiesec"] == pic)
                & (duplicates["source_tab"] == tab)
            ]["source_row"].tolist()
            print(f"       baris di sheet: {rows}")

    # ------------------------------------------------------------------
    section("9. SEL PIC YANG MEMUAT LEBIH DARI SATU NAMA")
    print("Kalau satu sel berisi dua nama, satu baris seharusnya = dua MR.")
    print()
    multi = preparation.find_multi_pic_cells(df)
    print(f"Jumlah sel seperti itu : {len(multi)}")
    if not multi.empty:
        print(multi["pic_aiesec"].value_counts().head(10).to_string())

    # ------------------------------------------------------------------
    section("10. CEK SILANG BULAN")
    months_present = sorted(
        {str(m) for m in df["month"].dropna().unique()},
        key=lambda m: preparation.TERM_MONTH_ORDER.index(m),
    )
    print(f"Bulan yang ada di df_mr : {months_present}")
    leaked = [m for m in preparation.EXCLUDED_MONTHS if m in months_present]
    print(f"Bulan yang harus dibuang bocor masuk? : {leaked if leaked else 'TIDAK'}")
    print(f"Record dengan bulan kosong (NaN)      : {int(df['month'].isna().sum())}")

    print()
    line("#")
    print("df_mr SIAP DIVALIDASI. Bandingkan angka di atas dengan ESSM.")
    line("#")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
