"""
build_period.py - PHASE 9: BUILD & VALIDATE PERIOD FILTER

Memastikan filter periode di src/periods.py tidak mengubah arti KPI mana pun.

Yang diuji:
    1. Pilihan periode yang tersedia (bulan & kuartal)
    2. Seluruh periode harus mereproduksi enam angka baseline Phase 3-8
    3. Additivitas metrik ALIRAN: jumlah 12 bulan = total seluruh periode
    4. Kuartal: hasil filter bulan = kolom `quarter` di National_1.3/1.4
    5. Conversion rate: per bulan cocok dengan sheet; gabungan bulan -> NaN
    6. Metrik POSISI: tanggal acuan bergeser mengikuti akhir periode,
       dibatasi tidak melewati hari ini
    7. Perilaku tepi: bulan tanpa data, February/March, nama bulan ngawur,
       dataframe kosong

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_period.py
"""

from __future__ import annotations

import sys

import pandas as pd

from src import metrics, periods, preparation, sheets

pd.set_option("display.width", 170)
pd.set_option("display.max_columns", 30)

# Angka acuan hasil validasi Phase 3-8 untuk pilihan "seluruh periode".
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


def main() -> int:
    print()
    line("#")
    print("PHASE 9 - BUILD & VALIDATE PERIOD FILTER (src/periods.py)")
    line("#")

    today = pd.Timestamp.today().normalize()
    print()
    print("Aturan yang dipakai:")
    print("  satuan filter   : BULAN (sumber tidak punya level harian)")
    print("  metrik aliran   : baris di luar periode DIBUANG (MR, revenue, in-kind)")
    print("  metrik posisi   : baris TIDAK dibuang; tanggal acuan digeser")
    print("  tanggal acuan   : akhir bulan terakhir periode, maksimal hari ini")
    print("  conversion rate : dari sheet; gabungan beberapa bulan -> NaN")
    print(f"  hari ini        : {today.date()}")

    print()
    print("Menarik data dari Active ESSM & National Mirror ...")
    try:
        df_mr = preparation.build_df_mr()
        partner_grid = preparation.load_partner_grid()
        df_partner = preparation.build_df_partner(partner_grid, reference_date=today)
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

    frames = {
        "df_mr": df_mr,
        "df_partner": df_partner,
        "df_conversion": df_conversion,
        "df_financial": df_financial,
        "df_inkind": df_inkind,
    }
    for name, df in frames.items():
        print(f"  {name:<14}: {df.shape[0]} baris")

    def select(selection):
        """Terapkan periode ke seluruh dataframe sekaligus."""
        return periods.apply_period(
            selection,
            df_mr=df_mr,
            df_partner=df_partner,
            df_conversion=df_conversion,
            df_financial=df_financial,
            df_inkind=df_inkind,
            today=today,
        )

    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        print(f"  [{'OK' if ok else 'GAGAL':<5}] {label}")
        if not ok:
            failures.append(label)

    # ------------------------------------------------------------------
    section("1. PILIHAN PERIODE YANG TERSEDIA")
    print(f"Bulan masa jabatan ({len(periods.TERM_MONTHS)}):")
    print(f"  {', '.join(periods.TERM_MONTHS)}")
    print()
    print("Kuartal:")
    for quarter, months in periods.QUARTER_MONTHS.items():
        print(f"  {quarter:<12} {', '.join(months)}")
    print()
    print("Bulan yang benar-benar punya data:")
    value_columns = {
        "df_mr": "pic_aiesec",
        "df_partner": None,
        "df_financial": "revenue",
        "df_inkind": "inkind_value",
    }
    for name, df in frames.items():
        if name == "df_conversion":
            continue
        available = periods.available_months(df, value_column=value_columns[name])
        print(f"  {name:<14}: {', '.join(available) if available else '(tidak ada)'}")
    print()
    print("Bulan yang punya BARIS di df_mr tapi tanpa satu pun nama PIC")
    print("(baris seperti ini tidak menambah Total MR):")
    rows_only = [
        month
        for month in periods.available_months(df_mr)
        if month not in periods.available_months(df_mr, value_column="pic_aiesec")
    ]
    print(f"  {', '.join(rows_only) if rows_only else '(tidak ada)'}")
    print()
    print("Label periode:")
    for selection in ("all", "august", ("april", "may"), "QUARTER #2",
                      ("april", "june")):
        print(f"  {str(selection):<24} -> {periods.period_label(selection)}")

    # ------------------------------------------------------------------
    section("2. SELURUH PERIODE HARUS SAMA DENGAN BASELINE PHASE 3-8")
    everything = select("all")
    summary = metrics.get_kpi_summary(
        everything.df_mr,
        everything.df_partner,
        everything.df_conversion,
        everything.df_financial,
        everything.df_inkind,
        scope=everything.conversion_scope or metrics.SCOPE_ALL,
    )
    print(f"  {'KPI':<22}{'filter periode':>20}{'baseline':>20}{'status':>10}")
    line()
    for key, expected in BASELINE.items():
        actual = summary[key]
        ok = not pd.isna(actual) and abs(float(actual) - float(expected)) < 0.01
        if key in ("financial_revenue", "inkind_value"):
            shown, reference = rupiah(actual), rupiah(expected)
        elif key == "conversion_rate":
            shown, reference = percent(actual), percent(expected)
        else:
            shown, reference = str(actual), str(expected)
        print(f"  {key:<22}{shown:>20}{reference:>20}"
              f"{('COCOK' if ok else 'GAGAL'):>10}")
        if not ok:
            failures.append(f"baseline {key}")
    print()
    print(f"  label            : {everything.label}")
    print(f"  scope conversion : {everything.conversion_scope}")
    print(f"  tanggal acuan    : {everything.reference_date.date()} "
          f"(= hari ini: {everything.reference_date == today})")

    # ------------------------------------------------------------------
    section("3. ADDITIVITAS METRIK ALIRAN (jumlah 12 bulan = total)")
    print(f"  {'bulan':<12}{'total MR':>10}{'partner baru':>14}"
          f"{'financial':>18}{'in-kind':>18}")
    line()
    totals = {"mr": 0, "partner": 0, "financial": 0.0, "inkind": 0.0}
    for month in periods.TERM_MONTHS:
        selected = select(month)
        month_mr = metrics.get_total_mr(selected.df_mr)
        month_partner = len(selected.df_partner_new)
        month_financial = metrics.get_financial_revenue(selected.df_financial)
        month_inkind = metrics.get_inkind_value(selected.df_inkind)
        totals["mr"] += month_mr
        totals["partner"] += month_partner
        totals["financial"] += month_financial
        totals["inkind"] += month_inkind
        print(f"  {month:<12}{month_mr:>10}{month_partner:>14}"
              f"{rupiah(month_financial):>18}{rupiah(month_inkind):>18}")
    line()
    print(f"  {'JUMLAH':<12}{totals['mr']:>10}{totals['partner']:>14}"
          f"{rupiah(totals['financial']):>18}{rupiah(totals['inkind']):>18}")
    print(f"  {'BASELINE':<12}{BASELINE['total_mr']:>10}"
          f"{BASELINE['partner_count']:>14}"
          f"{rupiah(BASELINE['financial_revenue']):>18}"
          f"{rupiah(BASELINE['inkind_value']):>18}")
    print()
    check("jumlah MR 12 bulan = Total MR", totals["mr"] == BASELINE["total_mr"])
    check("jumlah partner baru 12 bulan = jumlah partner",
          totals["partner"] == BASELINE["partner_count"])
    check("jumlah financial 12 bulan = total financial",
          abs(totals["financial"] - BASELINE["financial_revenue"]) < 0.01)
    check("jumlah in-kind 12 bulan = total in-kind",
          abs(totals["inkind"] - BASELINE["inkind_value"]) < 0.01)

    # ------------------------------------------------------------------
    section("4. KUARTAL: FILTER BULAN vs KOLOM `quarter` DI SHEET")
    print(f"  {'kuartal':<12}{'dataset':<12}{'filter bulan':>18}"
          f"{'kolom quarter':>18}{'status':>10}")
    line()
    for quarter in periods.QUARTER_MONTHS:
        for label, df, column in (
            ("financial", df_financial, "revenue"),
            ("in-kind", df_inkind, "inkind_value"),
        ):
            selected = periods.filter_by_months(df, quarter)
            by_filter = float(selected[column].sum()) if not selected.empty else 0.0
            by_column = float(df.loc[df["quarter"] == quarter, column].sum())
            ok = abs(by_filter - by_column) < 0.01
            print(f"  {quarter:<12}{label:<12}{rupiah(by_filter):>18}"
                  f"{rupiah(by_column):>18}{('COCOK' if ok else 'GAGAL'):>10}")
            if not ok:
                failures.append(f"kuartal {quarter} {label}")

    # ------------------------------------------------------------------
    section("5. CONVERSION RATE MENGIKUTI PERIODE")
    print("Satu bulan -> scope bulan itu, nilainya harus sama dengan sheet:")
    print(f"  {'bulan':<14}{'lewat filter':>16}{'langsung sheet':>18}{'status':>10}")
    line()
    for month in periods.TERM_MONTHS:
        selected = select(month)
        through_filter = metrics.get_conversion_rate(
            selected.df_conversion, scope=selected.conversion_scope
        )
        direct = metrics.get_conversion_rate(df_conversion, scope=month)
        ok = (pd.isna(through_filter) and pd.isna(direct)) or (
            not pd.isna(through_filter) and abs(through_filter - direct) < 0.01
        )
        print(f"  {month:<14}{percent(through_filter):>16}{percent(direct):>18}"
              f"{('COCOK' if ok else 'GAGAL'):>10}")
        if not ok:
            failures.append(f"conversion {month}")

    print()
    combined = select(("may", "june", "july"))
    combined_rate = metrics.get_conversion_rate(
        combined.df_conversion, scope=combined.conversion_scope
    )
    print(f"Gabungan bulan ({combined.label}):")
    print(f"  scope    : {combined.conversion_scope}")
    print(f"  nilai    : {percent(combined_rate)}")
    for note in combined.notes:
        print(f"  catatan  : {note}")
    check("gabungan bulan -> scope None", combined.conversion_scope is None)
    check("gabungan bulan -> conversion NaN, bukan 0", pd.isna(combined_rate))
    check("gabungan bulan -> ada catatan penjelasan", len(combined.notes) >= 1)

    # ------------------------------------------------------------------
    section("6. METRIK POSISI: TANGGAL ACUAN IKUT PERIODE")
    print(f"  {'periode':<14}{'tanggal acuan':>16}{'active partners':>18}"
          f"{'partner baru':>14}")
    line()
    active_counts: list[tuple[str, int]] = []
    for month in periods.TERM_MONTHS:
        selected = select(month)
        active = metrics.get_active_partner_count(selected.df_partner)
        active_counts.append((month, active))
        print(f"  {month:<14}{str(selected.reference_date.date()):>16}"
              f"{active:>18}{len(selected.df_partner_new):>14}")
    everything_active = metrics.get_active_partner_count(everything.df_partner)
    print(f"  {'all':<14}{str(everything.reference_date.date()):>16}"
          f"{everything_active:>18}{len(everything.df_partner_new):>14}")

    print()
    print("Baris df_partner TIDAK dibuang oleh filter (metrik posisi):")
    august = select("august")
    print(f"  df_partner saat filter august     : {len(august.df_partner)} baris")
    print(f"  df_partner_new saat filter august : {len(august.df_partner_new)} baris")
    check("filter bulan tidak mengurangi baris df_partner",
          len(august.df_partner) == len(df_partner))
    check("active per hari ini = 16 (baseline Phase 5)", everything_active == 16)

    # Kontrak hanya berakhir, tidak pernah dibatalkan mundur, jadi jumlah
    # partner aktif tidak boleh naik saat tanggal acuan makin baru.
    past = [
        (month, count)
        for month, count in active_counts
        if periods.period_reference_date(month, today=today) <= today
    ]
    non_increasing = all(
        past[index][1] >= past[index + 1][1] for index in range(len(past) - 1)
    )
    check("active partners tidak naik seiring tanggal acuan maju", non_increasing)

    future_reference = periods.period_reference_date("january", today=today)
    print()
    print(f"  acuan untuk bulan yang belum terjadi (january) : "
          f"{future_reference.date()}")
    check("bulan masa depan dibatasi hari ini", future_reference == today)

    print()
    print("Expiry tracker mengikuti tanggal acuan periode:")
    for selection in ("august", "all"):
        selected = select(selection)
        expiry = metrics.get_expiry_summary(
            selected.df_partner, reference_date=selected.reference_date
        )
        expired = int(
            expiry.loc[expiry["expiry_category"] == metrics.EXPIRY_EXPIRED,
                       "partner_count"].iloc[0]
        )
        print(f"  {selected.label:<18} acuan {selected.reference_date.date()} "
              f"-> expired {expired}")

    # ------------------------------------------------------------------
    section("7. PERILAKU TEPI")
    september = select("september")
    check("bulan tanpa MR (september) -> Total MR 0, bukan error",
          metrics.get_total_mr(september.df_mr) == 0)

    february = select("february")
    check("february -> Total MR 0 (dibuang di Phase 3)",
          metrics.get_total_mr(february.df_mr) == 0)
    check("february -> ada catatan soal MR yang dibuang",
          any("MR" in note for note in february.notes))
    check("february tetap punya data partner",
          len(february.df_partner_new) > 0)

    try:
        periods.normalize_months("agustusan")
        raised = False
    except ValueError:
        raised = True
    check("nama bulan ngawur -> ValueError", raised)

    try:
        periods.months_for_quarter("QUARTER #9")
        raised_quarter = False
    except ValueError:
        raised_quarter = True
    check("kuartal ngawur -> ValueError", raised_quarter)

    check("alias bulan Indonesia dikenali",
          periods.normalize_months("Agustus") == ("august",))
    check("pilihan tidak berurutan dirapikan ke urutan masa jabatan",
          periods.normalize_months(["july", "april"]) == ("april", "july"))
    check("pilihan ganda tidak dihitung dua kali",
          periods.normalize_months(["may", "may"]) == ("may",))

    empty_period = periods.apply_period("august")
    check("dataframe kosong -> tidak error",
          empty_period.df_mr.empty and empty_period.df_partner.empty)
    check("kolom month hilang -> KeyError", _raises_key_error(df_mr))
    check("available_months membedakan baris kosong dari data",
          "january" in periods.available_months(df_mr)
          and "january" not in periods.available_months(
              df_mr, value_column="pic_aiesec"))

    quarter_two = select("QUARTER #2")
    check("QUARTER #2 = may, june, july",
          quarter_two.months == ("may", "june", "july"))

    # ------------------------------------------------------------------
    print()
    line("#")
    if failures:
        print(f"PHASE 9 GAGAL. {len(failures)} pemeriksaan tidak lolos:")
        for item in failures:
            print(f"  - {item}")
        line("#")
        print()
        return 1

    print("PHASE 9 SELESAI. Filter periode konsisten dengan baseline Phase 3-8.")
    line("#")
    print()
    return 0


def _raises_key_error(df_mr: pd.DataFrame) -> bool:
    """Dataframe tanpa kolom month harus ditolak, bukan disaring diam-diam."""
    broken = df_mr.drop(columns=["month"])
    try:
        periods.filter_by_months(broken, "august")
    except KeyError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
