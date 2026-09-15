"""
build_app.py - PHASE 11: BUILD & VALIDATE STREAMLIT DASHBOARD

Menjalankan app.py secara HEADLESS memakai streamlit.testing.AppTest, lalu
membandingkan angka yang benar-benar tampil di kartu KPI dengan angka yang
dihitung ulang lewat periods.py + metrics.py.

Yang diperiksa:
    1. App berjalan tanpa exception (periode default)
    2. Lima kartu KPI menampilkan angka yang benar
    3. Ganti periode (bulan & kuartal) mengubah seluruh KPI dengan benar
    4. Periode tanpa conversion rate ditampilkan sebagai "—", bukan 0
    5. Tidak ada elemen yang menjumlahkan Financial + In-Kind
    6. Struktur halaman: tab, tabel, dan pill status

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_app.py
"""

from __future__ import annotations

import sys

import pandas as pd
from streamlit.testing.v1 import AppTest

from src import metrics, periods, preparation, sheets

# Menarik lima dataset dari Google Sheets butuh waktu; beri kelonggaran.
RUN_TIMEOUT = 240

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


# Format tampilan ditulis ulang di sini (bukan diimpor dari app.py) supaya
# script ini tidak perlu menjalankan Streamlit di luar konteksnya.
def number(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}".replace(",", ".")


def percent(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}%".replace(".", ",")


def rupiah_compact(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    amount = float(value)
    if amount == 0:
        return "Rp0"
    for divisor, unit in ((1_000_000_000, "M"), (1_000_000, "jt"), (1_000, "rb")):
        if abs(amount) >= divisor:
            text = f"{amount / divisor:.1f}"
            if text.endswith(".0"):
                text = text[:-2]
            return f"Rp{text.replace('.', ',')} {unit}"
    return f"Rp{amount:,.0f}".replace(",", ".")


def main() -> int:
    print()
    line("#")
    print("PHASE 11 - BUILD & VALIDATE STREAMLIT DASHBOARD (app.py)")
    line("#")

    today = pd.Timestamp.today().normalize()
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK' if ok else 'GAGAL':<5}] {label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(label)

    print()
    print("Menarik data pembanding langsung dari ESSM ...")
    try:
        partner_grid = preparation.load_partner_grid()
        frames = {
            "df_mr": preparation.build_df_mr(),
            "df_partner": preparation.build_df_partner(
                partner_grid, reference_date=today
            ),
            "df_conversion": preparation.build_df_conversion(partner_grid),
            "df_financial": preparation.build_df_financial(
                preparation.load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
            ),
            "df_inkind": preparation.build_df_inkind(
                preparation.load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
            ),
        }
    except Exception as exc:
        print(f"\nGagal membangun dataframe pembanding: {type(exc).__name__}: {exc}")
        return 1

    def expected(selection) -> dict[str, str]:
        """KPI yang SEHARUSNYA tampil untuk satu pilihan periode."""
        frame = periods.apply_period(selection, today=today, **frames)
        summary = metrics.get_kpi_summary(
            frame.df_mr, frame.df_partner, frame.df_conversion,
            frame.df_financial, frame.df_inkind,
            scope=frame.conversion_scope or "",
        )
        return {
            "Market Research": number(summary["total_mr"]),
            "Active Partners": number(summary["active_partners"]),
            "Conversion Rate": percent(summary["conversion_rate"]),
            "Financial Revenue": rupiah_compact(summary["financial_revenue"]),
            "In-Kind Value": rupiah_compact(summary["inkind_value"]),
        }

    # ------------------------------------------------------------------
    section("1. APP BERJALAN (PERIODE DEFAULT: SELURUH PERIODE)")
    app = AppTest.from_file("app.py", default_timeout=RUN_TIMEOUT)
    app.run()

    if app.exception:
        print("  Exception saat menjalankan app.py:")
        for item in app.exception:
            print(f"    {item.value}")
        failures.append("app.py raise exception")
        line("#")
        return 1

    check("app.py berjalan tanpa exception", not app.exception)
    check("ada 5 kartu KPI", len(app.metric) == 5, f"{len(app.metric)} kartu")
    check("ada 5 tab", len(app.tabs) >= 5, f"{len(app.tabs)} tab")
    check("kontrol periode ada di sidebar",
          len(app.segmented_control) == 1 and len(app.button) >= 1)

    # ------------------------------------------------------------------
    section("2. ANGKA DI KARTU KPI = HASIL metrics.py")
    labels = [item.label for item in app.metric]
    values = {item.label: item.value for item in app.metric}
    print(f"  {'kartu':<20}{'di dashboard':>18}{'hasil metrics':>18}{'status':>10}")
    line()
    for label, want in expected("all").items():
        got = values.get(label, "(tidak ada)")
        ok = got == want
        print(f"  {label:<20}{got:>18}{want:>18}{('COCOK' if ok else 'GAGAL'):>10}")
        if not ok:
            failures.append(f"KPI {label} (seluruh periode)")

    check("Total MR = baseline 1694", values.get("Market Research") == number(1694))
    check("Active Partners = baseline 16", values.get("Active Partners") == "16")
    check("Conversion Rate = baseline 82,86%",
          values.get("Conversion Rate") == "82,86%")

    # ------------------------------------------------------------------
    section("3. GANTI PERIODE: BULAN AGUSTUS")
    app.segmented_control[0].set_value("Bulan").run()
    if app.exception:
        for item in app.exception:
            print(f"    {item.value}")
        failures.append("app.py raise exception saat ganti ke Bulan")
    else:
        month = app.selectbox[0].value
        print(f"  Bulan terpilih : {month}")
        values = {item.label: item.value for item in app.metric}
        print(f"  {'kartu':<20}{'di dashboard':>18}{'hasil metrics':>18}{'status':>10}")
        line()
        for label, want in expected(month).items():
            got = values.get(label, "(tidak ada)")
            ok = got == want
            print(f"  {label:<20}{got:>18}{want:>18}{('COCOK' if ok else 'GAGAL'):>10}")
            if not ok:
                failures.append(f"KPI {label} (bulan {month})")
        check("Total MR bulan berbeda dari total seluruh periode",
              values.get("Market Research") != number(1694),
              str(values.get("Market Research")))

    # ------------------------------------------------------------------
    section("4. GANTI PERIODE: KUARTAL (CONVERSION TIDAK TERSEDIA)")
    app.segmented_control[0].set_value("Kuartal").run()
    if app.exception:
        for item in app.exception:
            print(f"    {item.value}")
        failures.append("app.py raise exception saat ganti ke Kuartal")
    else:
        quarter = app.selectbox[0].value
        print(f"  Kuartal terpilih : {quarter}")
        values = {item.label: item.value for item in app.metric}
        print(f"  {'kartu':<20}{'di dashboard':>18}{'hasil metrics':>18}{'status':>10}")
        line()
        for label, want in expected(quarter).items():
            got = values.get(label, "(tidak ada)")
            ok = got == want
            print(f"  {label:<20}{got:>18}{want:>18}{('COCOK' if ok else 'GAGAL'):>10}")
            if not ok:
                failures.append(f"KPI {label} ({quarter})")

        check("conversion rate gabungan bulan ditampilkan '—', bukan 0",
              values.get("Conversion Rate") == "—",
              str(values.get("Conversion Rate")))

        page_text = " ".join(item.value for item in app.markdown)
        check("halaman menjelaskan conversion rate tidak tersedia",
              "tidak tersedia" in page_text.lower())

    # ------------------------------------------------------------------
    section("5. ATURAN BISNIS TERJAGA DI UI")
    app.segmented_control[0].set_value("Semua").run()
    labels = [item.label for item in app.metric]
    page_text = " ".join(item.value for item in app.markdown).lower()
    caption_text = " ".join(item.value for item in app.caption).lower()

    check("tidak ada kartu 'Total Revenue'",
          not any("total revenue" in label.lower() for label in labels),
          ", ".join(labels))
    check("Financial dan In-Kind muncul sebagai dua kartu terpisah",
          "Financial Revenue" in labels and "In-Kind Value" in labels)
    check("halaman menyebut keduanya tidak dijumlahkan",
          "tidak pernah dijumlahkan" in caption_text
          or "tidak pernah dijumlahkan" in page_text)
    check("URL spreadsheet tidak pernah dicetak ke halaman",
          "docs.google.com" not in page_text + caption_text)

    # ------------------------------------------------------------------
    section("6. STRUKTUR HALAMAN")
    check("ada pill status (kontrak/dokumen)",
          any("kontrak" in item.value.lower() or "belum ada" in item.value.lower()
              for item in app.markdown))
    check("ada tabel pendukung", len(app.dataframe) >= 1,
          f"{len(app.dataframe)} tabel")
    check("judul halaman tampil",
          any("Business Development Dashboard" in item.value for item in app.markdown))

    # ------------------------------------------------------------------
    print()
    line("#")
    if failures:
        print(f"PHASE 11 GAGAL. {len(failures)} pemeriksaan tidak lolos:")
        for item in failures:
            print(f"  - {item}")
        line("#")
        print()
        return 1

    print("PHASE 11 SELESAI. Dashboard menampilkan angka yang sama dengan metrics.py.")
    line("#")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
