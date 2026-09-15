"""
app.py — STREAMLIT DASHBOARD (Phase 11)

Merangkai keempat layer menjadi satu dashboard:

    sheets.py      -> ambil data (satu-satunya yang menyentuh Google Sheets)
    preparation.py -> bangun dataframe
    periods.py     -> terjemahkan pilihan periode
    metrics.py     -> hitung KPI
    charts.py      -> gambar figure

File ini HANYA mengatur tata letak dan interaksi. Tidak ada aturan bisnis
baru di sini: kalau sebuah angka perlu dihitung, tempatnya di metrics.py.

CATATAN KEAMANAN
    Dashboard ini tidak punya autentikasi. Isinya nama partner dan nilai
    kontrak, jadi jangan diekspos ke internet tanpa proteksi akses
    (login Streamlit Cloud / SSO / reverse proxy). Lihat Phase 13.

Cara menjalankan:
    .venv/Scripts/streamlit.exe run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import charts, metrics, periods, preparation, sheets

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

# Data ESSM diperbarui manual oleh tim BD, jadi 15 menit sudah cukup segar
# dan menghemat kuota Google Sheets API.
CACHE_TTL_SECONDS = 15 * 60

# Modebar Plotly disembunyikan: ini dashboard, bukan alat eksplorasi grafik.
PLOTLY_CONFIG = {"displayModeBar": False, "displaylogo": False, "responsive": True}

RANGE_ALL = "Seluruh periode"
RANGE_QUARTER = "Kuartal"
RANGE_MONTH = "Bulan"

st.set_page_config(
    page_title="BD Analytics — AIESEC in BINUS",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sedikit CSS: merapatkan tata letak dan menyeragamkan kartu, supaya
# tampilannya kartu-kartu dashboard dan bukan tumpukan tabel.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1500px; }

      /* Kartu chart */
      div[data-testid="stVerticalBlockBorderWrapper"] {
          border-radius: 14px;
          border-color: #E7EBF0;
          background: #FFFFFF;
          box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
      }

      /* Kartu KPI */
      div[data-testid="stMetric"] {
          border-radius: 14px;
          border: 1px solid #E7EBF0;
          background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFE 100%);
          padding: 12px 14px 10px 14px;
      }
      div[data-testid="stMetricLabel"] p {
          font-size: 0.74rem; font-weight: 600; letter-spacing: .04em;
          text-transform: uppercase; color: #66707A;
      }
      div[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
      div[data-testid="stMetricDelta"] { font-size: 0.76rem; color: #66707A; }

      /* Judul halaman */
      .bd-title { font-size: 1.45rem; font-weight: 700; margin: 0; color: #1F2933; }
      .bd-subtitle { font-size: .85rem; color: #66707A; margin: .15rem 0 0 0; }

      /* Pill status */
      .bd-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin: .1rem 0 .2rem 0; }
      .bd-pill {
          font-size: .78rem; padding: .28rem .6rem; border-radius: 999px;
          border: 1px solid transparent; white-space: nowrap;
      }
      .bd-pill-alert { background: #FDF1E7; border-color: #F3C99B; color: #8A4B08; }
      .bd-pill-info  { background: #EAF2FE; border-color: #B9D5FB; color: #0B4F9E; }
      .bd-pill-ok    { background: #E9F7F1; border-color: #A8DFC8; color: #0A5C3E; }
      .bd-pill-muted { background: #F4F6F8; border-color: #E1E6EB; color: #5A6570; }

      /* Tab lebih rapat & jelas */
      button[data-baseweb="tab"] { padding-top: .35rem; padding-bottom: .35rem; }
      div[data-baseweb="tab-list"] { gap: .35rem; }

      /* Sidebar */
      section[data-testid="stSidebar"] { background: #FBFCFE; }
      .bd-brand { font-weight: 700; font-size: 1rem; color: #1F2933; }
      .bd-brand-sub { font-size: .78rem; color: #66707A; margin-top: -.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Menarik data dari ESSM ...")
def load_frames(reference_date: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Ambil dan bangun seluruh dataframe. Hasilnya di-cache per tanggal acuan."""
    partner_grid = preparation.load_partner_grid()
    return {
        "df_mr": preparation.build_df_mr(),
        "df_partner": preparation.build_df_partner(
            partner_grid, reference_date=reference_date
        ),
        "df_conversion": preparation.build_df_conversion(partner_grid),
        "df_financial": preparation.build_df_financial(
            preparation.load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
        ),
        "df_inkind": preparation.build_df_inkind(
            preparation.load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
        ),
    }


# ---------------------------------------------------------------------------
# Format tampilan
# ---------------------------------------------------------------------------


def number(value) -> str:
    """1694 -> 1.694 (pemisah ribuan gaya Indonesia)."""
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}".replace(",", ".")


def percent(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}%".replace(".", ",")


def pills(items: list[tuple[str, str]]) -> None:
    """Baris pill status. Teks selalu memuat angkanya, bukan hanya warna."""
    if not items:
        return
    html = "".join(
        f'<span class="bd-pill bd-pill-{kind}">{text}</span>' for kind, text in items
    )
    st.markdown(f'<div class="bd-pills">{html}</div>', unsafe_allow_html=True)


def card(figure, key: str | None = None) -> None:
    """Satu figure di dalam kartu."""
    with st.container(border=True):
        st.plotly_chart(
            figure, width="stretch", theme=None, config=PLOTLY_CONFIG, key=key
        )


# ---------------------------------------------------------------------------
# Sidebar: pilihan periode
# ---------------------------------------------------------------------------


def sidebar_controls() -> tuple[object, bool]:
    """Kontrol periode. Return (pilihan untuk periods.apply_period, minta_refresh)."""
    with st.sidebar:
        st.markdown(
            '<div class="bd-brand">AIESEC in BINUS</div>'
            '<div class="bd-brand-sub">Business Development Analytics</div>',
            unsafe_allow_html=True,
        )
        st.divider()

        mode = st.segmented_control(
            "Rentang waktu",
            [RANGE_ALL, RANGE_QUARTER, RANGE_MONTH],
            default=RANGE_ALL,
            key="range_mode",
        ) or RANGE_ALL

        selection: object = "all"
        if mode == RANGE_QUARTER:
            labels = {
                quarter: f"{quarter.replace('QUARTER ', 'Q').replace('#', '')} · "
                         f"{months[0].title()[:3]}–{months[-1].title()[:3]}"
                for quarter, months in periods.QUARTER_MONTHS.items()
            }
            selection = st.selectbox(
                "Kuartal", list(periods.QUARTER_MONTHS), index=1,
                format_func=lambda value: labels[value],
            )
        elif mode == RANGE_MONTH:
            selection = st.selectbox(
                "Bulan", list(periods.TERM_MONTHS),
                index=periods.TERM_MONTHS.index("august"),
                format_func=lambda value: str(value).title(),
            )

        st.divider()
        refresh = st.button("Muat ulang data", width="stretch", icon=":material/sync:")
        st.caption(
            "Sumber: Active ESSM (Market Research) dan National Mirror "
            "(partner, revenue, in-kind). Akses read-only; dashboard tidak "
            "bisa menulis ke ESSM."
        )
    return selection, refresh


# ---------------------------------------------------------------------------
# Bagian-bagian halaman
# ---------------------------------------------------------------------------


def header(frame: periods.PeriodFrames, loaded_at: pd.Timestamp) -> None:
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        st.markdown(
            '<p class="bd-title">Business Development Dashboard</p>'
            f'<p class="bd-subtitle">Periode {frame.label} · '
            f"metrik posisi dihitung per {frame.reference_date:%d %B %Y}</p>",
            unsafe_allow_html=True,
        )
    with right:
        scope_text = (
            "Conversion rate: seluruh periode"
            if frame.conversion_scope == metrics.SCOPE_ALL
            else f"Conversion rate: {frame.conversion_scope}"
            if frame.conversion_scope
            else "Conversion rate: tidak tersedia untuk periode ini"
        )
        pills(
            [
                ("muted", f"Data ditarik {loaded_at:%H:%M}"),
                ("info" if frame.conversion_scope else "alert", scope_text),
            ]
        )


def kpi_row(frame: periods.PeriodFrames, summary: dict, mr_by_pic: pd.DataFrame) -> None:
    columns = st.columns(5, gap="small")
    with columns[0]:
        st.metric(
            "Market Research",
            number(summary["total_mr"]),
            delta=f"{len(mr_by_pic)} PIC terlibat",
            delta_color="off",
            border=True,
            help="Jumlah kemunculan nama PIC di kolom PIC FROM AIESEC "
                 "(definisi tim BD), bukan jumlah baris.",
        )
    with columns[1]:
        st.metric(
            "Active Partners",
            number(summary["active_partners"]),
            delta=f"dari {number(summary['partner_count'])} partner",
            delta_color="off",
            border=True,
            help="Punya dokumen LoA DAN Month End belum terlewat pada "
                 "tanggal acuan periode. Kolom Status di sheet tidak dipakai.",
        )
    with columns[2]:
        st.metric(
            "Conversion Rate",
            percent(summary["conversion_rate"]),
            delta="tahap contract signed",
            delta_color="off",
            border=True,
            help="Diambil apa adanya dari National 1.1. Untuk periode "
                 "gabungan beberapa bulan sheet tidak menyediakannya, "
                 "jadi ditampilkan sebagai —.",
        )
    with columns[3]:
        st.metric(
            "Financial Revenue",
            charts.rupiah_compact(summary["financial_revenue"]),
            delta=f"{len(frame.df_financial)} pembayaran",
            delta_color="off",
            border=True,
            help=f"Nilai penuh: {charts.rupiah(summary['financial_revenue'])}. "
                 "Tidak pernah dijumlahkan dengan In-Kind Value.",
        )
    with columns[4]:
        st.metric(
            "In-Kind Value",
            charts.rupiah_compact(summary["inkind_value"]),
            delta=f"{len(frame.df_inkind)} dukungan",
            delta_color="off",
            border=True,
            help=f"Nilai penuh: {charts.rupiah(summary['inkind_value'])}. "
                 "Taksiran nilai dukungan non-tunai.",
        )


def alert_row(
    frame: periods.PeriodFrames,
    expiry_summary: pd.DataFrame,
    completeness: pd.DataFrame,
) -> None:
    """Ringkasan hal yang perlu ditindak, dalam satu baris pill."""
    items: list[tuple[str, str]] = []

    def count(category: str) -> int:
        row = expiry_summary.loc[expiry_summary["expiry_category"] == category]
        return int(row["partner_count"].iloc[0]) if not row.empty else 0

    ending = count(metrics.EXPIRY_THIS_MONTH)
    soon = count(metrics.EXPIRY_SOON)
    expired = count(metrics.EXPIRY_EXPIRED)
    if ending:
        items.append(("alert", f"⚠ {ending} kontrak berakhir bulan ini"))
    if soon:
        items.append(("info", f"{soon} kontrak berakhir dalam 1–3 bulan"))
    if expired:
        items.append(("muted", f"{expired} kontrak sudah berakhir"))

    if not completeness.empty:
        gaps = completeness[completeness["missing"] > 0]
        for row in gaps.itertuples():
            items.append(
                ("alert" if row.missing > 5 else "info",
                 f"{row.document.upper()} belum ada di {row.missing} partner aktif")
            )
        if gaps.empty:
            items.append(("ok", "Dokumen partner aktif lengkap"))

    for note in frame.notes:
        items.append(("muted", note.split(":")[0]))

    pills(items)


def tab_overview(data: dict) -> None:
    left, right = st.columns([3, 2], gap="small")
    with left:
        card(charts.mr_monthly_bar(data["mr_by_month"]), key="ov_mr_month")
        card(
            charts.revenue_monthly_bar(
                data["financial_monthly"], "Financial Revenue per bulan",
                color=charts.COLOR_FINANCIAL, value_label="Financial Revenue",
            ),
            key="ov_fin_month",
        )
    with right:
        card(charts.conversion_funnel_chart(data["funnel"]), key="ov_funnel")
        card(
            charts.revenue_comparison_bar(
                data["summary"]["financial_revenue"], data["summary"]["inkind_value"]
            ),
            key="ov_comparison",
        )

    lower_left, lower_right = st.columns(2, gap="small")
    with lower_left:
        card(charts.expiry_summary_bar(data["expiry_summary"]), key="ov_expiry")
    with lower_right:
        card(charts.document_completeness_bar(data["completeness"]), key="ov_documents")


def tab_market_research(data: dict) -> None:
    left, right = st.columns([2, 3], gap="small")
    with left:
        card(charts.mr_by_pic_bar(data["mr_by_pic"]), key="mr_pic")
    with right:
        card(charts.mr_monthly_bar(data["mr_by_month"]), key="mr_month")
        with st.container(border=True):
            st.markdown("**MR per PIC**")
            st.dataframe(
                data["mr_by_pic"].rename(
                    columns={
                        "pic_aiesec": "PIC",
                        "total_mr": "MR",
                        "share_percent": "Porsi (%)",
                    }
                ),
                hide_index=True,
                width="stretch",
                height=240,
                column_config={
                    "MR": st.column_config.NumberColumn(format="%d"),
                    "Porsi (%)": st.column_config.ProgressColumn(
                        format="%.2f%%", min_value=0.0, max_value=100.0
                    ),
                },
            )


def tab_partner(data: dict) -> None:
    left, right = st.columns(2, gap="small")
    with left:
        card(charts.stakeholder_donut(data["stakeholder"]), key="pt_donut")
    with right:
        card(charts.conversion_funnel_chart(data["funnel"]), key="pt_funnel")

    card(charts.conversion_monthly_line(data["conversion_monthly"]), key="pt_conv_month")

    with st.expander("Daftar partner aktif"):
        active = metrics.get_active_partners(data["frame"].df_partner)
        st.dataframe(
            active[
                ["partner_name", "stakeholder", "signed_month_raw", "end_month_raw",
                 "months_remaining"]
            ].rename(
                columns={
                    "partner_name": "Partner",
                    "stakeholder": "Stakeholder",
                    "signed_month_raw": "Mulai",
                    "end_month_raw": "Berakhir",
                    "months_remaining": "Sisa bulan",
                }
            ),
            hide_index=True,
            width="stretch",
        )


def tab_revenue(data: dict) -> None:
    st.caption(
        "Financial Revenue dan In-Kind Value adalah dua KPI terpisah dan "
        "tidak pernah dijumlahkan menjadi satu angka."
    )
    left, right = st.columns(2, gap="small")
    with left:
        card(
            charts.revenue_monthly_bar(
                data["financial_monthly"], "Financial Revenue per bulan",
                color=charts.COLOR_FINANCIAL, value_label="Financial Revenue",
            ),
            key="rv_fin_month",
        )
        card(
            charts.revenue_by_partner_bar(
                data["financial_partner"], "Financial Revenue per partner",
                color=charts.COLOR_FINANCIAL,
            ),
            key="rv_fin_partner",
        )
    with right:
        card(
            charts.revenue_monthly_bar(
                data["inkind_monthly"], "In-Kind Value per bulan",
                color=charts.COLOR_INKIND, value_label="In-Kind Value",
            ),
            key="rv_ink_month",
        )
        card(
            charts.revenue_by_partner_bar(
                data["inkind_partner"], "In-Kind Value per partner",
                color=charts.COLOR_INKIND,
            ),
            key="rv_ink_partner",
        )


def tab_documents(data: dict) -> None:
    left, right = st.columns(2, gap="small")
    with left:
        card(charts.document_completeness_bar(data["completeness"]), key="dc_complete")
    with right:
        card(
            charts.expiry_timeline_bar(data["expiry_detail"]),
            key="dc_timeline",
        )

    card(charts.document_tracker_heatmap(data["tracker"]), key="dc_tracker")

    with st.expander("Kontrak yang paling dekat berakhir"):
        detail = data["expiry_detail"]
        soon = detail[
            detail["expiry_category"].isin(
                [metrics.EXPIRY_EXPIRED, metrics.EXPIRY_THIS_MONTH, metrics.EXPIRY_SOON]
            )
        ]
        st.dataframe(
            soon[
                ["partner_name", "end_month_raw", "months_remaining",
                 "expiry_category", "is_active"]
            ].rename(
                columns={
                    "partner_name": "Partner",
                    "end_month_raw": "Berakhir",
                    "months_remaining": "Sisa bulan",
                    "expiry_category": "Kategori",
                    "is_active": "Aktif",
                }
            ),
            hide_index=True,
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    selection, refresh = sidebar_controls()
    if refresh:
        load_frames.clear()

    today = pd.Timestamp.today().normalize()
    try:
        frames = load_frames(today)
    except Exception as exc:  # noqa: BLE001 - pesan ramah, detail tetap muncul
        st.error(
            "Gagal menarik data dari Google Sheets. Periksa `.env`, "
            "`credentials.json`, dan akses Viewer ke kedua spreadsheet."
        )
        st.exception(exc)
        return

    frame = periods.apply_period(selection, today=today, **frames)

    summary = metrics.get_kpi_summary(
        frame.df_mr, frame.df_partner, frame.df_conversion,
        frame.df_financial, frame.df_inkind,
        scope=frame.conversion_scope or "",
    )
    data = {
        "frame": frame,
        "summary": summary,
        "mr_by_pic": metrics.get_mr_by_pic(frame.df_mr),
        "mr_by_month": metrics.get_mr_by_month(frame.df_mr),
        "stakeholder": metrics.get_stakeholder_distribution(frame.df_partner),
        "funnel": metrics.get_conversion_funnel(
            frame.df_conversion, scope=frame.conversion_scope or ""
        ),
        "conversion_monthly": metrics.get_conversion_by_month(frame.df_conversion),
        "financial_monthly": metrics.get_revenue_by_month(frame.df_financial, "revenue"),
        "inkind_monthly": metrics.get_revenue_by_month(frame.df_inkind, "inkind_value"),
        "financial_partner": metrics.get_revenue_by_partner(
            frame.df_financial, "revenue"
        ),
        "inkind_partner": metrics.get_revenue_by_partner(
            frame.df_inkind, "inkind_value"
        ),
        "completeness": metrics.get_document_completeness(frame.df_partner),
        "tracker": metrics.get_document_tracker(frame.df_partner),
        "expiry_summary": metrics.get_expiry_summary(
            frame.df_partner, reference_date=frame.reference_date
        ),
        "expiry_detail": metrics.get_partnership_expiry(
            frame.df_partner, reference_date=frame.reference_date
        ),
    }

    header(frame, pd.Timestamp.now())
    kpi_row(frame, summary, data["mr_by_pic"])
    alert_row(frame, data["expiry_summary"], data["completeness"])

    overview, market, partner, revenue, documents = st.tabs(
        ["Ringkasan", "Market Research", "Partner & Funnel", "Revenue",
         "Dokumen & Kontrak"]
    )
    with overview:
        tab_overview(data)
    with market:
        tab_market_research(data)
    with partner:
        tab_partner(data)
    with revenue:
        tab_revenue(data)
    with documents:
        tab_documents(data)

    st.caption(
        "National ESSM asli tidak pernah diakses langsung — satu-satunya "
        "sumber National adalah mirror spreadsheet. Angka conversion rate "
        "diambil dari sheet, tidak dihitung ulang."
    )


if __name__ == "__main__":
    main()
