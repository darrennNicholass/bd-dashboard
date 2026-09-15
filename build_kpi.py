"""
build_kpi.py - PHASE 8: BUILD & VALIDATE KPI LAYER

Menjalankan seluruh fungsi di src/metrics.py memakai data live, lalu
MENCOCOKKAN angka utamanya dengan hasil validasi Phase 3-7. Kalau ada
angka yang bergeser, script ini menandainya sebagai GAGAL - jadi kesalahan
di layer KPI tidak lolos diam-diam ke dashboard.

Yang dilaporkan:
    1. Headline KPI (6 angka)
    2. CEK SILANG terhadap angka yang sudah divalidasi BD
    3. MR per PIC & per bulan
    4. Sales funnel conversion rate (seluruh periode + per bulan)
    5. Financial Revenue: per bulan & per partner
    6. In-Kind Value: per bulan & per partner
    7. Distribusi stakeholder
    8. Document Tracker (hanya partner aktif)
    9. Partnership Expiry Tracker
   10. Uji perilaku: dataframe kosong & reference_date lain

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_kpi.py
"""

from __future__ import annotations

import sys

import pandas as pd

from src import metrics, preparation, sheets

pd.set_option("display.width", 170)
pd.set_option("display.max_columns", 30)

# Angka acuan hasil validasi tim BD di Phase 3-7. JANGAN diubah kecuali
# tim BD memang mengubah definisinya.
BASELINE = {
    "total_mr": 1694,
    "partner_count": 35,
    "active_partners": 16,
    "conversion_rate": 82.86,
    "financial_revenue": 26_750_000.0,
    "inkind_value": 119_685_000.0,
}


def line(char: str = "-", width: int = 82) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


def rupiah(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"Rp{value:,.0f}"


def percent(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.2f}%"


def tick(flag: bool) -> str:
    return "ADA" if flag else "-"


def money_table(df: pd.DataFrame) -> str:
    """Tabel rekap uang dengan kolom amount diformat rupiah."""
    shown = df.copy()
    shown["amount"] = shown["amount"].map(rupiah)
    return shown.to_string(index=False)


def main() -> int:
    print()
    line("#")
    print("PHASE 8 - BUILD & VALIDATE KPI LAYER (src/metrics.py)")
    line("#")

    reference_date = pd.Timestamp.today().normalize()
    print()
    print("Aturan yang dipakai:")
    print("  Total MR         : jumlah kemunculan nama PIC")
    print("  Active Partner   : ada LoA DAN Month End >= bulan berjalan")
    print("  Conversion rate  : diambil dari sheet, tahap "
          f"{preparation.CONVERSION_STAGE!r}")
    print("  Financial & In-Kind: DUA angka terpisah, tidak pernah dijumlahkan")
    print(f"  tanggal acuan    : {reference_date.date()} "
          f"(bulan {reference_date.to_period('M')})")

    # ------------------------------------------------------------------
    print()
    print("Menarik data dari Active ESSM & National Mirror ...")
    try:
        df_mr = preparation.build_df_mr()
        partner_grid = preparation.load_partner_grid()
        df_partner = preparation.build_df_partner(
            partner_grid, reference_date=reference_date
        )
        df_conversion = preparation.build_df_conversion(partner_grid)
        df_financial = preparation.build_df_financial(
            preparation.load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
        )
        df_inkind = preparation.build_df_inkind(
            preparation.load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
        )
    except Exception as exc:
        print(f"\nGagal membangun dataframe: {type(exc).__name__}: {exc}")
        return 1

    print(f"  df_mr        : {df_mr.shape[0]} baris")
    print(f"  df_partner   : {df_partner.shape[0]} baris")
    print(f"  df_conversion: {df_conversion.shape[0]} baris")
    print(f"  df_financial : {df_financial.shape[0]} baris")
    print(f"  df_inkind    : {df_inkind.shape[0]} baris")

    # ------------------------------------------------------------------
    section("1. HEADLINE KPI")
    summary = metrics.get_kpi_summary(
        df_mr, df_partner, df_conversion, df_financial, df_inkind
    )
    print(f"  {'Total Market Research':<28}{summary['total_mr']:>22}")
    print(f"  {'Partner di portofolio':<28}{summary['partner_count']:>22}")
    print(f"  {'Active Partners':<28}{summary['active_partners']:>22}")
    print(f"  {'Conversion Rate':<28}{percent(summary['conversion_rate']):>22}")
    print(f"  {'Financial Revenue':<28}{rupiah(summary['financial_revenue']):>22}")
    print(f"  {'In-Kind Value':<28}{rupiah(summary['inkind_value']):>22}")
    print()
    print("  Financial Revenue dan In-Kind Value sengaja tidak dijumlahkan.")
    print(f"  Kunci 'total_revenue' ada di summary? "
          f"{'YA (SALAH)' if 'total_revenue' in summary else 'TIDAK (benar)'}")

    # ------------------------------------------------------------------
    section("2. CEK SILANG DENGAN ANGKA YANG SUDAH DIVALIDASI BD")
    print(f"  {'KPI':<22}{'metrics.py':>20}{'acuan Phase 3-7':>20}{'status':>10}")
    line()
    failures: list[str] = []
    for key, expected in BASELINE.items():
        actual = summary[key]
        ok = (
            not pd.isna(actual)
            and abs(float(actual) - float(expected)) < 0.01
        )
        if not ok:
            failures.append(key)
        if key in ("financial_revenue", "inkind_value"):
            shown, reference = rupiah(actual), rupiah(expected)
        elif key == "conversion_rate":
            shown, reference = percent(actual), percent(expected)
        else:
            shown, reference = str(actual), str(expected)
        print(f"  {key:<22}{shown:>20}{reference:>20}"
              f"{('COCOK' if ok else 'GAGAL'):>10}")

    print()
    # Total MR harus sama dengan jumlah kolom MR per PIC: kalau beda,
    # ada nama PIC yang hilang saat agregasi.
    by_pic = metrics.get_mr_by_pic(df_mr)
    pic_sum = int(by_pic["total_mr"].sum())
    month_sum = int(metrics.get_mr_by_month(df_mr)["total_mr"].sum())
    print("Konsistensi agregasi (harus sama dengan Total MR):")
    print(f"  jumlah MR per PIC    : {pic_sum}")
    print(f"  jumlah MR per bulan  : {month_sum}")
    consistent = pic_sum == month_sum == summary["total_mr"]
    print(f"  status               : {'COCOK' if consistent else 'GAGAL'}")
    if not consistent:
        failures.append("konsistensi agregasi MR")

    # Total revenue per bulan & per partner harus sama dengan headline.
    for label, df, column, headline in (
        ("financial", df_financial, "revenue", summary["financial_revenue"]),
        ("in-kind", df_inkind, "inkind_value", summary["inkind_value"]),
    ):
        monthly = float(metrics.get_revenue_by_month(df, column)["amount"].sum())
        by_partner = float(metrics.get_revenue_by_partner(df, column)["amount"].sum())
        ok = abs(monthly - headline) < 0.01 and abs(by_partner - headline) < 0.01
        print()
        print(f"Konsistensi agregasi {label}:")
        print(f"  per bulan   : {rupiah(monthly)}")
        print(f"  per partner : {rupiah(by_partner)}")
        print(f"  status      : {'COCOK' if ok else 'GAGAL'}")
        if not ok:
            failures.append(f"konsistensi agregasi {label}")

    # ------------------------------------------------------------------
    section("3. MARKET RESEARCH")
    print(f"MR per PIC ({len(by_pic)} PIC unik):")
    print(by_pic.to_string(index=False))
    print()
    print("MR per bulan:")
    print(metrics.get_mr_by_month(df_mr).to_string(index=False))

    # ------------------------------------------------------------------
    section("4. SALES FUNNEL CONVERSION RATE (diambil dari sheet)")
    funnel = metrics.get_conversion_funnel(df_conversion)
    print("Seluruh periode:")
    print(f"  {'tahap':<32}{'conversion rate':>18}")
    line()
    for _, row in funnel.iterrows():
        print(f"  {str(row['stage']):<32}{percent(row['conversion_rate']):>18}")
    print()
    print(f"KPI utama (tahap {preparation.CONVERSION_STAGE!r}) : "
          f"{percent(metrics.get_conversion_rate(df_conversion))}")
    print()
    print("Per bulan:")
    monthly_conversion = metrics.get_conversion_by_month(df_conversion)
    print(f"  {'bulan':<14}{'conversion rate':>18}")
    line()
    for _, row in monthly_conversion.iterrows():
        print(f"  {str(row['month']):<14}{percent(row['conversion_rate']):>18}")

    # ------------------------------------------------------------------
    section("5. FINANCIAL REVENUE (National_1.3)")
    print("Per bulan:")
    financial_monthly = metrics.get_revenue_by_month(df_financial, "revenue")
    print(f"  {'bulan':<14}{'record':>8}{'nilai':>20}")
    line()
    for _, row in financial_monthly.iterrows():
        print(f"  {str(row['month']):<14}{int(row['records']):>8}"
              f"{rupiah(row['amount']):>20}")
    print()
    print("Per partner:")
    print(money_table(metrics.get_revenue_by_partner(df_financial, "revenue")))

    # ------------------------------------------------------------------
    section("6. IN-KIND VALUE (National_1.4)")
    print("Per bulan:")
    inkind_monthly = metrics.get_revenue_by_month(df_inkind, "inkind_value")
    print(f"  {'bulan':<14}{'record':>8}{'nilai':>20}")
    line()
    for _, row in inkind_monthly.iterrows():
        print(f"  {str(row['month']):<14}{int(row['records']):>8}"
              f"{rupiah(row['amount']):>20}")
    print()
    print("Per partner:")
    print(money_table(metrics.get_revenue_by_partner(df_inkind, "inkind_value")))

    # ------------------------------------------------------------------
    section("7. DISTRIBUSI STAKEHOLDER")
    print("Seluruh partner:")
    print(metrics.get_stakeholder_distribution(df_partner).to_string(index=False))
    print()
    print("Hanya partner aktif:")
    print(
        metrics.get_stakeholder_distribution(df_partner, active_only=True)
        .to_string(index=False)
    )

    # ------------------------------------------------------------------
    section("8. DOCUMENT TRACKER (hanya partner aktif)")
    tracker = metrics.get_document_tracker(df_partner)
    shown = tracker.copy()
    for document in preparation.DOCUMENT_FIELDS:
        shown[document.upper()] = shown[f"has_{document}"].map(tick)
    print(
        shown[
            ["partner_name", "PROPOSAL", "MOM", "LOA", "INVOICE", "documents_missing"]
        ].to_string(index=False)
    )
    print()
    print(f"Partner aktif dengan dokumen lengkap : "
          f"{int(tracker['documents_complete'].sum())} dari {len(tracker)}")
    print()
    print("Rekap per jenis dokumen:")
    print(metrics.get_document_completeness(df_partner).to_string(index=False))

    # ------------------------------------------------------------------
    section("9. PARTNERSHIP EXPIRY TRACKER")
    print("Ringkasan (seluruh partner):")
    print(metrics.get_expiry_summary(df_partner, reference_date=reference_date)
          .to_string(index=False))
    print()
    print("Ringkasan (hanya partner aktif):")
    print(
        metrics.get_expiry_summary(
            df_partner, reference_date=reference_date, active_only=True
        ).to_string(index=False)
    )
    print()
    print("Urut dari yang paling dekat berakhir:")
    expiry = metrics.get_partnership_expiry(df_partner, reference_date=reference_date)
    print(
        expiry[
            ["partner_name", "signed_month_raw", "end_month_raw",
             "months_remaining", "expiry_category", "has_loa", "is_active"]
        ].to_string(index=False)
    )

    # ------------------------------------------------------------------
    section("10. UJI PERILAKU LAYER KPI")
    empty = pd.DataFrame()
    checks = [
        ("get_total_mr(kosong) = 0", metrics.get_total_mr(empty) == 0),
        ("get_active_partner_count(kosong) = 0",
         metrics.get_active_partner_count(empty) == 0),
        ("get_financial_revenue(kosong) = 0.0",
         metrics.get_financial_revenue(empty) == 0.0),
        ("get_mr_by_pic(kosong) -> dataframe kosong",
         metrics.get_mr_by_pic(empty).empty),
        ("get_document_tracker(kosong) -> dataframe kosong",
         metrics.get_document_tracker(empty).empty),
        ("get_expiry_summary(kosong) -> semua kategori 0",
         int(metrics.get_expiry_summary(empty)["partner_count"].sum()) == 0),
        ("scope tidak ada -> NaN, bukan 0",
         pd.isna(metrics.get_conversion_rate(df_conversion, scope="tidak-ada"))),
        ("kolom kurang -> KeyError", _raises_key_error(df_mr)),
    ]

    # reference_date berbeda harus menggeser kategori expiry.
    future = metrics.get_expiry_summary(
        df_partner, reference_date=reference_date + pd.DateOffset(months=12)
    )
    now = metrics.get_expiry_summary(df_partner, reference_date=reference_date)
    expired_now = int(now.loc[now["expiry_category"] == metrics.EXPIRY_EXPIRED,
                              "partner_count"].iloc[0])
    expired_future = int(future.loc[future["expiry_category"] == metrics.EXPIRY_EXPIRED,
                                    "partner_count"].iloc[0])
    checks.append(
        (f"reference_date +12 bulan menaikkan 'expired' ({expired_now} -> "
         f"{expired_future})", expired_future > expired_now)
    )

    for label, ok in checks:
        print(f"  [{'OK' if ok else 'GAGAL':<5}] {label}")
        if not ok:
            failures.append(label)

    # ------------------------------------------------------------------
    print()
    line("#")
    if failures:
        print(f"PHASE 8 GAGAL. {len(failures)} pemeriksaan tidak lolos:")
        for item in failures:
            print(f"  - {item}")
        line("#")
        print()
        return 1

    print("PHASE 8 SELESAI. Semua KPI cocok dengan angka validasi Phase 3-7.")
    line("#")
    print()
    return 0


def _raises_key_error(df_mr: pd.DataFrame) -> bool:
    """Dataframe dengan kolom salah harus ditolak, bukan menghasilkan angka."""
    broken = df_mr.rename(columns={"pic_aiesec": "pic"})
    try:
        metrics.get_total_mr(broken)
    except KeyError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
