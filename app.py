"""
app.py — STREAMLIT DASHBOARD (Phase 11)

Merangkai keempat layer menjadi satu dashboard:

    sheets.py      -> ambil data (satu-satunya yang menyentuh Google Sheets)
    preparation.py -> bangun dataframe
    periods.py     -> terjemahkan pilihan periode
    metrics.py     -> hitung KPI
    charts.py      -> gambar figure

File ini HANYA mengatur tata letak, gaya, dan interaksi. Tidak ada aturan
bisnis baru di sini: kalau sebuah angka perlu dihitung, tempatnya di
metrics.py.

ARAH DESAIN (referensi tim BD)
    SaaS dashboard soft-indigo: latar lavender lembut, kartu putih membulat
    dengan bayangan halus, satu kartu gelap bergradien sebagai penarik mata,
    kartu statistik kecil bersparkline, dan tipografi geometris dua keluarga
    font (Plus Jakarta Sans untuk judul/angka, Inter untuk teks).

CATATAN KEAMANAN
    Dashboard ini tidak punya autentikasi. Isinya nama partner dan nilai
    kontrak, jadi jangan diekspos ke internet tanpa proteksi akses
    (login Streamlit Cloud / SSO / reverse proxy). Lihat Phase 13.

Cara menjalankan:
    .venv/Scripts/streamlit.exe run app.py
"""

from __future__ import annotations

import html

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

RANGE_ALL = "Semua"
RANGE_QUARTER = "Kuartal"
RANGE_MONTH = "Bulan"

# Warna delta sekaligus mewarnai sparkline di kartu KPI. Semuanya dijaga
# di keluarga biru: indigo (primary) dan biru langit, sejalan dengan warna
# KPI itu di grafik (financial indigo, in-kind biru langit). Variasi
# tampilan datang dari bentuk sparkline-nya, bukan dari warna acak.
KPI_COLORS = {
    "mr": "primary",
    "partner": "blue",
    "conversion": "primary",
    "financial": "primary",
    "inkind": "blue",
}

st.set_page_config(
    page_title="BD Analytics — AIESEC in BINUS",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Gaya
# ---------------------------------------------------------------------------

STYLE = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@400;500;600&display=swap');

  html, body, [class*="st-"], button, input, select, textarea {
      font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  }
  h1, h2, h3, h4, .bd-title, .bd-card-title, div[data-testid="stMetricValue"] {
      font-family: 'Plus Jakarta Sans', 'Inter', system-ui, sans-serif;
  }

  .stApp { background: #F4F6FC; }
  /* Toolbar Streamlit dibuat transparan supaya judul halaman tidak tertutup. */
  header[data-testid="stHeader"] { background: transparent; }
  .block-container { padding-top: 3rem; padding-bottom: 2.2rem; max-width: 1560px; }

  /* ---- Kartu ---- */
  div[data-testid="stVerticalBlockBorderWrapper"] {
      background: #FFFFFF;
      border: 1px solid #EDF0F8;
      border-radius: 20px;
      box-shadow: 0 6px 20px rgba(27, 37, 89, 0.05);
      padding: .25rem .35rem;
  }

  /* ---- Kartu KPI ---- */
  div[data-testid="stMetric"] {
      background: #FFFFFF;
      border: 1px solid #EDF0F8;
      border-radius: 20px;
      box-shadow: 0 6px 20px rgba(27, 37, 89, 0.05);
      padding: 14px 16px 10px 16px;
  }
  div[data-testid="stMetricLabel"] p {
      font-size: .72rem; font-weight: 600; letter-spacing: .08em;
      text-transform: uppercase; color: #8A94AD;
  }
  div[data-testid="stMetricValue"] {
      font-size: 1.75rem; font-weight: 800; color: #1B2559; line-height: 1.15;
  }
  div[data-testid="stMetricDelta"] { font-size: .74rem; color: #8A94AD; font-weight: 500; }

  /* ---- Kartu gelap (penarik mata, mengikuti referensi) ---- */
  .st-key-hero_highlight div[data-testid="stVerticalBlockBorderWrapper"],
  div[class*="st-key-hero_highlight"] {
      background: linear-gradient(155deg, #5B6BF7 0%, #4453D6 55%, #2F3BAF 100%);
      border: none;
      box-shadow: 0 14px 30px rgba(59, 75, 216, 0.28);
  }
  div[class*="st-key-hero_highlight"] .bd-card-title,
  div[class*="st-key-hero_highlight"] .bd-card-sub,
  div[class*="st-key-hero_highlight"] p { color: #FFFFFF !important; }
  div[class*="st-key-hero_highlight"] .bd-card-sub { color: rgba(255,255,255,.78) !important; }

  /* ---- Judul ---- */
  .bd-title { font-size: 1.5rem; font-weight: 800; margin: 0; color: #1B2559;
              letter-spacing: -.01em; }
  .bd-subtitle { font-size: .84rem; color: #8A94AD; margin: .2rem 0 0 0; }
  .bd-card-title { font-size: .95rem; font-weight: 700; color: #1B2559; margin: .1rem 0 0 0; }
  .bd-card-sub { font-size: .76rem; color: #8A94AD; margin: .1rem 0 .5rem 0; }

  /* ---- Pill status ---- */
  .bd-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin: .35rem 0 .1rem 0; }
  .bd-pill { font-size: .76rem; font-weight: 500; padding: .3rem .7rem;
             border-radius: 999px; border: 1px solid transparent; white-space: nowrap; }
  .bd-pill-alert { background: #FFF1E8; border-color: #FFD6B8; color: #9A4B06; }
  .bd-pill-info  { background: #EEF0FE; border-color: #D3D8FD; color: #3A45B8; }
  .bd-pill-ok    { background: #E6F8F1; border-color: #B6E6D3; color: #0A6E4C; }
  .bd-pill-muted { background: #F1F3FA; border-color: #E4E8F4; color: #6B7590; }

  /* ---- Daftar (gaya panel "Messages" pada referensi) ---- */
  .bd-list { display: flex; flex-direction: column; gap: .1rem; }
  .bd-row { display: flex; align-items: center; gap: .65rem; padding: .5rem .35rem;
            border-radius: 14px; }
  .bd-row:hover { background: #F7F8FD; }
  .bd-avatar { width: 34px; height: 34px; border-radius: 12px; flex: 0 0 34px;
               display: flex; align-items: center; justify-content: center;
               font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700;
               font-size: .74rem; color: #FFFFFF; }
  .bd-row-main { flex: 1 1 auto; min-width: 0; }
  .bd-row-name { font-size: .82rem; font-weight: 600; color: #1B2559;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bd-row-meta { font-size: .72rem; color: #8A94AD; }
  .bd-row-tag { font-size: .7rem; font-weight: 600; padding: .18rem .5rem;
                border-radius: 999px; background: #F1F3FA; color: #4A5578;
                white-space: nowrap; }

  /* ---- Tab ---- */
  div[data-baseweb="tab-list"] { gap: .3rem; border-bottom: 1px solid #E9ECF6; }
  button[data-baseweb="tab"] { padding: .4rem .1rem; }
  button[data-baseweb="tab"] p { font-size: .86rem; font-weight: 600; }

  /* ---- Sidebar ---- */
  section[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #EDF0F8; }
  .bd-brand-mark { width: 38px; height: 38px; border-radius: 13px; color: #FFFFFF;
                   display: flex; align-items: center; justify-content: center;
                   font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 800;
                   background: linear-gradient(140deg, #5B6BF7, #3B4BD8); }
  .bd-brand { font-weight: 800; font-size: .98rem; color: #1B2559; margin-top: .5rem; }
  .bd-brand-sub { font-size: .74rem; color: #8A94AD; margin-top: -.15rem; }
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)


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
    body = "".join(
        f'<span class="bd-pill bd-pill-{kind}">{html.escape(text)}</span>'
        for kind, text in items
    )
    st.markdown(f'<div class="bd-pills">{body}</div>', unsafe_allow_html=True)


def card_title(title: str, subtitle: str = "") -> None:
    """Judul di dalam kartu, dipakai kalau figure-nya sendiri tanpa judul."""
    st.markdown(
        f'<p class="bd-card-title">{html.escape(title)}</p>'
        + (f'<p class="bd-card-sub">{html.escape(subtitle)}</p>' if subtitle else ""),
        unsafe_allow_html=True,
    )


def card(figure, key: str | None = None, title: str = "", subtitle: str = "") -> None:
    """Satu figure di dalam kartu putih."""
    with st.container(border=True):
        if title:
            card_title(title, subtitle)
        st.plotly_chart(
            figure, width="stretch", theme=None, config=PLOTLY_CONFIG, key=key
        )


def initials(name: str) -> str:
    parts = [part for part in str(name).split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def attention_list(rows: list[dict]) -> None:
    """Daftar ringkas bergaya panel notifikasi.

    Nama partner di-escape sebelum masuk HTML: isinya data dari spreadsheet,
    jadi tidak boleh dipercaya sebagai markup.
    """
    if not rows:
        st.markdown(
            '<p class="bd-row-meta">Tidak ada kontrak yang perlu perhatian '
            "pada periode ini.</p>",
            unsafe_allow_html=True,
        )
        return
    items = []
    for row in rows:
        items.append(
            '<div class="bd-row">'
            f'<div class="bd-avatar" style="background:{row["color"]}">'
            f'{html.escape(initials(row["name"]))}</div>'
            '<div class="bd-row-main">'
            f'<div class="bd-row-name">{html.escape(str(row["name"]))}</div>'
            f'<div class="bd-row-meta">{html.escape(str(row["meta"]))}</div>'
            "</div>"
            f'<span class="bd-row-tag">{html.escape(str(row["tag"]))}</span>'
            "</div>"
        )
    st.markdown(f'<div class="bd-list">{"".join(items)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: pilihan periode
# ---------------------------------------------------------------------------


def sidebar_controls() -> tuple[object, bool]:
    """Kontrol periode. Return (pilihan untuk periods.apply_period, minta_refresh)."""
    with st.sidebar:
        st.markdown(
            '<div class="bd-brand-mark">BD</div>'
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
        refresh = st.button("Muat ulang data", width="stretch", type="secondary")
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
            f'<p class="bd-subtitle">Periode {html.escape(frame.label)} · '
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


def kpi_row(data: dict) -> None:
    """Lima kartu KPI dengan sparkline kecil, seperti kartu statistik referensi."""
    frame, summary = data["frame"], data["summary"]
    columns = st.columns(5, gap="small")

    with columns[0]:
        st.metric(
            "Market Research",
            number(summary["total_mr"]),
            delta=f"{len(data['mr_by_pic'])} PIC terlibat",
            delta_color=KPI_COLORS["mr"],
            delta_arrow="off",
            border=True,
            chart_data=list(data["mr_by_month"]["total_mr"]),
            chart_type="area",
            help="Jumlah kemunculan nama PIC di kolom PIC FROM AIESEC "
                 "(definisi tim BD), bukan jumlah baris.",
        )
    with columns[1]:
        st.metric(
            "Active Partners",
            number(summary["active_partners"]),
            delta=f"dari {number(summary['partner_count'])} partner",
            delta_color=KPI_COLORS["partner"],
            delta_arrow="off",
            border=True,
            chart_data=data["active_trend"],
            chart_type="line",
            help="Punya dokumen LoA DAN Month End belum terlewat pada "
                 "tanggal acuan periode. Kolom Status di sheet tidak dipakai.",
        )
    with columns[2]:
        st.metric(
            "Conversion Rate",
            percent(summary["conversion_rate"]),
            delta="tahap contract signed",
            delta_color=KPI_COLORS["conversion"],
            delta_arrow="off",
            border=True,
            chart_data=list(data["conversion_monthly"]["conversion_rate"].fillna(0)),
            chart_type="bar",
            help="Diambil apa adanya dari National 1.1. Untuk periode "
                 "gabungan beberapa bulan sheet tidak menyediakannya, "
                 "jadi ditampilkan sebagai —.",
        )
    with columns[3]:
        st.metric(
            "Financial Revenue",
            charts.rupiah_compact(summary["financial_revenue"]),
            delta=f"{len(frame.df_financial)} pembayaran",
            delta_color=KPI_COLORS["financial"],
            delta_arrow="off",
            border=True,
            chart_data=list(data["financial_monthly"]["amount"]),
            chart_type="bar",
            help=f"Nilai penuh: {charts.rupiah(summary['financial_revenue'])}. "
                 "Tidak pernah dijumlahkan dengan In-Kind Value.",
        )
    with columns[4]:
        st.metric(
            "In-Kind Value",
            charts.rupiah_compact(summary["inkind_value"]),
            delta=f"{len(frame.df_inkind)} dukungan",
            delta_color=KPI_COLORS["inkind"],
            delta_arrow="off",
            border=True,
            chart_data=list(data["inkind_monthly"]["amount"]),
            chart_type="bar",
            help=f"Nilai penuh: {charts.rupiah(summary['inkind_value'])}. "
                 "Taksiran nilai dukungan non-tunai.",
        )


def alert_row(data: dict) -> None:
    """Ringkasan hal yang perlu ditindak, dalam satu baris pill.

    Kontrak yang dihitung di sini hanya partner AKTIF, supaya angkanya
    sejalan dengan panel "Perlu perhatian" di halaman Ringkasan.
    """
    frame, completeness = data["frame"], data["completeness"]
    active_expiry = metrics.get_expiry_summary(
        frame.df_partner, reference_date=frame.reference_date, active_only=True
    )
    all_expiry = data["expiry_summary"]
    items: list[tuple[str, str]] = []

    def count(summary: pd.DataFrame, category: str) -> int:
        row = summary.loc[summary["expiry_category"] == category]
        return int(row["partner_count"].iloc[0]) if not row.empty else 0

    ending = count(active_expiry, metrics.EXPIRY_THIS_MONTH)
    soon = count(active_expiry, metrics.EXPIRY_SOON)
    expired = count(all_expiry, metrics.EXPIRY_EXPIRED)
    if ending:
        items.append(("alert", f"{ending} kontrak aktif berakhir bulan ini"))
    if soon:
        items.append(("info", f"{soon} kontrak aktif berakhir dalam 1–3 bulan"))
    if expired:
        items.append(("muted", f"{expired} kontrak sudah berakhir di portofolio"))

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
        # Catatan dari periods.py bisa panjang; di pill cukup inti kalimatnya.
        short = note.split(":")[0].split("(")[0].strip()
        items.append(("muted", short[:70]))

    pills(items)


def tab_overview(data: dict) -> None:
    """Halaman utama: satu chart besar, satu kartu gelap, satu daftar."""
    main, highlight, watchlist = st.columns([2.3, 1.15, 1.3], gap="small")

    with main:
        card(
            charts.mr_monthly_area(data["mr_by_month"], title=None, description=""),
            key="ov_mr_area",
            title="Statistik Market Research",
            subtitle=f"Total {number(data['summary']['total_mr'])} MR · "
                     f"periode {data['frame'].label.lower()}",
        )

    with highlight:
        with st.container(border=True, key="hero_highlight"):
            card_title(
                "Partner aktif",
                "Punya LoA dan kontrak belum berakhir",
            )
            st.plotly_chart(
                charts.gauge_donut(
                    data["summary"]["active_partners"],
                    data["summary"]["partner_count"],
                    "partner aktif",
                    on_dark=True,
                    height=190,
                ),
                width="stretch", theme=None, config=PLOTLY_CONFIG, key="ov_gauge",
            )
        card(
            charts.revenue_comparison_bar(
                data["summary"]["financial_revenue"],
                data["summary"]["inkind_value"],
                title=None,
                description="",
            ),
            key="ov_comparison",
            title="Financial vs In-Kind",
            subtitle="Dua KPI terpisah — tidak pernah dijumlahkan",
        )

    with watchlist:
        with st.container(border=True):
            card_title("Perlu perhatian", "Kontrak terdekat berakhir")
            attention_list(data["watchlist"])

    lower_left, lower_middle, lower_right = st.columns([1.25, 1, 1], gap="small")
    with lower_left:
        card(
            charts.conversion_funnel_chart(data["funnel"], title=None, description=""),
            key="ov_funnel",
            title="Sales funnel",
            subtitle="Diambil apa adanya dari National 1.1",
        )
    with lower_middle:
        card(
            charts.expiry_summary_bar(data["expiry_summary"], title=None, description=""),
            key="ov_expiry",
            title="Status masa kontrak",
            subtitle="Kategori berbasis bulan",
        )
    with lower_right:
        card(
            charts.document_completeness_bar(
                data["completeness"], title=None, description=""
            ),
            key="ov_documents",
            title="Kelengkapan dokumen",
            subtitle="Hanya partner aktif",
        )


def tab_market_research(data: dict) -> None:
    left, right = st.columns([2, 3], gap="small")
    with left:
        card(
            charts.mr_by_pic_bar(data["mr_by_pic"], title=None, description=""),
            key="mr_pic",
            title="MR per PIC",
            subtitle=f"{len(data['mr_by_pic'])} PIC · urut terbanyak",
        )
    with right:
        card(
            charts.mr_monthly_area(data["mr_by_month"], title=None, description=""),
            key="mr_month",
            title="Tren MR per bulan",
            subtitle="Urut masa jabatan (February → January)",
        )
        with st.container(border=True):
            card_title("Rincian per PIC")
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
                height=230,
                column_config={
                    "MR": st.column_config.NumberColumn(format="%d"),
                    "Porsi (%)": st.column_config.ProgressColumn(
                        format="%.2f%%", min_value=0.0, max_value=100.0
                    ),
                },
            )


def tab_partner(data: dict) -> None:
    left, right = st.columns([1.1, 1], gap="small")
    with left:
        card(
            charts.stakeholder_donut(data["stakeholder"], title=None, description=""),
            key="pt_donut",
            title="Distribusi stakeholder",
            subtitle=f"{len(data['stakeholder'])} kelompok",
        )
    with right:
        card(
            charts.conversion_funnel_chart(data["funnel"], title=None, description=""),
            key="pt_funnel",
            title="Sales funnel conversion rate",
            subtitle="Nilai dari sheet, tidak dihitung ulang",
        )

    card(
        charts.conversion_monthly_line(
            data["conversion_monthly"], title=None, description=""
        ),
        key="pt_conv_month",
        title="Conversion rate per bulan",
        subtitle="Titik bolong = sheet belum punya angkanya",
    )

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
                data["financial_monthly"], title=None,
                color=charts.COLOR_FINANCIAL, value_label="Financial Revenue",
                description="",
            ),
            key="rv_fin_month",
            title="Financial Revenue per bulan",
            subtitle=charts.rupiah(data["summary"]["financial_revenue"]),
        )
        card(
            charts.revenue_by_partner_bar(
                data["financial_partner"], title=None,
                color=charts.COLOR_FINANCIAL, description="",
            ),
            key="rv_fin_partner",
            title="Financial Revenue per partner",
            subtitle=f"{len(data['financial_partner'])} partner menyumbang",
        )
    with right:
        card(
            charts.revenue_monthly_bar(
                data["inkind_monthly"], title=None,
                color=charts.COLOR_INKIND, value_label="In-Kind Value",
                description="",
            ),
            key="rv_ink_month",
            title="In-Kind Value per bulan",
            subtitle=charts.rupiah(data["summary"]["inkind_value"]),
        )
        card(
            charts.revenue_by_partner_bar(
                data["inkind_partner"], title=None,
                color=charts.COLOR_INKIND, description="",
            ),
            key="rv_ink_partner",
            title="In-Kind Value per partner",
            subtitle=f"{len(data['inkind_partner'])} partner menyumbang",
        )


def tab_documents(data: dict) -> None:
    left, right = st.columns(2, gap="small")
    with left:
        card(
            charts.document_completeness_bar(
                data["completeness"], title=None, description=""
            ),
            key="dc_complete",
            title="Kelengkapan dokumen partner aktif",
            subtitle="Ada vs belum ada, per jenis dokumen",
        )
    with right:
        card(
            charts.expiry_timeline_bar(
                data["expiry_detail"], title=None, description=""
            ),
            key="dc_timeline",
            title="Kontrak menurut bulan berakhir",
            subtitle="Hanya partner aktif",
        )

    card(
        charts.document_tracker_heatmap(data["tracker"], title=None, description=""),
        key="dc_tracker",
        title="Document tracker partner aktif",
        subtitle="v = dokumen ada · urut dari yang paling banyak kosong",
    )

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
# Penyiapan data untuk tampilan
# ---------------------------------------------------------------------------


def active_partner_trend(df_partner: pd.DataFrame, today: pd.Timestamp) -> list[int]:
    """Jumlah partner aktif di akhir setiap bulan masa jabatan.

    Dipakai sebagai sparkline di kartu Active Partners. Perhitungannya
    memakai preparation.apply_reference_date(), jadi definisi "aktif"
    tetap satu-satunya yang dipakai seluruh dashboard.
    """
    if df_partner is None or df_partner.empty:
        return []
    trend: list[int] = []
    for month in periods.TERM_MONTHS:
        reference = periods.period_reference_date(month, today=today)
        as_of = preparation.apply_reference_date(df_partner, reference)
        trend.append(metrics.get_active_partner_count(as_of))
    return trend


def watchlist_rows(expiry_detail: pd.DataFrame, limit: int = 6) -> list[dict]:
    """Kontrak yang paling dekat berakhir, untuk panel "Perlu perhatian"."""
    if expiry_detail is None or expiry_detail.empty:
        return []
    urgent = expiry_detail[
        expiry_detail["expiry_category"].isin(
            [metrics.EXPIRY_THIS_MONTH, metrics.EXPIRY_SOON, metrics.EXPIRY_EXPIRED]
        )
        & expiry_detail["is_active"]
    ]
    if urgent.empty:
        urgent = expiry_detail[expiry_detail["is_active"]]

    rows: list[dict] = []
    for item in urgent.head(limit).itertuples():
        remaining = item.months_remaining
        if pd.isna(remaining):
            tag = "tanpa data"
        elif int(remaining) == 0:
            tag = "bulan ini"
        elif int(remaining) < 0:
            tag = f"{abs(int(remaining))} bln lewat"
        else:
            tag = f"{int(remaining)} bln lagi"
        rows.append(
            {
                "name": item.partner_name,
                "meta": f"{item.stakeholder or 'Tanpa stakeholder'} · "
                        f"berakhir {item.end_month_raw or '-'}",
                "tag": tag,
                "color": charts.EXPIRY_COLORS.get(
                    item.expiry_category, charts.PRIMARY
                ),
            }
        )
    return rows


def build_view_data(frame: periods.PeriodFrames, today: pd.Timestamp) -> dict:
    """Semua angka & tabel yang dipakai halaman, semuanya lewat metrics.py."""
    summary = metrics.get_kpi_summary(
        frame.df_mr, frame.df_partner, frame.df_conversion,
        frame.df_financial, frame.df_inkind,
        scope=frame.conversion_scope or "",
    )
    expiry_detail = metrics.get_partnership_expiry(
        frame.df_partner, reference_date=frame.reference_date
    )
    return {
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
        "financial_partner": metrics.get_revenue_by_partner(frame.df_financial, "revenue"),
        "inkind_partner": metrics.get_revenue_by_partner(frame.df_inkind, "inkind_value"),
        "completeness": metrics.get_document_completeness(frame.df_partner),
        "tracker": metrics.get_document_tracker(frame.df_partner),
        "expiry_summary": metrics.get_expiry_summary(
            frame.df_partner, reference_date=frame.reference_date
        ),
        "expiry_detail": expiry_detail,
        "active_trend": active_partner_trend(frame.df_partner, today),
        "watchlist": watchlist_rows(expiry_detail),
    }


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
    data = build_view_data(frame, today)

    header(frame, pd.Timestamp.now())
    kpi_row(data)
    alert_row(data)

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
