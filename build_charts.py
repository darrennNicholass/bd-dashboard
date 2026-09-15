"""
build_charts.py - PHASE 10: BUILD & VALIDATE VISUALIZATION LAYER

Membangun seluruh figure di src/charts.py memakai data live, lalu MEMERIKSA
ISI FIGURE-nya: angka yang tergambar harus sama dengan angka dari metrics.py.
Grafik yang cantik tapi angkanya bergeser lebih berbahaya daripada tabel biasa.

Yang diperiksa:
    1. Semua figure terbentuk (tipe, jumlah trace)
    2. Angka di dalam figure = angka dari metrics.py
    3. Financial vs In-Kind tidak pernah bertumpuk (barmode group)
    4. Integrasi dengan filter periode Phase 9
    5. Perilaku tepi: dataframe kosong, kolom kurang, conversion tidak tersedia
    6. Menulis pratinjau HTML semua figure (tidak masuk Git)

Cara menjalankan (dari root proyek):
    .venv/Scripts/python.exe build_charts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from src import charts, metrics, periods, preparation, sheets

PREVIEW_PATH = Path(__file__).resolve().parent / "phase10_charts.html"

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


def trace_sum(fig: go.Figure, axis: str = "y") -> float:
    """Jumlahkan seluruh nilai yang benar-benar tergambar di figure."""
    total = 0.0
    for trace in fig.data:
        values = getattr(trace, axis, None)
        if values is None:
            values = getattr(trace, "values", None)
        if values is None:
            continue
        total += float(pd.Series(list(values)).astype("float64").fillna(0).sum())
    return total


def main() -> int:
    print()
    line("#")
    print("PHASE 10 - BUILD & VALIDATE VISUALIZATION LAYER (src/charts.py)")
    line("#")

    today = pd.Timestamp.today().normalize()
    print()
    print("Aturan yang dipakai:")
    print("  charts.py hanya menerima dataframe hasil metrics.py")
    print("  Financial vs In-Kind: barmode group, TIDAK PERNAH stack")
    print("  warna bukan satu-satunya pembawa informasi (selalu ada label teks)")
    print("  dataframe kosong -> figure berketerangan, bukan exception")

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

    everything = periods.apply_period(
        "all",
        df_mr=df_mr,
        df_partner=df_partner,
        df_conversion=df_conversion,
        df_financial=df_financial,
        df_inkind=df_inkind,
        today=today,
    )

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK' if ok else 'GAGAL':<5}] {label}"
              + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(label)

    # ------------------------------------------------------------------
    # Data untuk figure, semuanya lewat metrics.py
    # ------------------------------------------------------------------
    mr_by_pic = metrics.get_mr_by_pic(everything.df_mr)
    mr_by_month = metrics.get_mr_by_month(everything.df_mr)
    stakeholder = metrics.get_stakeholder_distribution(everything.df_partner)
    funnel = metrics.get_conversion_funnel(
        everything.df_conversion, scope=everything.conversion_scope
    )
    conversion_monthly = metrics.get_conversion_by_month(everything.df_conversion)
    financial_monthly = metrics.get_revenue_by_month(everything.df_financial, "revenue")
    inkind_monthly = metrics.get_revenue_by_month(everything.df_inkind, "inkind_value")
    financial_partner = metrics.get_revenue_by_partner(everything.df_financial, "revenue")
    inkind_partner = metrics.get_revenue_by_partner(everything.df_inkind, "inkind_value")
    completeness = metrics.get_document_completeness(everything.df_partner)
    tracker = metrics.get_document_tracker(everything.df_partner)
    expiry_summary = metrics.get_expiry_summary(
        everything.df_partner, reference_date=everything.reference_date
    )
    expiry_detail = metrics.get_partnership_expiry(
        everything.df_partner, reference_date=everything.reference_date
    )

    figures: dict[str, go.Figure] = {
        "MR per PIC": charts.mr_by_pic_bar(mr_by_pic),
        "MR per bulan": charts.mr_monthly_bar(mr_by_month),
        "Distribusi stakeholder": charts.stakeholder_donut(stakeholder),
        "Sales funnel": charts.conversion_funnel_chart(funnel),
        "Conversion per bulan": charts.conversion_monthly_line(conversion_monthly),
        "Financial per bulan": charts.revenue_monthly_bar(
            financial_monthly, "Financial Revenue per bulan",
            color=charts.COLOR_FINANCIAL, value_label="Financial Revenue",
        ),
        "In-Kind per bulan": charts.revenue_monthly_bar(
            inkind_monthly, "In-Kind Value per bulan",
            color=charts.COLOR_INKIND, value_label="In-Kind Value",
        ),
        "Financial vs In-Kind": charts.revenue_comparison_bar(
            metrics.get_financial_revenue(everything.df_financial),
            metrics.get_inkind_value(everything.df_inkind),
        ),
        "Financial per partner": charts.revenue_by_partner_bar(
            financial_partner, "Financial Revenue per partner",
            color=charts.COLOR_FINANCIAL,
        ),
        "In-Kind per partner": charts.revenue_by_partner_bar(
            inkind_partner, "In-Kind Value per partner", color=charts.COLOR_INKIND,
        ),
        "Kelengkapan dokumen": charts.document_completeness_bar(completeness),
        "Document tracker": charts.document_tracker_heatmap(tracker),
        "Status kontrak": charts.expiry_summary_bar(expiry_summary),
        "Kontrak per bulan berakhir": charts.expiry_timeline_bar(expiry_detail),
    }

    # ------------------------------------------------------------------
    section("1. SEMUA FIGURE TERBENTUK")
    print(f"  {'figure':<30}{'tipe trace':<22}{'jumlah trace':>14}")
    line()
    for name, fig in figures.items():
        kinds = ", ".join(sorted({trace.type for trace in fig.data})) or "(kosong)"
        print(f"  {name:<30}{kinds:<22}{len(fig.data):>14}")
        if not isinstance(fig, go.Figure) or not fig.data:
            failures.append(f"figure {name} tidak terbentuk")

    print()
    for name, fig in figures.items():
        has_title = bool(fig.layout.title.text)
        has_description = bool((fig.layout.meta or {}).get("description"))
        if not (has_title and has_description):
            failures.append(f"figure {name} tanpa judul/keterangan")
    check("semua figure punya judul dan keterangan teks",
          not any("tanpa judul" in item for item in failures))

    # ------------------------------------------------------------------
    section("2. ANGKA DI DALAM FIGURE = ANGKA DARI metrics.py")
    check("MR per PIC: jumlah bar = Total MR",
          int(trace_sum(figures["MR per PIC"], "x")) == BASELINE["total_mr"],
          f"{int(trace_sum(figures['MR per PIC'], 'x'))}")
    check("MR per PIC: jumlah batang = jumlah PIC",
          len(figures["MR per PIC"].data[0].x) == len(mr_by_pic),
          f"{len(mr_by_pic)} PIC")
    check("MR per PIC: batang teratas = PIC dengan MR terbanyak",
          figures["MR per PIC"].data[0].y[-1] == mr_by_pic.iloc[0]["pic_aiesec"],
          str(mr_by_pic.iloc[0]["pic_aiesec"]))
    check("MR per bulan: jumlah bar = Total MR",
          int(trace_sum(figures["MR per bulan"])) == BASELINE["total_mr"])
    check("Stakeholder: jumlah potongan donut = jumlah partner",
          int(trace_sum(figures["Distribusi stakeholder"])) == BASELINE["partner_count"])
    check("Funnel: nilai tahap terakhir = conversion rate KPI",
          abs(float(figures["Sales funnel"].data[0].x[-1]) - BASELINE["conversion_rate"])
          < 0.01,
          f"{float(figures['Sales funnel'].data[0].x[-1]):.2f}%")
    check("Financial per bulan: jumlah bar = total financial",
          abs(trace_sum(figures["Financial per bulan"]) - BASELINE["financial_revenue"])
          < 0.01)
    check("In-Kind per bulan: jumlah bar = total in-kind",
          abs(trace_sum(figures["In-Kind per bulan"]) - BASELINE["inkind_value"]) < 0.01)
    # Chart per partner hanya menampilkan sebagian teratas, jadi
    # pembandingnya juga sebagian teratas — bukan totalnya.
    top_n = 8
    check(f"Financial per partner: jumlah bar = {top_n} partner teratas",
          abs(trace_sum(figures["Financial per partner"], "x")
              - float(financial_partner.head(top_n)["amount"].sum())) < 0.01)
    check(f"In-Kind per partner: jumlah bar = {top_n} partner teratas",
          abs(trace_sum(figures["In-Kind per partner"], "x")
              - float(inkind_partner.head(top_n)["amount"].sum())) < 0.01)
    check("chart per partner menyebut berapa dari berapa yang ditampilkan",
          f"dari {len(inkind_partner)} partner"
          in str((figures["In-Kind per partner"].layout.meta or {}).get("description")),
          str((figures["In-Kind per partner"].layout.meta or {}).get("description")))
    check("Kelengkapan dokumen: ada + belum ada = partner aktif x 4 jenis",
          int(trace_sum(figures["Kelengkapan dokumen"]))
          == BASELINE["active_partners"] * len(preparation.DOCUMENT_FIELDS))
    heatmap = figures["Document tracker"].data[0]
    check("Document tracker: baris heatmap = jumlah partner aktif",
          len(heatmap.y) == BASELINE["active_partners"], f"{len(heatmap.y)} baris")
    check("Document tracker: kolom LoA semuanya ADA (16 partner)",
          int(sum(row[2] for row in heatmap.z)) == BASELINE["active_partners"])
    check("Status kontrak: jumlah kategori = jumlah partner",
          int(trace_sum(figures["Status kontrak"])) == BASELINE["partner_count"])
    check("Kontrak per bulan berakhir: jumlah = partner aktif",
          int(trace_sum(figures["Kontrak per bulan berakhir"]))
          == BASELINE["active_partners"])

    conversion_trace = figures["Conversion per bulan"].data[0]
    filled = int(pd.Series(list(conversion_trace.y)).notna().sum())
    expected_filled = int(conversion_monthly["conversion_rate"].notna().sum())
    check("Conversion per bulan: bulan tanpa angka dibiarkan bolong, bukan 0",
          filled == expected_filled and conversion_trace.connectgaps is False,
          f"{filled} bulan terisi")

    # ------------------------------------------------------------------
    section("3. FINANCIAL DAN IN-KIND TIDAK PERNAH BERTUMPUK")
    comparison = figures["Financial vs In-Kind"]
    check("figure perbandingan memakai barmode 'group'",
          comparison.layout.barmode == "group", str(comparison.layout.barmode))
    check("figure perbandingan punya dua trace terpisah", len(comparison.data) == 2)
    check("nilai financial di figure = baseline",
          abs(float(comparison.data[0].y[0]) - BASELINE["financial_revenue"]) < 0.01)
    check("nilai in-kind di figure = baseline",
          abs(float(comparison.data[1].y[0]) - BASELINE["inkind_value"]) < 0.01)

    stacked = {
        name: fig.layout.barmode
        for name, fig in figures.items()
        if fig.layout.barmode == "stack"
    }
    print()
    print("Figure yang memang bertumpuk (tinggi totalnya bermakna):")
    for name in stacked:
        print(f"  - {name}")
    check("hanya figure dokumen & expiry yang bertumpuk",
          set(stacked) == {"Kelengkapan dokumen", "Kontrak per bulan berakhir"})

    # ------------------------------------------------------------------
    section("4. INTEGRASI DENGAN FILTER PERIODE (PHASE 9)")
    august = periods.apply_period(
        "august",
        df_mr=df_mr, df_partner=df_partner, df_conversion=df_conversion,
        df_financial=df_financial, df_inkind=df_inkind, today=today,
    )
    august_mr_fig = charts.mr_by_pic_bar(metrics.get_mr_by_pic(august.df_mr))
    august_total = metrics.get_total_mr(august.df_mr)
    print(f"  Total MR August menurut metrics : {august_total}")
    print(f"  Jumlah bar di figure August     : {int(trace_sum(august_mr_fig, 'x'))}")
    check("figure mengikuti periode terpilih",
          int(trace_sum(august_mr_fig, "x")) == august_total)

    quarter = periods.apply_period(
        "QUARTER #2",
        df_mr=df_mr, df_partner=df_partner, df_conversion=df_conversion,
        df_financial=df_financial, df_inkind=df_inkind, today=today,
    )
    quarter_funnel = charts.conversion_funnel_chart(
        metrics.get_conversion_funnel(
            quarter.df_conversion, scope=quarter.conversion_scope
        )
    )
    annotations = [item.text for item in quarter_funnel.layout.annotations]
    print()
    print(f"  Scope conversion QUARTER #2 : {quarter.conversion_scope}")
    print(f"  Keterangan di figure        : {annotations[0] if annotations else '(tidak ada)'}")
    check("conversion tidak tersedia -> figure menjelaskan, bukan angka palsu",
          any("conversion rate" in str(text) for text in annotations))

    # ------------------------------------------------------------------
    section("5. PERILAKU TEPI")
    empty = pd.DataFrame()
    empty_cases = {
        "mr_by_pic_bar": charts.mr_by_pic_bar(empty),
        "mr_monthly_bar": charts.mr_monthly_bar(empty),
        "stakeholder_donut": charts.stakeholder_donut(empty),
        "conversion_funnel_chart": charts.conversion_funnel_chart(empty),
        "conversion_monthly_line": charts.conversion_monthly_line(empty),
        "revenue_monthly_bar": charts.revenue_monthly_bar(empty, "kosong"),
        "revenue_by_partner_bar": charts.revenue_by_partner_bar(empty, "kosong"),
        "document_completeness_bar": charts.document_completeness_bar(empty),
        "document_tracker_heatmap": charts.document_tracker_heatmap(empty),
        "expiry_summary_bar": charts.expiry_summary_bar(empty),
        "expiry_timeline_bar": charts.expiry_timeline_bar(empty),
    }
    for name, fig in empty_cases.items():
        texts = [str(item.text) for item in fig.layout.annotations]
        expected = (
            "conversion rate" if name == "conversion_funnel_chart" else "Tidak ada data"
        )
        ok = bool(fig.data == ()) and any(expected in text for text in texts)
        if not ok:
            failures.append(f"{name}(kosong) tidak menampilkan keterangan")
    check("semua fungsi menangani dataframe kosong dengan keterangan",
          not any("(kosong)" in item for item in failures),
          f"{len(empty_cases)} fungsi")

    check("kolom kurang -> KeyError",
          _raises_key_error(mr_by_pic))
    check("nilai NaN pada perbandingan revenue tidak jadi 0 diam-diam",
          charts.rupiah(float("nan")) == "-")
    check("format rupiah gaya Indonesia",
          charts.rupiah(26_750_000) == "Rp26.750.000",
          charts.rupiah(26_750_000))

    palette_unique = len(set(charts.PALETTE)) == len(charts.PALETTE)
    check("palet colorblind-safe tanpa warna kembar", palette_unique,
          f"{len(charts.PALETTE)} warna")

    # ------------------------------------------------------------------
    section("6. PRATINJAU HTML")
    try:
        _write_preview(figures, everything.label)
        print(f"  Ditulis ke : {PREVIEW_PATH.name}")
        print("  Buka file itu di browser untuk memeriksa grafiknya secara visual.")
        print("  File ini berisi nama partner, jadi tidak masuk Git (.gitignore).")
        check("pratinjau HTML tertulis", PREVIEW_PATH.is_file(),
              f"{PREVIEW_PATH.stat().st_size // 1024} KB")
    except Exception as exc:
        check(f"pratinjau HTML tertulis ({type(exc).__name__}: {exc})", False)

    # ------------------------------------------------------------------
    print()
    line("#")
    if failures:
        print(f"PHASE 10 GAGAL. {len(failures)} pemeriksaan tidak lolos:")
        for item in failures:
            print(f"  - {item}")
        line("#")
        print()
        return 1

    print("PHASE 10 SELESAI. Semua figure memuat angka yang sama dengan metrics.py.")
    line("#")
    print()
    return 0


def _raises_key_error(df_by_pic: pd.DataFrame) -> bool:
    """Kolom yang salah harus ditolak, bukan menghasilkan grafik menyesatkan."""
    broken = df_by_pic.rename(columns={"total_mr": "jumlah"})
    try:
        charts.mr_by_pic_bar(broken)
    except KeyError:
        return True
    return False


def _write_preview(figures: dict[str, go.Figure], label: str) -> None:
    """Tulis semua figure ke satu file HTML untuk pemeriksaan visual."""
    blocks = []
    for index, (name, fig) in enumerate(figures.items()):
        blocks.append(
            fig.to_html(
                full_html=False,
                include_plotlyjs="cdn" if index == 0 else False,
                div_id=f"chart-{index}",
            )
        )
    body = "\n<hr>\n".join(blocks)
    PREVIEW_PATH.write_text(
        "<!doctype html>\n"
        '<html lang="id">\n<head>\n<meta charset="utf-8">\n'
        "<title>Phase 10 - Pratinjau chart BD Dashboard</title>\n"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}"
        "h1{font-size:20px}p{color:#555}</style>\n</head>\n<body>\n"
        "<h1>Pratinjau chart BD Dashboard</h1>\n"
        f"<p>Periode: {label}. Dibuat oleh build_charts.py "
        f"pada {pd.Timestamp.now():%Y-%m-%d %H:%M}.</p>\n"
        f"{body}\n</body>\n</html>\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
