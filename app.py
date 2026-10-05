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

ARAH DESAIN
    Dashboard dark-navy dengan lapisan gradien biru, panel transparan,
    batas blue-gray, dan aksen biru/cyan yang terfokus.

CATATAN KEAMANAN
    Data partner asli hanya untuk Member Login. Tampilan Anonymous memakai
    masking dan tab S&D tidak memuat konfigurasi atau bukti asli.
    (login Streamlit Cloud / SSO / reverse proxy). Lihat Phase 13.

Cara menjalankan:
    .venv/Scripts/streamlit.exe run app.py
"""

from __future__ import annotations

import html
import textwrap

import pandas as pd
import streamlit as st

from src import charts, metrics, periods, preparation, sheets, snd_tracker
from src.google_drive_service import DriveServiceError
from src.snd_matching import requirement_signature
from src.snd_security import may_access_real_snd
from src.snd_store import SNDStoreError, get_store, partner_key

st.set_page_config(
    page_title="BD Analytics — AIESEC in BINUS",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Perbaikan font ikon — WAJIB disisipkan ke SETIAP blok <style> dashboard
# ---------------------------------------------------------------------------
#
# MASALAH YANG DIPERBAIKI
#   Streamlit menggambar ikon sebagai LIGATUR TEKS, bukan sebagai gambar:
#
#       <span data-testid="stIconMaterial">keyboard_double_arrow_left</span>
#
#   Span itu hanya berubah menjadi panah kalau font "Material Symbols
#   Rounded" yang dipakai untuk merendernya.
#
#   Dashboard ini punya aturan font global dengan selektor [class*="st-"].
#   Kelas emotion milik Streamlit berbentuk "st-emotion-cache-..." sehingga
#   IKUT COCOK dengan selektor itu. Karena CSS kita disuntikkan setelah CSS
#   Streamlit dan spesifisitasnya sama, font ikon tertimpa font teks biasa.
#   Akibatnya yang tampil justru NAMA ikonnya sebagai tulisan:
#
#       keyboard_double_arrow_left / keyboard_double_arrow_right
#           -> tombol buka/tutup sidebar
#       keyboard_arrow_down / keyboard_arrow_right
#           -> panah expander dan panah geser tab
#       info, warning, help
#           -> ikon st.warning, st.info, dan ikon bantuan pada kartu KPI
#       fullscreen, download, search
#           -> toolbar yang muncul saat kursor melewati grafik dan tabel
#
#   Itulah "tulisan aneh yang muncul saat hover" - bukan teks ganda,
#   melainkan ikon yang gagal menjadi ikon.
#
# CARA PERBAIKANNYA
#   Font ikon dipasang ulang secara eksplisit. Selektornya memakai
#   data-testid (dan kelas target emotion sebagai cadangan) supaya tepat
#   sasaran, dan !important dipakai SENGAJA: aturan ini memang harus
#   menang melawan aturan font global mana pun, sekarang maupun nanti.
ICON_FONT_CSS = """
  [data-testid="stIconMaterial"],
  [data-testid="stAlertDynamicIcon"],
  [data-testid="stToastDynamicIcon"],
  [data-testid="stHeadingIcon"],
  [data-testid="stMetricIcon"],
  [data-testid="stExpanderIcon"],
  [data-testid="stTooltipIcon"] svg,
  span.e1vmumty0,
  .material-symbols-rounded,
  .material-symbols-outlined {
      font-family: 'Material Symbols Rounded' !important;
      font-weight: normal !important;
      font-style: normal !important;
      letter-spacing: normal !important;
      text-transform: none !important;
      white-space: nowrap !important;
      word-wrap: normal !important;
      direction: ltr !important;
      font-feature-settings: 'liga' !important;
      -moz-font-feature-settings: 'liga' !important;
      -webkit-font-feature-settings: 'liga' !important;
      -webkit-font-smoothing: antialiased;
  }

  /* Ikon di dalam tombol ikut kursor tombolnya, bukan kursor teks. */
  button [data-testid="stIconMaterial"],
  [role="button"] [data-testid="stIconMaterial"],
  summary [data-testid="stIconMaterial"],
  button span.e1vmumty0,
  [role="button"] span.e1vmumty0 {
      cursor: pointer;
  }
"""

# =========================================================
# ACCESS CONTROL
# =========================================================

ENTRY_DARK_STYLE = """
<style>
  .stApp {
    background: radial-gradient(ellipse 65% 50% at 50% 7%, rgba(40,102,202,.26), transparent 75%),
                linear-gradient(145deg, #09172b, #07111f 72%) !important;
    color: #e7efff !important;
  }
  .landing-badge { background: #1b3556 !important; color: #a7caff !important;
                   border-color: #3c628f !important; }
  .access-card, div[class*="st-key-login_card"] {
    background: linear-gradient(145deg, #192e4b, #112139) !important;
    border: 1px solid #315174 !important;
    box-shadow: 0 20px 45px rgba(1,8,25,.35) !important;
  }
  .access-card h3, .login-title, .stApp h1 { color: #edf4ff !important; }
  .access-card p, .login-sub, .login-foot { color: #b5c6df !important; }
  .access-icon, .login-mark { background: #254c7f !important; color: #dcecff !important; }
  .login-eyebrow, .login-field-label { color: #8fbcff !important; }
  [data-testid="stButton"] > button {
    background: #1e3a60 !important; color: #d5e7ff !important;
    border-color: #3c6190 !important;
  }
  [data-testid="stButton"] > button:hover { background: #285992 !important; color: #fff !important; }
  div[class*="st-key-login_submit"] button {
    background: linear-gradient(110deg, #2464c5, #3988ed) !important;
    color: #fff !important;
  }
  div[class*="st-key-login_card"] [data-testid="stTextInputRootElement"] {
    background: #142740 !important; border-color: #37577c !important;
  }
</style>
"""

if "access_mode" not in st.session_state:
    st.session_state.access_mode = None


# ---------- LANDING PAGE ----------
# ---------- LANDING PAGE ----------
# ---------- LANDING PAGE ----------
# ---------- LANDING PAGE ----------
if st.session_state.access_mode is None:

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@500;600;700;800&family=Manrope:wght@400;500;600;700&display=swap');

        .stApp {
            background:
                radial-gradient(circle at 10% 15%, rgba(91,107,247,.22), transparent 35%),
                radial-gradient(circle at 90% 80%, rgba(93,173,255,.18), transparent 35%),
                linear-gradient(135deg, #E8EDFF 0%, #F7F9FF 48%, #FFFFFF 72%, #EDF4FF 100%);
            min-height: 100vh;
        }

        html, body, [class*="st-"], button, input {
            font-family: 'Manrope', sans-serif;
        }

        .block-container {
            max-width: 1050px;
            padding-top: 5rem;
            padding-bottom: 3rem;
        }

        header[data-testid="stHeader"] {
            background: transparent;
        }

        #MainMenu {
            visibility: hidden;
        }

        footer {
            visibility: hidden;
        }

        .landing-badge {
            display: inline-block;
            padding: 8px 16px;
            background: rgba(255,255,255,.7);
            border: 1px solid rgba(91,107,247,.15);
            border-radius: 999px;
            color: #5362D8;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: .1em;
            box-shadow: 0 8px 25px rgba(47,59,140,.07);
        }

        .access-card {
            min-height: 215px;
            padding: 30px;
            background: rgba(255,255,255,.82);
            border: 1px solid rgba(255,255,255,.9);
            border-radius: 25px;
            box-shadow: 0 20px 55px rgba(42,54,125,.10);
            backdrop-filter: blur(18px);
        }

        .access-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 50px;
            height: 50px;
            border-radius: 16px;
            background: linear-gradient(135deg,#EEF1FF,#E0E6FF);
            font-size: 22px;
            margin-bottom: 18px;
        }

        [data-testid="stButton"] > button {
            height: 50px;
            margin-top: 8px;
            border-radius: 14px;
            border: 1px solid rgba(91,107,247,.15);
            background: rgba(255,255,255,.85);
            color: #4655CE;
            font-weight: 700;
            box-shadow: 0 7px 20px rgba(53,65,150,.07);
            transition: all .2s ease;
        }

        [data-testid="stButton"] > button:hover {
            transform: translateY(-2px);
            background: linear-gradient(110deg,#5969EA,#7381FA);
            color: white;
            border-color: transparent;
            box-shadow: 0 13px 28px rgba(77,92,220,.23);
        }

        /* Audit kursor: kartu dan teks di halaman ini bukan elemen yang
           bisa ditekan, jadi kursornya harus tetap kursor biasa. */
        .stApp, .block-container, h1, h2, h3, p, span, div,
        .landing-badge, .access-card, .access-card *, .access-icon,
        [data-testid="stMarkdownContainer"],
        [data-testid="stMarkdownContainer"] * { cursor: default; }
        button, [role="button"], a[href],
        [data-testid="stButton"] > button,
        [data-testid="stButton"] > button * { cursor: pointer; }
        [data-testid="stHeadingWithActionElements"] a,
        [data-testid="stHeaderActionElements"] { display: none !important; }
        """
        + ICON_FONT_CSS
        + """
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ==========================
    # HEADER
    # ==========================

    st.markdown(ENTRY_DARK_STYLE, unsafe_allow_html=True)

    st.markdown(
        '<p style="text-align:center; margin:0 0 20px 0;">'
        '<span class="landing-badge">● &nbsp; AIESEC IN BINUS · BUSINESS DEVELOPMENT</span>'
        '</p>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<h1 style="'
        "font-family:'DM Sans',sans-serif;"
        'font-size:60px;'
        'font-weight:800;'
        'letter-spacing:-3px;'
        'text-align:center;'
        'color:#26358C;'
        'margin:0 0 14px 0;'
        '">'
        'BD Analytics'
        '</h1>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p style="'
        'text-align:center;'
        'max-width:650px;'
        'margin:0 auto 45px auto;'
        'color:#75809B;'
        'font-size:15px;'
        'line-height:1.8;'
        '">'
        'A centralized analytics workspace for monitoring partnership '
        'performance, market research, conversion, revenue, and document insights.'
        '</p>',
        unsafe_allow_html=True,
    )

    # ==========================
    # ACCESS CARDS
    # ==========================

    space1, public_col, member_col, space2 = st.columns(
        [0.12, 1, 1, 0.12],
        gap="medium",
    )

    # PUBLIC
    with public_col:

        public_html = (
            '<div class="access-card">'
            '<span class="access-icon">👤</span>'
            '<h3 style="'
            "font-family:'DM Sans',sans-serif;"
            'color:#202C5C;'
            'font-size:20px;'
            'font-weight:700;'
            'margin:0 0 9px 0;'
            '">'
            'Public Access'
            '</h3>'
            '<p style="'
            'color:#7D87A1;'
            'font-size:13px;'
            'line-height:1.7;'
            'margin:0;'
            '">'
            'Explore the portfolio version of BD Analytics. '
            'Sensitive partnership information is automatically '
            'anonymized for public viewing.'
            '</p>'
            '</div>'
        )

        st.markdown(
            public_html,
            unsafe_allow_html=True,
        )

        if st.button(
            "Explore Dashboard  →",
            use_container_width=True,
            key="public_access",
        ):
            st.session_state.access_mode = "anonymous"
            st.rerun()

    # MEMBER
    with member_col:

        member_html = (
            '<div class="access-card">'
            '<span class="access-icon">🔐</span>'
            '<h3 style="'
            "font-family:'DM Sans',sans-serif;"
            'color:#202C5C;'
            'font-size:20px;'
            'font-weight:700;'
            'margin:0 0 9px 0;'
            '">'
            'AIESEC Member'
            '</h3>'
            '<p style="'
            'color:#7D87A1;'
            'font-size:13px;'
            'line-height:1.7;'
            'margin:0;'
            '">'
            'Sign in to access complete partnership metrics, '
            'financial insights, contract information, '
            'and internal document tracking.'
            '</p>'
            '</div>'
        )

        st.markdown(
            member_html,
            unsafe_allow_html=True,
        )

        if st.button(
            "Member Login  →",
            use_container_width=True,
            key="member_access",
        ):
            st.session_state.access_mode = "login"
            st.rerun()

    st.markdown(
        '<p style="'
        'text-align:center;'
        'color:#8F99B0;'
        'font-size:11px;'
        'margin-top:30px;'
        '">'
        '🟢 &nbsp; Internal partnership data is protected'
        '</p>',
        unsafe_allow_html=True,
    )

    st.stop()

# ---------- MEMBER LOGIN ----------
if st.session_state.access_mode == "login":

    # Gaya khusus halaman login. Tetap biru-putih seperti dashboard, tapi
    # lebih lapang karena hanya ada satu kartu di layar.
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');

        html, body, [class*="st-"], button, input { font-family: 'Inter', sans-serif; }

        .stApp {
            background:
                radial-gradient(circle at 12% 12%, rgba(91,107,247,.16), transparent 38%),
                radial-gradient(circle at 88% 85%, rgba(46,155,240,.13), transparent 38%),
                linear-gradient(140deg, #EDF2FF 0%, #F8FAFF 46%, #FFFFFF 74%, #EEF4FF 100%);
            min-height: 100vh;
        }
        header[data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer { visibility: hidden; }
        .block-container { max-width: 980px; padding-top: 4.5rem; padding-bottom: 3rem; }

        /* Kartu login: satu container Streamlit yang di-styling, supaya
           input password tetap widget asli Streamlit (bukan HTML palsu).

           Dua bentuk markup didukung sekaligus. Streamlit versi lama
           membungkus container ber-border dengan
           stVerticalBlockBorderWrapper; versi yang dipakai sekarang
           memasang border langsung pada elemen yang membawa kelas
           st-key-<key>. Menyebut keduanya membuat gaya ini tidak diam-diam
           berhenti bekerja saat Streamlit diperbarui. */
        div[class*="st-key-login_card"] [data-testid="stVerticalBlockBorderWrapper"],
        div[class*="st-key-login_card"] {
            background: rgba(255,255,255,.92);
            border: 1px solid #E2E9FB;
            border-radius: 24px;
            box-shadow: 0 18px 46px rgba(31, 45, 112, .10);
            padding: 1.9rem 1.9rem 1.5rem 1.9rem;
            backdrop-filter: blur(14px);
        }

        .login-mark {
            display: inline-flex; align-items: center; justify-content: center;
            width: 46px; height: 46px; border-radius: 15px;
            background: linear-gradient(140deg, #5B6BF7, #3B4BD8);
            color: #FFFFFF; font-size: 20px;
            box-shadow: 0 8px 20px rgba(59, 75, 216, .26);
        }
        .login-eyebrow {
            /* block, bukan inline-block: kalau inline, teks ini berdiri di
               samping lambang kunci dan saling menimpa. */
            display: block; margin-top: .9rem;
            font-size: .66rem; font-weight: 800; letter-spacing: .14em;
            text-transform: uppercase; color: #5B6BF7;
        }
        .login-title {
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 1.6rem; font-weight: 800; color: #1B2559;
            letter-spacing: -.02em; margin: .3rem 0 .35rem 0;
        }
        .login-sub {
            font-size: .86rem; color: #75809B; line-height: 1.65;
            margin: 0 0 1.35rem 0;
        }
        .login-field-label {
            font-size: .72rem; font-weight: 700; letter-spacing: .09em;
            text-transform: uppercase; color: #8A94AD; margin: 0 0 .35rem .15rem;
        }
        .login-foot {
            display: flex; align-items: center; justify-content: center; gap: .4rem;
            margin-top: 1.4rem; font-size: .74rem; color: #97A1B8;
        }
        .login-foot-dot {
            width: 7px; height: 7px; border-radius: 50%; background: #1F7AE0;
            box-shadow: 0 0 0 3px rgba(31,122,224,.16);
        }

        /* Input password.
           stTextInputRootElement adalah pembungkus input yang benar-benar
           ada di DOM; data-baseweb sudah tidak dipakai lagi oleh Streamlit,
           jadi keduanya disebut agar aman di dua versi. */
        div[class*="st-key-login_card"] div[data-baseweb="input"],
        div[class*="st-key-login_card"] [data-testid="stTextInputRootElement"] {
            border-radius: 14px !important;
            border: 1px solid #DCE3F7 !important;
            background: #FBFCFF !important;
        }
        div[class*="st-key-login_card"] div[data-baseweb="input"]:focus-within,
        div[class*="st-key-login_card"] [data-testid="stTextInputRootElement"]:focus-within {
            border-color: #8492FB !important;
            box-shadow: 0 0 0 4px rgba(91,107,247,.12);
        }
        div[class*="st-key-login_card"] input { height: 46px; font-size: .9rem; }

        /* Tombol utama & tombol kembali */
        div[class*="st-key-login_submit"] button {
            height: 46px; margin-top: .55rem;
            border-radius: 14px; border: none;
            background: linear-gradient(110deg, #5B6BF7, #3B4BD8);
            color: #FFFFFF; font-weight: 700; font-size: .88rem;
            box-shadow: 0 10px 24px rgba(59, 75, 216, .24);
            transition: transform .18s ease, box-shadow .18s ease;
        }
        div[class*="st-key-login_submit"] button:hover {
            transform: translateY(-1px);
            box-shadow: 0 14px 30px rgba(59, 75, 216, .30);
        }
        div[class*="st-key-login_back"] button {
            height: 40px; margin-top: .35rem;
            border-radius: 12px; border: 1px solid transparent;
            background: transparent; color: #7D87A1;
            font-weight: 600; font-size: .8rem;
        }
        div[class*="st-key-login_back"] button:hover {
            background: #F2F5FE; color: #3B4BD8;
        }

        /* Audit kursor untuk halaman ini juga. */
        .stApp, .block-container, h1, h2, h3, p, span, div,
        [data-testid="stMarkdownContainer"],
        [data-testid="stMarkdownContainer"] *,
        [data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stAlert"], [data-testid="stAlert"] * { cursor: default; }
        button, [role="button"], a[href], [data-testid="stButton"] > button,
        [data-testid="stButton"] > button * { cursor: pointer; }
        input[type="password"], input[type="text"] { cursor: text; }
        [data-testid="stHeadingWithActionElements"] a,
        [data-testid="stHeaderActionElements"] { display: none !important; }
        """
        + ICON_FONT_CSS
        + """
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(ENTRY_DARK_STYLE, unsafe_allow_html=True)

    def _member_password() -> str | None:
        """Ambil MEMBER_PASSWORD dari Streamlit Secrets.

        Hanya mengembalikan nilainya untuk dibandingkan di memori. Nilainya
        TIDAK PERNAH ditulis ke UI, ke log, maupun ke pesan error - kalau
        belum dikonfigurasi, yang muncul hanya keterangan bahwa login belum
        disiapkan.
        """
        try:
            secret = st.secrets["MEMBER_PASSWORD"]
        except (KeyError, FileNotFoundError):
            return None
        return str(secret)

    left_gap, login_column, right_gap = st.columns([1, 1.7, 1], gap="small")

    with login_column:
        with st.container(border=True, key="login_card"):
            st.markdown(
                '<span class="login-mark">&#128274;</span>'
                '<span class="login-eyebrow">Internal Access</span>'
                '<p class="login-title">AIESEC Member Access</p>'
                '<p class="login-sub">Enter your member password to access the '
                'full dashboard.</p>'
                '<p class="login-field-label">Member password</p>',
                unsafe_allow_html=True,
            )

            password = st.text_input(
                "Member password",
                type="password",
                # Bukan deretan titik: placeholder berisi titik-titik terbaca
                # seperti kolom yang sudah terisi.
                placeholder="Enter password",
                label_visibility="collapsed",
                key="member_password_input",
            )

            submitted = st.button(
                "Access Dashboard",
                width="stretch",
                key="login_submit",
            )
            went_back = st.button(
                "Back to access options",
                width="stretch",
                key="login_back",
            )

            if submitted:
                expected = _member_password()
                if expected is None:
                    st.error(
                        "Member login belum dikonfigurasi. Hubungi pengelola "
                        "dashboard untuk mengaktifkannya."
                    )
                elif password == expected:
                    st.session_state.access_mode = "member"
                    st.rerun()
                else:
                    st.error("Password tidak sesuai. Silakan coba lagi.")

            if went_back:
                st.session_state.access_mode = None
                st.rerun()

        st.markdown(
            '<div class="login-foot"><span class="login-foot-dot"></span>'
            'Internal partnership data is protected</div>',
            unsafe_allow_html=True,
        )

    st.stop()


# ---------- ACCESS STATUS ----------
IS_MEMBER = st.session_state.access_mode == "member"

def protected(value: str) -> str:
    return value if IS_MEMBER else "XXX"

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


# ---------------------------------------------------------------------------
# Gaya
# ---------------------------------------------------------------------------

STYLE = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@400;500;600&display=swap');

  html, body, [class*="st-"], button, input, select, textarea {
      font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  }
  h1, h2, h3, h4, .bd-title, .bd-card-title, [data-testid="stMetricValue"] {
      font-family: 'Plus Jakarta Sans', 'Inter', system-ui, sans-serif;
  }

  .stApp { background: #F4F6FC; }
  /* Toolbar Streamlit dibuat transparan supaya judul halaman tidak tertutup. */
  header[data-testid="stHeader"] { background: transparent; }
  .block-container { padding-top: 3rem; padding-bottom: 2.2rem; max-width: 1560px; }

  /* ---- Kartu ---- */
  /* Dua bentuk markup didukung sekaligus:
       Streamlit lama : st.container(border=True) dibungkus
                        stVerticalBlockBorderWrapper
       Streamlit kini : border dipasang langsung pada elemen yang membawa
                        kelas st-key-<key>
     Karena itu setiap kartu diberi key berawalan "bdcard" (lihat helper
     card()), sehingga hook-nya adalah parameter key yang memang bagian
     dari API Streamlit - bukan kelas emotion yang berubah setiap build. */
  [data-testid="stVerticalBlockBorderWrapper"],
  div[class*="st-key-bdcard"] {
      background: #FFFFFF;
      border: 1px solid #EDF0F8;
      border-radius: 20px;
      box-shadow: 0 6px 20px rgba(27, 37, 89, 0.05);
      padding: .25rem .35rem;
  }
  /* Kartu section punya isi padat, jadi paddingnya sedikit lebih lega. */
  div[class*="st-key-bdcard_section"] {
      padding: .9rem 1.1rem 1rem 1.1rem;
  }

  /* ---- Kartu KPI ---- */
  [data-testid="stMetric"] {
      background: #FFFFFF;
      border: 1px solid #EDF0F8;
      border-radius: 20px;
      box-shadow: 0 6px 20px rgba(27, 37, 89, 0.05);
      padding: 14px 16px 10px 16px;
  }
  [data-testid="stMetricLabel"] p {
      font-size: .72rem; font-weight: 600; letter-spacing: .08em;
      text-transform: uppercase; color: #8A94AD;
  }
  /* Label metric boleh turun baris. Section Completed Partnership memakai
     lima kartu berdampingan, dan tanpa ini nama seperti
     "Post-Partnership Completion" terpotong menjadi "Post-Partnership Co...". */
  [data-testid="stMetricLabel"],
  [data-testid="stMetricLabel"] p,
  [data-testid="stMetricDelta"],
  [data-testid="stMetricDelta"] div {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
  }
  [data-testid="stMetricValue"] {
      font-size: 1.75rem; font-weight: 800; color: #1B2559; line-height: 1.15;
  }
  [data-testid="stMetricDelta"] { font-size: .74rem; color: #8A94AD; font-weight: 500; }

  /* ---- Kartu gelap (penarik mata, mengikuti referensi) ---- */
  .st-key-hero_highlight [data-testid="stVerticalBlockBorderWrapper"],
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
  .bd-row { display: flex; align-items: center; gap: .6rem; padding: .5rem .35rem;
            border-radius: 14px; }
  .bd-row:hover { background: #F7F8FD; }
  .bd-avatar { width: 34px; height: 34px; border-radius: 12px;
               flex: 0 0 34px; display: flex; align-items: center;
               justify-content: center;
               font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700;
               font-size: .74rem; color: #FFFFFF; }
  /* min-width: 0 wajib ada: tanpa itu teks panjang menolak menyusut dan
     menabrak tag durasi di kanan. */
  .bd-row-main { flex: 1 1 auto; min-width: 0; overflow: hidden; }
  .bd-row-name, .bd-row-meta {
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      display: block; max-width: 100%;
  }
  .bd-row-name { font-size: .82rem; font-weight: 600; color: #1B2559;
                 line-height: 1.35; }
  .bd-row-meta { font-size: .72rem; color: #8A94AD; line-height: 1.35; }
  .bd-row-tag { flex: 0 0 auto; font-size: .7rem; font-weight: 600;
                padding: .18rem .5rem; border-radius: 999px;
                background: #EEF0FE; color: #3A45B8; white-space: nowrap; }

  /* ---- Tab ---- */
  /* Streamlit tidak lagi memasang data-baseweb. Tab sekarang berupa
     div[role="tab"][data-testid="stTab"]; kedua bentuk disebut agar aman. */
  div[data-baseweb="tab-list"],
  [data-testid="stTabs"] > div:first-child {
      gap: .3rem; border-bottom: 1px solid #E9ECF6;
  }
  button[data-baseweb="tab"],
  [data-testid="stTab"] { padding: .4rem .1rem; }
  button[data-baseweb="tab"] p,
  [data-testid="stTab"] p { font-size: .86rem; font-weight: 600; }

  /* ---- Sidebar ---- */
  section[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #EDF0F8; }
  .bd-brand-mark { width: 38px; height: 38px; border-radius: 13px; color: #FFFFFF;
                   display: flex; align-items: center; justify-content: center;
                   font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 800;
                   background: linear-gradient(140deg, #5B6BF7, #3B4BD8); }
  .bd-brand { font-weight: 800; font-size: .98rem; color: #1B2559; margin-top: .5rem; }
  .bd-brand-sub { font-size: .74rem; color: #8A94AD; margin-top: -.15rem; }

  /* ---- Sidebar: kelompok kontrol ---- */
  /* Setiap kelompok punya judul kecil + permukaan biru sangat muda, supaya
     sidebar terbaca sebagai satu panel bertingkat, bukan tumpukan widget. */
  section[data-testid="stSidebar"] .block-container,
  section[data-testid="stSidebar"] > div { padding-top: 1.1rem; }

  .bd-side-group { display: flex; align-items: baseline; gap: .4rem;
                   margin: 1.05rem .1rem .45rem .1rem; }
  .bd-side-group-title { font-family: 'Plus Jakarta Sans', sans-serif;
                         font-size: .68rem; font-weight: 800; letter-spacing: .13em;
                         text-transform: uppercase; color: #3B4BD8; }
  .bd-side-group-line { flex: 1 1 auto; height: 1px;
                        background: linear-gradient(90deg, #D8DEFB, rgba(216,222,251,0)); }

  section[data-testid="stSidebar"] div[class*="st-key-side_"]
      [data-testid="stVerticalBlockBorderWrapper"],
  section[data-testid="stSidebar"] div[class*="st-key-side_"] {
      background: linear-gradient(180deg, #F7F9FF 0%, #FFFFFF 100%);
      border: 1px solid #E4EAFD;
      border-radius: 16px;
      box-shadow: 0 3px 12px rgba(27, 37, 89, 0.04);
      padding: .55rem .7rem .65rem .7rem;
  }
  section[data-testid="stSidebar"] label p { font-size: .76rem; font-weight: 600;
                                             color: #6B7590; }
  section[data-testid="stSidebar"] [data-testid="stButton"] > button {
      border-radius: 12px; font-weight: 700; font-size: .8rem;
      border: 1px solid #D8DEFB; background: #FFFFFF; color: #3B4BD8;
  }
  section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
      background: #EEF1FE; border-color: #B9CEFC; color: #2F3BAF;
  }

  /* ---- Baris akses di sidebar ---- */
  .bd-access { display: flex; align-items: center; gap: .55rem; }
  .bd-access-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; }
  .bd-access-dot-member { background: #1F7AE0; box-shadow: 0 0 0 3px rgba(31,122,224,.16); }
  .bd-access-dot-anon { background: #8FA0C4; box-shadow: 0 0 0 3px rgba(143,160,196,.16); }
  .bd-access-main { min-width: 0; }
  .bd-access-name { font-size: .8rem; font-weight: 700; color: #1B2559; line-height: 1.3; }
  .bd-access-note { font-size: .7rem; color: #8A94AD; line-height: 1.3; }

  /* ---- Judul section di dalam tab ---- */
  .bd-section { margin: .35rem 0 .55rem 0; }
  .bd-section-title { font-family: 'Plus Jakarta Sans', sans-serif;
                      font-size: 1.05rem; font-weight: 800; color: #1B2559;
                      margin: 0; letter-spacing: -.01em; }
  .bd-section-sub { font-size: .78rem; color: #8A94AD; margin: .15rem 0 0 0; }

  /* ---- Badge status (Completed / Missing / Not Required Yet) ---- */
  .bd-badge { display: inline-flex; align-items: center; gap: .3rem;
              font-size: .72rem; font-weight: 700; letter-spacing: .01em;
              padding: .22rem .6rem; border-radius: 999px;
              border: 1px solid transparent; white-space: nowrap; }
  .bd-badge-done    { background: #E8F3FE; border-color: #B6D7F8; color: #1257A0; }
  .bd-badge-missing { background: #FFF2E9; border-color: #FFD2B3; color: #9A4B06; }
  .bd-badge-wait    { background: #F1F3FA; border-color: #E0E5F3; color: #6B7590; }
  .bd-badge-none    { background: #F7F8FC; border-color: #E9ECF6; color: #96A0B8; }
  .bd-badge-mark { font-weight: 800; }

  /* ---- Baris tracker post-partnership ---- */
  .bd-track { display: flex; flex-direction: column; gap: .3rem; margin-top: .25rem; }
  .bd-track-head, .bd-track-row {
      display: grid; grid-template-columns: minmax(0, 2.1fr) minmax(0, 1fr)
                                            minmax(0, 1.15fr) minmax(0, 1.15fr);
      gap: .5rem; align-items: center;
  }
  .bd-track-head { padding: 0 .7rem .25rem .7rem; }
  .bd-track-head span { font-size: .68rem; font-weight: 800; letter-spacing: .09em;
                        text-transform: uppercase; color: #96A0B8; }
  .bd-track-row { padding: .55rem .7rem; border-radius: 14px;
                  background: #FBFCFF; border: 1px solid #EDF0F8; }
  .bd-track-row-full { background: linear-gradient(90deg, #F3F8FE, #FBFCFF 65%);
                       border-color: #DCE9FA; }
  /* Setiap sel grid dibuat bisa menyusut, dan nama + keterangan ditumpuk.
     Tanpa display:block keduanya berdempetan dalam satu baris sehingga
     nama partner dan stakeholder terbaca menyambung tanpa spasi.
     Catatan: komentar di blok ini ikut terkirim ke browser, jadi jangan
     pernah menulis nama partner asli di sini. */
  .bd-track-row > span { min-width: 0; }
  .bd-track-name { display: block; font-size: .83rem; font-weight: 700;
                   color: #1B2559; overflow: hidden; text-overflow: ellipsis;
                   white-space: nowrap; }
  .bd-track-meta { display: block; font-size: .71rem; color: #8A94AD;
                   overflow: hidden; text-overflow: ellipsis;
                   white-space: nowrap; }
  .bd-track-date { font-size: .76rem; color: #4A5573; font-weight: 600; }

  /* ---- Kartu partner yang segera berakhir ---- */
  /* auto-fit + minmax: jumlah kolom menyesuaikan lebar layar tanpa media
     query, jadi kartu tetap terbaca di layar sempit maupun lebar. */
  .bd-exp-grid { display: grid; gap: .6rem; margin-top: .3rem;
                 grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
  .bd-exp-card { position: relative; overflow: hidden;
                 padding: .8rem .9rem .85rem 1.05rem;
                 background: linear-gradient(135deg, #FFFFFF 0%, #F8FAFF 100%);
                 border: 1px solid #E4EAFD; border-radius: 18px;
                 box-shadow: 0 4px 16px rgba(27, 37, 89, 0.05); }
  .bd-exp-card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0;
                         width: 4px; background: #B9CEFC; }
  .bd-exp-critical { background: linear-gradient(135deg, #FFFFFF 0%, #EEF4FF 100%);
                     border-color: #C3D6FB; }
  .bd-exp-critical::before { background: linear-gradient(180deg, #3B4BD8, #16357E); }
  .bd-exp-warning { border-color: #D3DEFB; }
  .bd-exp-warning::before { background: linear-gradient(180deg, #5B6BF7, #3B4BD8); }
  .bd-exp-normal::before { background: #B9CEFC; }
  .bd-exp-top { display: flex; align-items: flex-start; justify-content: space-between;
                gap: .5rem; }
  /* Nama partner boleh turun ke baris kedua. Memotong di tengah kata
     ("DMAC Chicke...") membuat partner sulit dikenali, sedangkan dua baris
     masih rapi dan tinggi kartu tetap seragam karena dibatasi clamp. */
  .bd-exp-name { font-family: 'Plus Jakarta Sans', sans-serif; font-size: .92rem;
                 font-weight: 800; color: #1B2559; line-height: 1.3;
                 min-width: 0; white-space: normal; overflow: hidden;
                 display: -webkit-box; -webkit-line-clamp: 2;
                 -webkit-box-orient: vertical; }
  .bd-exp-pic { font-size: .72rem; color: #8A94AD; margin-top: .1rem;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bd-exp-label { font-size: .66rem; font-weight: 700; letter-spacing: .1em;
                  text-transform: uppercase; color: #96A0B8; margin-top: .65rem; }
  .bd-exp-date { font-family: 'Plus Jakarta Sans', sans-serif; font-size: .95rem;
                 font-weight: 800; color: #22306B; line-height: 1.25; }
  .bd-exp-days { font-size: .76rem; font-weight: 700; margin-top: .2rem; }
  .bd-exp-days-critical { color: #16357E; }
  .bd-exp-days-warning { color: #3B4BD8; }
  .bd-exp-days-normal { color: #6B7590; }
  .bd-exp-tag { flex: 0 0 auto; font-size: .64rem; font-weight: 800;
                letter-spacing: .06em; text-transform: uppercase;
                padding: .2rem .5rem; border-radius: 999px; white-space: nowrap; }
  .bd-exp-tag-critical { background: #E3ECFD; color: #16357E; }
  .bd-exp-tag-warning  { background: #EEF1FE; color: #3B4BD8; }
  .bd-exp-tag-normal   { background: #F1F3FA; color: #6B7590; }

  /* ---- Empty state ---- */
  .bd-empty { padding: 1.1rem .9rem; border-radius: 16px; text-align: center;
              background: #FBFCFF; border: 1px dashed #DDE3F1; }
  .bd-empty-title { font-size: .84rem; font-weight: 700; color: #4A5573; }
  .bd-empty-note { font-size: .74rem; color: #8A94AD; margin-top: .2rem; }

  /* ---- Responsif ---- */
  /* Di layar sempit kolom tabel status ditumpuk dan kartu jadi satu kolom,
     supaya nama partner tidak terpotong menjadi tidak terbaca. */
  @media (max-width: 860px) {
    .bd-track-head { display: none; }
    .bd-track-row { grid-template-columns: 1fr; gap: .25rem;
                    padding: .65rem .8rem; }
    .bd-track-row span { min-width: 0; }
    .bd-exp-grid { grid-template-columns: 1fr; }
    .bd-exp-name { white-space: normal; }
  }

  /* =======================================================================
     AUDIT KURSOR
     -----------------------------------------------------------------------
     Masalah: teks, kartu, metric, dan area sekitar grafik memunculkan
     kursor resize (ew-resize / ns-resize / col-resize) dan kursor
     navigasi. Dua penyebabnya:
       1. Plotly menambah lapisan drag di tepi sumbu -> sudah dimatikan di
          akarnya lewat dragmode=False + fixedrange=True di charts.py,
          dan dijaga ulang lewat CSS di bawah.
       2. Streamlit memberi anchor link otomatis pada heading.
     Aturannya: hanya elemen yang benar-benar bisa ditekan/diisi/digeser
     yang boleh punya kursor khusus.
     ======================================================================= */

  /* Teks & wadah non-interaktif -> kursor biasa. */
  .stApp, .block-container,
  h1, h2, h3, h4, h5, h6, p, span, small, strong, em, li, dt, dd,
  [data-testid="stMarkdownContainer"],
  [data-testid="stMarkdownContainer"] *,
  [data-testid="stCaptionContainer"],
  [data-testid="stHeadingWithActionElements"],
  [data-testid="stVerticalBlock"],
  [data-testid="stHorizontalBlock"],
  [data-testid="stVerticalBlock"],
  [data-testid="stVerticalBlockBorderWrapper"],
  [data-testid="stMetric"], [data-testid="stMetric"] *,
  [data-testid="stElementContainer"],
  [data-testid="stAlert"], [data-testid="stAlert"] * {
      cursor: default;
  }

  /* Elemen yang memang bisa ditekan.
     Catatan: Streamlit sudah tidak memasang data-baseweb, jadi selektor
     lama itu tidak lagi cocok dengan apa pun. Yang dipakai sekarang adalah
     data-testid dan atribut ARIA yang benar-benar ada di DOM; bentuk lama
     tetap disebut supaya versi Streamlit lain tidak ikut rusak. */
  button, [role="button"], a[href], summary,
  [role="tab"], [data-testid="stTab"], [data-testid="stTab"] *,
  button[data-baseweb="tab"], button[data-baseweb="tab"] *,
  [data-testid="stExpander"] summary,
  [data-testid="stExpander"] summary *,
  [data-testid="stButton"] > button,
  [data-testid="stButton"] > button *,
  [data-testid="stSegmentedControl"] label,
  [data-testid="stSegmentedControl"] button,
  [data-testid="stButtonGroup"] button,
  [role="combobox"], [role="combobox"] *,
  [role="option"], [role="listbox"] li,
  div[data-baseweb="select"], div[data-baseweb="select"] *,
  label[data-baseweb="checkbox"], label[data-baseweb="radio"],
  [data-testid="stCheckbox"] label, [data-testid="stRadio"] label,
  /* Tanpa awalan tag. Beberapa elemen Streamlit bukan <div> - pemicu
     tooltip salah satunya - sehingga selektor yang diawali "div" tidak
     pernah cocok dan kursornya jatuh ke aturan teks biasa. */
  [data-testid="stTooltipHoverTarget"],
  [data-testid="stTooltipHoverTarget"] *,
  [data-testid="stTooltipIcon"],
  [data-testid="stTooltipIcon"] * {
      cursor: pointer;
  }

  /* Input teks tetap caret, bukan kursor panah. */
  input[type="text"], input[type="password"], input[type="number"],
  input[type="search"], textarea {
      cursor: text;
  }

  /* Anchor link otomatis pada heading: penyebab kursor "navigasi" muncul
     di judul yang sebenarnya bukan tautan. */
  [data-testid="stHeadingWithActionElements"] a,
  [data-testid="stHeaderActionElements"] {
      display: none !important;
  }

  /* Jaring pengaman untuk Plotly: kalau versi Plotly menambah lapisan drag
     baru, kursornya tetap tidak berubah menjadi panah dobel. */
  .js-plotly-plot .plotly .draglayer,
  .js-plotly-plot .plotly .draglayer *,
  .js-plotly-plot .plotly .drag,
  .js-plotly-plot .plotly .cursor-ew-resize,
  .js-plotly-plot .plotly .cursor-ns-resize,
  .js-plotly-plot .plotly .cursor-nesw-resize,
  .js-plotly-plot .plotly .cursor-nwse-resize,
  .js-plotly-plot .plotly .cursor-col-resize,
  .js-plotly-plot .plotly .cursor-row-resize,
  .js-plotly-plot .plotly .cursor-move,
  .js-plotly-plot .plotly .cursor-crosshair,
  .js-plotly-plot .plotly .nsewdrag {
      cursor: default !important;
  }

  /* Komponen buatan sendiri: semuanya teks, bukan tombol. */
  .bd-title, .bd-subtitle, .bd-card-title, .bd-card-sub,
  .bd-pill, .bd-pills, .bd-list, .bd-row, .bd-row-main, .bd-row-name,
  .bd-row-meta, .bd-row-tag, .bd-avatar, .bd-brand, .bd-brand-sub,
  .bd-brand-mark, .bd-badge, .bd-track, .bd-track-head, .bd-track-row,
  .bd-track-name, .bd-track-meta, .bd-track-date,
  .bd-exp-grid, .bd-exp-card, .bd-exp-card *,
  .bd-section, .bd-section-title, .bd-section-sub,
  .bd-side-group, .bd-side-group-title, .bd-access, .bd-access *,
  .bd-empty, .bd-empty * {
      cursor: default;
  }
""" + ICON_FONT_CSS + """
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)

# Dark navy theme overrides the existing component hooks, preserving layout
# and widget behavior while giving all dashboard sections one visual system.
DARK_STYLE = """
<style>
  .stApp {
    color: #e7efff;
    background:
      radial-gradient(ellipse 58% 32% at 53% -4%, rgba(43,111,225,.23), transparent 72%),
      radial-gradient(ellipse 35% 30% at 100% 44%, rgba(17,94,173,.12), transparent 75%),
      linear-gradient(155deg, #0a1629 0%, #081221 46%, #07111e 100%) !important;
  }
  .block-container { max-width: 1500px; padding-top: 2.25rem; }
  [data-testid="stVerticalBlockBorderWrapper"],
  div[class*="st-key-bdcard"] {
    background: linear-gradient(145deg, rgba(20,37,62,.96), rgba(13,26,46,.96)) !important;
    border: 1px solid #2b4263 !important;
    box-shadow: 0 12px 28px rgba(0,4,15,.22), inset 0 1px rgba(119,169,255,.035) !important;
    border-radius: 18px !important;
    transition: border-color .18s ease, box-shadow .18s ease;
  }
  div[class*="st-key-bdcard"]:hover {
    border-color: #42658d !important;
    box-shadow: 0 14px 30px rgba(0,7,25,.32), 0 0 18px rgba(52,119,255,.06) !important;
  }
  [data-testid="stMetric"] {
    background: linear-gradient(150deg, #172a46, #101d32 72%) !important;
    border: 1px solid #2b4263 !important;
    box-shadow: 0 10px 25px rgba(0,5,18,.24) !important;
    border-radius: 17px !important;
  }
  [data-testid="stMetricValue"] { color: #f0f6ff !important; }
  [data-testid="stMetricLabel"] p, [data-testid="stMetricDelta"] { color: #9eafd0 !important; }
  .bd-title, .bd-card-title, .bd-section-title, .bd-brand,
  .bd-row-name, .bd-track-name, .bd-exp-name, .bd-access-name { color: #edf4ff !important; }
  .bd-subtitle, .bd-card-sub, .bd-section-sub, .bd-row-meta,
  .bd-track-meta, .bd-access-note, .bd-brand-sub, .bd-exp-pic,
  .bd-exp-label, .bd-empty-note { color: #9eafd0 !important; }
  .bd-side-group-title { color: #74a9ff !important; }
  .bd-side-group-line { background: linear-gradient(90deg, #335b91, transparent) !important; }
  .bd-row:hover { background: #1a3150 !important; }
  .bd-row-tag, .bd-pill-info {
    color: #a8c7ff !important; background: #213b65 !important; border-color: #35578a !important;
  }
  .bd-pill-muted { color: #a7b7d3 !important; background: #1b2b44 !important; border-color: #31445f !important; }
  .bd-pill-ok, .bd-badge-done { color: #7de0bb !important; background: #12382f !important; border-color: #286a54 !important; }
  .bd-pill-alert, .bd-badge-missing { color: #ffc17d !important; background: #3b2b21 !important; border-color: #765333 !important; }
  .bd-badge-wait, .bd-badge-none { color: #b4c2d9 !important; background: #1b2b44 !important; border-color: #31445f !important; }
  .bd-track-row, .bd-exp-card, .bd-empty {
    background: linear-gradient(125deg, #172a45, #112038) !important;
    border-color: #2b4263 !important;
  }
  .bd-track-row-full, .bd-exp-critical { background: linear-gradient(120deg, #1c3b61, #12243d) !important; }
  .bd-track-head span, .bd-track-date, .bd-empty-title { color: #d5e4ff !important; }
  .bd-exp-date, .bd-exp-days-normal { color: #b7cbed !important; }
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, rgba(16,31,52,.98), rgba(9,21,38,.98)) !important;
    border-right: 1px solid #294366 !important;
    box-shadow: 12px 0 35px rgba(2,8,22,.22);
  }
  section[data-testid="stSidebar"] div[class*="st-key-side_"] {
    background: rgba(27,48,78,.65) !important;
    border: 1px solid #315071 !important;
  }
  section[data-testid="stSidebar"] label p { color: #b3c4df !important; }
  section[data-testid="stSidebar"] [data-testid="stButton"] > button {
    color: #c4dcff !important; background: #203858 !important; border-color: #3b5d86 !important;
  }
  section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
    color: #fff !important; background: #2b5182 !important; border-color: #6da4ed !important;
  }
  [data-testid="stTabs"] > div:first-child { border-bottom-color: #294260 !important; }
  [data-testid="stTab"][aria-selected="true"], button[data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(120deg, #1c4a89, #286cc3) !important;
    border-radius: 11px 11px 0 0; color: white !important;
  }
  [data-testid="stTab"]:hover { background: #1b3455; }
  [data-testid="stExpander"] { border-color: #2e496a !important; border-radius: 14px !important; }
  [data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(110deg, #2464c5, #3588ed) !important;
    border-color: #5a9dec !important; color: white !important;
    box-shadow: 0 7px 21px rgba(29,104,218,.24);
  }
  [data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(110deg, #3278dd, #4b9cff) !important;
  }
  [data-testid="stButton"] > button:disabled {
    opacity: .43 !important; box-shadow: none !important; cursor: not-allowed !important;
  }
  [data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
  [data-testid="stSelectbox"] > div > div {
    background: #13243d !important; border-color: #355174 !important;
  }
  [data-testid="stProgress"] > div > div { background: linear-gradient(90deg, #2f72d9, #53b5ff) !important; }
  @media (max-width: 900px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    .bd-title { font-size: 1.25rem; }
  }
</style>
"""
st.markdown(DARK_STYLE, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Menarik data dari ESSM ...")
def load_frames(reference_date: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Ambil dan bangun seluruh dataframe. Hasilnya di-cache per tanggal acuan."""
    partner_grid = preparation.load_partner_grid()
    df_mr = preparation.build_df_mr()
    df_partner = preparation.attach_partner_functions(
        preparation.build_df_partner(partner_grid, reference_date=reference_date), df_mr
    )
    return {
        "df_mr": df_mr,
        "df_partner": df_partner,
        "df_conversion": preparation.build_df_conversion(partner_grid),
        "df_financial": preparation.build_df_financial(
            preparation.load_revenue_grid(sheets.NATIONAL_FINANCIAL_WORKSHEET)
        ),
        "df_inkind": preparation.build_df_inkind(
            preparation.load_revenue_grid(sheets.NATIONAL_INKIND_WORKSHEET)
        ),
    }


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_post_partnership_frames() -> dict[str, object]:
    """Ambil National_1.2 (Partnership Report) & PSC (Partnership Survey).

    Dipisahkan dari load_frames() dengan sengaja: kedua tab ini baru, dan
    kalau salah satunya bermasalah dashboard yang sudah berjalan tidak boleh
    ikut mati. Kegagalan dikembalikan sebagai daftar KUNCI SUMBER, bukan
    sebagai exception atau pesan teknis - jadi tidak ada stack trace, nama
    tab, atau URL spreadsheet yang bocor ke UI.

    Returns:
        dict: df_report, df_survey, unavailable (kunci sumber yang gagal).
    """
    result: dict[str, object] = {
        "df_report": pd.DataFrame(),
        "df_survey": pd.DataFrame(),
        "unavailable": [],
    }

    for key, source, build in (
        ("df_report", metrics.POST_SOURCE_REPORT, preparation.build_df_report),
        ("df_survey", metrics.POST_SOURCE_SURVEY, preparation.build_df_survey),
    ):
        try:
            result[key] = build()
        except Exception:  # noqa: BLE001 - detail teknis sengaja tidak diteruskan
            result["unavailable"].append(source)

    return result


def friendly_load_error(exc: BaseException) -> str:
    """Terjemahkan kegagalan pengambilan data menjadi pesan yang bisa ditindak.

    Hanya JENIS masalah yang disampaikan, bukan pesan asli dari pustaka.
    Pesan gspread/Google API bisa memuat URL spreadsheet, dan aturan
    proyek ini melarang URL privat muncul di UI maupun log - jadi teks
    exception tidak pernah diteruskan ke pengguna.
    """
    # Nama kelas dan kode status aman: keduanya tidak memuat URL atau kredensial.
    name = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = f"{name} {status or ''}"

    if status == 429 or "Quota" in name or "RateLimit" in name:
        return (
            "Kuota Google Sheets API sedang terlampaui. Data akan bisa "
            "ditarik lagi dalam beberapa menit — coba tekan Muat ulang data."
        )
    if status in (401, 403):
        return (
            "Akses ke spreadsheet ditolak. Pastikan Service Account masih "
            "punya izin Viewer ke Active ESSM dan National Mirror."
        )
    if status == 404 or "WorksheetNotFound" in name or "SpreadsheetNotFound" in name:
        return (
            "Spreadsheet atau tab yang dibutuhkan tidak ditemukan. Periksa "
            "apakah nama tab di sumber berubah."
        )
    if "RuntimeError" in name:
        return (
            "Konfigurasi sumber data belum lengkap. Lengkapi `.env` "
            "(atau Streamlit Secrets) dan `credentials.json`."
        )
    if "Transport" in text or "Connection" in name or "Timeout" in name:
        return (
            "Koneksi ke Google Sheets gagal. Periksa jaringan lalu tekan "
            "Muat ulang data."
        )
    return (
        "Gagal menarik data dari Google Sheets. Periksa `.env`, "
        "`credentials.json`, dan akses Viewer ke kedua spreadsheet."
    )


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
    """Satu figure di dalam kartu putih.

    key dipakai dua kali: untuk st.plotly_chart dan - dengan awalan
    "bdcard_" - sebagai key container. Awalan itu yang menjadi pegangan CSS
    (kelas st-key-bdcard_...), karena parameter key adalah bagian resmi API
    Streamlit, sedangkan struktur DOM internalnya berubah antar versi.
    """
    with st.container(border=True, key=f"bdcard_{key}" if key else None):
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
# Format tampilan: post-partnership & masa berakhir
# ---------------------------------------------------------------------------

# Gaya badge per status. Lambang teks selalu ikut, jadi statusnya tetap
# terbaca tanpa harus membedakan warna.
STATUS_BADGES: dict[str, tuple[str, str]] = {
    metrics.POST_STATUS_COMPLETED: ("done", "✓"),
    metrics.POST_STATUS_MISSING: ("missing", "!"),
    metrics.POST_STATUS_NOT_REQUIRED: ("wait", "·"),
    metrics.POST_STATUS_NO_END_DATE: ("none", "–"),
    metrics.POST_STATUS_UNAVAILABLE: ("none", "?"),
}

# Nama sumber yang boleh ditampilkan ke pengguna. Nama tab spreadsheet dan
# URL-nya tidak pernah muncul di UI.
SOURCE_LABELS: dict[str, str] = {
    metrics.POST_SOURCE_REPORT: "Partnership Report",
    metrics.POST_SOURCE_SURVEY: "Partnership Survey",
}

URGENCY_TAGS: dict[str, str] = {
    metrics.URGENCY_CRITICAL: "Ending Soon",
    metrics.URGENCY_WARNING: "Ending Soon",
    metrics.URGENCY_NORMAL: "On Track",
}


def text_or_none(value) -> str | None:
    """Teks bersih dari sel yang bisa berisi None, NaN, atau string kosong."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def status_badge(status: str) -> str:
    """Potongan HTML badge untuk satu status post-partnership."""
    kind, mark = STATUS_BADGES.get(status, ("none", "–"))
    return (
        f'<span class="bd-badge bd-badge-{kind}">'
        f'<span class="bd-badge-mark">{html.escape(mark)}</span>'
        f"{html.escape(str(status))}</span>"
    )


def section_title(title: str, subtitle: str = "") -> None:
    """Judul section di dalam tab."""
    st.markdown(
        f'<div class="bd-section"><p class="bd-section-title">{html.escape(title)}</p>'
        + (
            f'<p class="bd-section-sub">{html.escape(subtitle)}</p>'
            if subtitle
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def empty_state(title: str, note: str = "") -> None:
    """Pesan ramah saat sebuah section tidak punya data untuk ditampilkan."""
    st.markdown(
        f'<div class="bd-empty"><div class="bd-empty-title">{html.escape(title)}</div>'
        + (f'<div class="bd-empty-note">{html.escape(note)}</div>' if note else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def status_table(rows: list[dict]) -> None:
    """Tabel status berbadge: Partner | Tanggal | Survey | Report.

    Semua nilai berasal dari spreadsheet, jadi selalu di-escape sebelum
    masuk HTML.
    """
    if not rows:
        return

    head = (
        '<div class="bd-track-head">'
        "<span>Partner</span><span>Partnership End</span>"
        "<span>Partnership Survey</span><span>Partnership Report</span>"
        "</div>"
    )

    body = []
    for row in rows:
        full = row.get("full", False)
        meta = row.get("meta") or ""
        body.append(
            f'<div class="bd-track-row{" bd-track-row-full" if full else ""}">'
            "<span>"
            f'<span class="bd-track-name">{html.escape(str(row["name"]))}</span>'
            + (f'<span class="bd-track-meta">{html.escape(meta)}</span>' if meta else "")
            + "</span>"
            f'<span class="bd-track-date">{html.escape(str(row["end_label"]))}</span>'
            f'<span>{status_badge(row["survey_status"])}</span>'
            f'<span>{status_badge(row["report_status"])}</span>'
            "</div>"
        )

    st.markdown(
        f'<div class="bd-track">{head}{"".join(body)}</div>',
        unsafe_allow_html=True,
    )


def expiry_cards(rows: list[dict]) -> None:
    """Kartu partner yang paling dekat berakhir, urut paling mendesak dulu."""
    if not rows:
        empty_state(
            "Tidak ada partnership aktif yang mendekati akhir masa berlaku.",
            "Kartu akan muncul begitu ada kontrak yang menuju tanggal berakhir.",
        )
        return

    cards = []
    for row in rows:
        urgency = row["urgency"]
        pic = row.get("pic")
        # PIC hanya tersedia di National_1.2; kalau tidak ketemu, baris ini
        # diisi keterangan lain (stakeholder) daripada menampilkan PIC kosong.
        subtitle = f"PIC: {pic}" if pic else str(row.get("meta") or "")
        cards.append(
            f'<div class="bd-exp-card bd-exp-{urgency}">'
            '<div class="bd-exp-top">'
            f'<span class="bd-exp-name">{html.escape(str(row["name"]))}</span>'
            f'<span class="bd-exp-tag bd-exp-tag-{urgency}">'
            f'{html.escape(URGENCY_TAGS.get(urgency, "On Track"))}</span>'
            "</div>"
            f'<div class="bd-exp-pic">{html.escape(subtitle)}</div>'
            '<div class="bd-exp-label">Partnership ends</div>'
            f'<div class="bd-exp-date">{html.escape(str(row["end_label"]))}</div>'
            f'<div class="bd-exp-days bd-exp-days-{urgency}">'
            f'{html.escape(str(row["days_label"]))}</div>'
            "</div>"
        )

    st.markdown(
        f'<div class="bd-exp-grid">{"".join(cards)}</div>', unsafe_allow_html=True
    )


# ---------------------------------------------------------------------------
# Sidebar: pilihan periode
# ---------------------------------------------------------------------------


def sidebar_group(title: str) -> None:
    """Judul kelompok kontrol di sidebar, dengan garis pemisah halus."""
    st.markdown(
        '<div class="bd-side-group">'
        f'<span class="bd-side-group-title">{html.escape(title)}</span>'
        '<span class="bd-side-group-line"></span>'
        "</div>",
        unsafe_allow_html=True,
    )


def sidebar_controls() -> tuple[object, bool]:
    """Kontrol periode. Return (pilihan untuk periods.apply_period, minta_refresh).

    Tata letaknya dikelompokkan menjadi Dashboard Controls / Data Source /
    Access. Seluruh widget dan nilai default TIDAK berubah dari sebelumnya:
    hanya pembungkus visual dan pengelompokannya yang baru.
    """
    with st.sidebar:
        st.markdown(
            '<div class="bd-brand-mark">BD</div>'
            '<div class="bd-brand">AIESEC in BINUS</div>'
            '<div class="bd-brand-sub">Business Development Analytics</div>',
            unsafe_allow_html=True,
        )

        # ---- Dashboard Controls ----
        sidebar_group("Dashboard Controls")
        with st.container(border=True, key="side_controls"):
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

        # ---- Data Source ----
        sidebar_group("Data Source")
        with st.container(border=True, key="side_source"):
            st.markdown(
                '<p class="bd-access-note" style="margin:0 0 .45rem 0;">'
                'ESSM mirror · cache 15 menit</p>',
                unsafe_allow_html=True,
            )
            refresh = st.button("Muat ulang data", width="stretch", type="secondary")

        # ---- Access ----
        sidebar_group("Access")
        with st.container(border=True, key="side_access"):
            if IS_MEMBER:
                dot, name, note = "member", "AIESEC Member", "Akses penuh data internal"
            else:
                dot, name, note = "anon", "Anonymous", "Data internal disamarkan"
            st.markdown(
                '<div class="bd-access">'
                f'<span class="bd-access-dot bd-access-dot-{dot}"></span>'
                '<span class="bd-access-main">'
                f'<span class="bd-access-name">{name}</span><br>'
                f'<span class="bd-access-note">{note}</span>'
                "</span></div>",
                unsafe_allow_html=True,
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
            f"partner aktif: gabungan bulanan · status kontrak per "
            f"{frame.reference_date:%d %B %Y}</p>",
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
            protected(number(summary["total_mr"])),
            delta=(
            f"{len(data['mr_by_pic'])} PIC terlibat"
            if IS_MEMBER
            else "XXX PIC terlibat"),
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
            protected(number(summary["active_partners"])),
            delta=(
            f"dari {number(summary['partner_count'])} partner"
            if IS_MEMBER
            else "dari XXX partner"),
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
            protected(percent(summary["conversion_rate"])),
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
            protected(charts.rupiah_compact(summary["financial_revenue"])),
            delta=(
            f"{len(frame.df_financial)} pembayaran"
            if IS_MEMBER
            else "XXX pembayaran"),
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
            protected(charts.rupiah_compact(summary["inkind_value"])),
            delta=(
            f"{len(frame.df_inkind)} dukungan"
            if IS_MEMBER
            else "XXX dukungan"),
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
    if data.get("anonymous_demo"):
        # Never recalculate public alert counts from the unmasked ESSM frame.
        ending, soon, expired = 2, 3, 4
    else:
        active_expiry = metrics.get_expiry_summary(
            frame.df_partner, reference_date=frame.reference_date, active_only=True
        )
        all_expiry = data["expiry_summary"]

        def count(summary: pd.DataFrame, category: str) -> int:
            row = summary.loc[summary["expiry_category"] == category]
            return int(row["partner_count"].iloc[0]) if not row.empty else 0

        ending = count(active_expiry, metrics.EXPIRY_THIS_MONTH)
        soon = count(active_expiry, metrics.EXPIRY_SOON)
        expired = count(all_expiry, metrics.EXPIRY_EXPIRED)
    items: list[tuple[str, str]] = []
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
        with st.container(border=True, key="bdcard_watchlist"):
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
        with st.container(border=True, key="bdcard_mr_detail"):
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

        if not IS_MEMBER:
            st.info("🔒 Partner details are hidden in public mode.")

        else:
            active = data["frame"].df_partner_active

            st.dataframe(
                active[
                    [
                        "partner_name",
                        "stakeholder",
                        "signed_month_raw",
                        "end_month_raw",
                        "months_remaining",
                    ]
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
        subtitle="v = dokumen ada · N/A = Invoice exception · urut dari yang paling banyak kosong",
    )

    if IS_MEMBER and not data["tracker"].empty:
        with st.expander("Invoice exceptions · per partner"):
            st.caption(
                "Select In-Kind or No Membership when an invoice is not required. "
                "Choose Required to clear an exception."
            )
            tracker = data["tracker"]
            editor = pd.DataFrame({
                "Partner": tracker["partner_name"],
                "Invoice": [
                    "On file" if row.has_invoice else
                    f"N/A — {row.invoice_exception}" if pd.notna(row.invoice_exception)
                    else "Missing" for row in tracker.itertuples()
                ],
                "Invoice Exception": tracker["invoice_exception"].fillna("Required"),
                "Document Compliance": tracker["document_compliance_percent"].map(
                    lambda value: f"{value:.0f}%"
                ),
            })
            revision = st.session_state.get("invoice_editor_revision", 0)
            updated = st.data_editor(
                editor, hide_index=True, width="stretch",
                disabled=["Partner", "Invoice", "Document Compliance"],
                column_config={
                    "Invoice Exception": st.column_config.SelectboxColumn(
                        "Invoice Exception",
                        options=["Required", "In-Kind", "No Membership"],
                        required=True,
                    ),
                },
                key=f"invoice_editor_{revision}",
            )
            if st.button("Save invoice exceptions", type="primary"):
                try:
                    store = get_store()
                    for before, after in zip(editor.itertuples(), updated.itertuples()):
                        if before[3] != after[3]:
                            store.set_invoice_exception(
                                before[1], None if after[3] == "Required" else after[3]
                            )
                except SNDStoreError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.invoice_editor_revision = revision + 1
                    st.rerun()

    section_partnership_report_survey(data)
    section_completed_partnership(data)
    section_expiring_partners(data)


SND_STATUS_MARK = {
    "COMPLETED": "✓",
    "PARTIAL": "◐",
    "MISSING": "✕",
    "NEEDS_REVIEW": "?",
}


def _snd_editor(rows: list[dict], kind: str, partner_id: str) -> pd.DataFrame:
    selected = [row for row in rows if row["kind"] == kind]
    frame = pd.DataFrame(
        [{
            "ID": row["id"], "Deliverable": row["name"],
            "Quantity": row["quantity"],
        } for row in selected],
        columns=["ID", "Deliverable", "Quantity"],
    )
    if frame.empty:
        frame = frame.astype({"ID": "str", "Deliverable": "str", "Quantity": "Int64"})
    st.markdown(f"#### {kind.title()}")
    st.caption("Use Add row to add a requirement; select a row and delete it to remove one.")
    revision = st.session_state.get("snd_editor_revision", 0)
    return st.data_editor(
        frame, num_rows="dynamic", hide_index=True, width="stretch",
        column_order=["Deliverable", "Quantity"],
        column_config={
            "ID": None,
            "Deliverable": st.column_config.TextColumn("Deliverable", required=True, width="large"),
            "Quantity": st.column_config.NumberColumn(
                "Quantity", min_value=1, step=1, required=True, width="small", format="%d"
            ),
        },
        key=f"snd_{kind}_{partner_id}_{revision}",
    )


def _snd_editor_rows(frame: pd.DataFrame, kind: str) -> list[dict]:
    rows = []
    for item in frame.to_dict("records"):
        name = preparation.clean_text(item.get("Deliverable"))
        quantity = item.get("Quantity")
        if not name and (quantity is None or pd.isna(quantity)):
            continue
        rows.append({
            "id": preparation.clean_text(item.get("ID")), "kind": kind, "name": name,
            "quantity": quantity,
        })
    return rows


def _snd_check_results(result: dict) -> None:
    if result.get("partner_match") != "matched":
        st.warning("Partner Drive folder could not be matched safely. Review the folder name.")
    for warning in result.get("warnings", []):
        st.warning(warning)
    columns = st.columns(3)
    columns[0].metric("Supply", f"{result['supply_percent']:.0f}%")
    columns[1].metric("Demand", f"{result['demand_percent']:.0f}%")
    columns[2].metric("Overall S&D", f"{result['overall_percent']:.0f}%")
    st.progress(result["overall_percent"] / 100)
    for kind in ("supply", "demand"):
        st.markdown(f"#### {kind.title()} fulfillment")
        for item in result["requirements"]:
            if item["kind"] != kind:
                continue
            mark = SND_STATUS_MARK[item["status"]]
            with st.container(border=True):
                st.markdown(
                    f"{mark} **{html.escape(item['name'])}** · "
                    f"{item['fulfilled_quantity']}/{item['quantity']} · "
                    f"{item['status'].replace('_', ' ').title()}"
                )
                for proof in item["matched_evidence"]:
                    st.caption(
                        f"✓ {proof['name']} · {proof['method']} · "
                        f"{proof['confidence']:.0%} · credit {proof['credited_quantity']}"
                    )
                for proof in item["possible_evidence"]:
                    st.caption(f"? {proof['name']} · possible match ({proof['confidence']:.0%})")
    unmatched = result.get("unmatched_evidence", [])
    if unmatched:
        with st.container(border=True):
            st.markdown(f"#### Unmatched Evidence ({len(unmatched)})")
            for proof in unmatched:
                st.caption(f"{proof['kind'].title()} · {proof['name']}")


def tab_snd_tracker(data: dict) -> None:
    """ESSM active partners, saved manual requirements, and on-demand proof scan."""
    section_title(
        "S&D Fulfillment Tracker",
        "Set deliverables for active ESSM partners, then check their Drive proof.",
    )
    if not may_access_real_snd(st.session_state.get("access_mode")):
        st.info("S&D requirements and evidence are available to members only.", icon=":material/lock:")
        return

    active = data["frame"].df_partner_active
    if active.empty:
        empty_state("No active partners in this period.")
        return
    names = sorted(active["partner_name"].astype(str).unique(), key=str.casefold)
    try:
        store = get_store()
        resolutions = snd_tracker.active_partner_functions(active, store.get_partner_function_assignments())
        functions = {key: resolution.value for key, resolution in resolutions.items()}
        counts = store.requirement_counts()
    except SNDStoreError as exc:
        st.error(str(exc))
        return
    st.metric("Active Partners", len(names))

    search_col, filter_col = st.columns([2, 1])
    with search_col:
        search = st.text_input("Search Partner", placeholder="Search active partners").casefold().strip()
    with filter_col:
        category = st.selectbox("Function", ["All", "ELDs", "EwAs", "SS", "Unassigned"], key="snd_category")

    shown = [
        name for name in names
        if search in name.casefold()
        and (category == "All" or
             (category == "Unassigned" and not functions.get(partner_key(name))) or
             functions.get(partner_key(name)) == category)
    ]
    if not shown:
        empty_state("No partners match the search and function filter.")
        return

    selected_key = st.session_state.get("snd_selected_partner")
    for name in shown:
        key = partner_key(name)
        function = functions.get(key)
        requirements_count = counts.get(key, 0)
        requirements = store.list_requirements(name) if requirements_count else []
        signature = requirement_signature(requirements, function or "") if requirements else ""
        previous = store.get_check(name, signature) if signature else None
        selected = selected_key == key
        setup = "Configured" if requirements_count else "Not configured"
        progress = f"{previous['overall_percent']:.0f}%" if previous else "Not checked"
        with st.expander(
            f"{name} · {function or 'Unassigned'} · S&D Setup: {setup} · Fulfillment: {progress}",
            expanded=selected,
        ):
            if not selected:
                if st.button("Manage S&D", key=f"snd_manage_{key}"):
                    st.session_state.snd_selected_partner = key
                    st.rerun()
                continue

            chosen = function
            if function:
                st.markdown(f"**Function** · `{function}`")
                st.caption("Auto-detected from ESSM" if resolutions[key].source == "essm"
                           else "Saved partner function")
            else:
                st.caption("Function/category could not be identified unambiguously from ESSM.")
                chosen = st.selectbox(
                    "Function / category", ["ELDs", "EwAs", "SS"], index=None,
                    placeholder="Select function", key=f"snd_function_{key}",
                )
                if not chosen:
                    st.warning("Select the partner function before saving S&D requirements.")
            supply = _snd_editor(requirements, "supply", key)
            demand = _snd_editor(requirements, "demand", key)
            save_col, check_col = st.columns(2)
            with save_col:
                if st.button("Save S&D Requirements", type="primary", key=f"snd_save_{key}",
                             width="stretch", disabled=not chosen):
                    try:
                        saved = snd_tracker.save_partner_requirements(
                            name, active,
                            _snd_editor_rows(supply, "supply") + _snd_editor_rows(demand, "demand"),
                            store=store, manual_function=chosen,
                        )
                    except (ValueError, SNDStoreError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.snd_editor_revision = st.session_state.get("snd_editor_revision", 0) + 1
                        st.toast(f"Saved {len(saved)} requirements.")
                        st.rerun()
            with check_col:
                if st.button(
                    "↻ Check Fulfillment", type="secondary",
                    disabled=not snd_tracker.can_check_fulfillment(requirements, function),
                    key=f"snd_check_{key}", width="stretch",
                ):
                    try:
                        with st.spinner("Checking proof filenames in Google Drive..."):
                            previous = snd_tracker.check_partner_fulfillment(
                                name, function, store=store
                            )
                    except (DriveServiceError, ValueError, SNDStoreError) as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            if previous:
                _snd_check_results(previous)
            elif requirements:
                st.caption("Check Fulfillment to scan the saved requirements against Drive proof filenames.")
# ---------------------------------------------------------------------------
# Section baru: post-partnership & masa berakhir
# ---------------------------------------------------------------------------


def source_warnings(data: dict) -> None:
    """Peringatan ramah kalau salah satu sumber post-partnership tak terbaca."""
    for source in data.get("post_unavailable", []):
        label = SOURCE_LABELS.get(source, "Post-partnership")
        st.warning(f"{label} data is currently unavailable.", icon=":material/info:")


def section_partnership_report_survey(data: dict) -> None:
    """Partnership Report & Survey Tracker — SELURUH partner.

    Menunjukkan dengan jelas bahwa kedua dokumen ini hanya ditagih setelah
    masa partnership berakhir: partner aktif tampil sebagai "Not Required
    Yet", bukan "Missing".
    """
    with st.container(border=True, key="bdcard_section_report_survey"):
        section_title(
            "Partnership Report & Survey Tracker",
            "Kedua dokumen ini ditagih begitu bulan berakhir partnership "
            "tiba — termasuk partner yang berakhir bulan ini.",
        )
        source_warnings(data)

        status = data["post_status"]
        if status.empty:
            empty_state(
                "Belum ada data partner untuk dilacak.",
                "Tracker akan terisi begitu National 1.1 memuat partner.",
            )
            return

        counts = status["report_status"].value_counts()

        def status_count(label: str) -> str:
            return protected(number(int(counts.get(label, 0))))

        items: list[tuple[str, str]] = [
            ("ok", f"{status_count(metrics.POST_STATUS_COMPLETED)} report completed"),
            ("alert", f"{status_count(metrics.POST_STATUS_MISSING)} report missing"),
            ("info", f"{status_count(metrics.POST_STATUS_NOT_REQUIRED)} belum wajib "
                     "(belum masuk bulan berakhir)"),
            ("muted", f"{status_count(metrics.POST_STATUS_NO_END_DATE)} "
                      "tanpa tanggal akhir"),
        ]
        blocked = int(counts.get(metrics.POST_STATUS_UNAVAILABLE, 0))
        if blocked:
            items.append(
                ("muted", f"{protected(number(blocked))} belum bisa dinilai "
                          "(sumber tidak terbaca)")
            )
        pills(items)

        early = int(status["report_submitted_early"].sum())
        if early:
            st.caption(
                f"{protected(number(early))} partner sudah mengumpulkan "
                "Partnership Report sebelum bulan berakhirnya tiba. Statusnya "
                "tetap Not Required Yet karena belum ditagih."
            )

        # Tinggi dibatasi supaya daftar seluruh partner tidak mendorong
        # section di bawahnya terlalu jauh; isinya bisa digulir.
        with st.container(height=360, border=False):
            status_table(post_status_rows(status))


def section_completed_partnership(data: dict) -> None:
    """Completed Partnership Tracker — hanya partner yang sudah selesai."""
    with st.container(border=True, key="bdcard_section_completed"):
        section_title(
            "Completed Partnership Tracker",
            "Partnership yang sudah memasuki atau melewati bulan "
            f"berakhirnya per {data['frame'].reference_date:%B %Y}.",
        )
        source_warnings(data)

        tracker = data["post_tracker"]
        summary = data["post_summary"]

        if tracker.empty:
            empty_state(
                "No completed partnerships found for the selected period.",
                "Partnership yang bulan berakhirnya belum tiba tidak "
                "ditampilkan di section ini.",
            )
            return

        metric_columns = st.columns(5, gap="small")
        with metric_columns[0]:
            st.metric(
                "Completed Partnerships",
                protected(number(summary["completed_partnerships"])),
                delta="bulan berakhir sudah tiba",
                delta_color="primary", delta_arrow="off", border=True,
                help="Partner yang bulan berakhir partnership-nya sudah tiba "
                     "atau terlewat pada periode yang dipilih.",
            )
        with metric_columns[1]:
            st.metric(
                "Survey Completed",
                protected(number(summary["survey_completed"])),
                delta=f"dari {number(summary['completed_partnerships'])} partnership"
                if IS_MEMBER else "dari XXX partnership",
                delta_color="blue", delta_arrow="off", border=True,
                help="Nama partner ditemukan pada respons Partnership Survey (PSC).",
            )
        with metric_columns[2]:
            st.metric(
                "Report Completed",
                protected(number(summary["report_completed"])),
                delta=f"dari {number(summary['completed_partnerships'])} partnership"
                if IS_MEMBER else "dari XXX partnership",
                delta_color="primary", delta_arrow="off", border=True,
                help="Dokumen Post-Partnership Report sudah tercatat di "
                     "National 1.2.",
            )
        with metric_columns[3]:
            st.metric(
                "Fully Completed",
                protected(number(summary["fully_completed"])),
                delta="survey dan report lengkap",
                delta_color="blue", delta_arrow="off", border=True,
                help="Partner yang Partnership Survey DAN Partnership "
                     "Report-nya sudah ada.",
            )
        with metric_columns[4]:
            st.metric(
                "Post-Partnership Completion",
                protected(percent(summary["completion_percent"])),
                delta=(
                    f"{summary['survey_completed'] + summary['report_completed']}"
                    f" / {summary['required_documents']} dokumen"
                    if IS_MEMBER
                    else "XXX / XXX dokumen"
                ),
                delta_color="primary", delta_arrow="off", border=True,
                help="(survey selesai + report selesai) dibagi "
                     "(partnership selesai x 2). Partner aktif tidak ikut "
                     "dihitung sebagai penyebut.",
            )

        detail, chart = st.columns([1.6, 1], gap="small")
        with detail:
            status_table(post_tracker_rows(tracker))
            recorded_gap = summary["report_recorded_without_document"]
            if recorded_gap:
                st.caption(
                    f"{protected(number(recorded_gap))} partner sudah tercatat "
                    "di National 1.2 tetapi kolom dokumen laporannya masih "
                    "kosong, jadi laporannya dihitung belum ada."
                )
        with chart:
            st.plotly_chart(
                charts.post_partnership_bar(
                    data["post_completeness"], title=None, description=""
                ),
                width="stretch", theme=None, config=PLOTLY_CONFIG,
                key="dc_post_bar",
            )


def section_expiring_partners(data: dict) -> None:
    """Partner yang partnership-nya akan segera berakhir, dalam kartu."""
    with st.container(border=True, key="bdcard_section_expiring"):
        section_title(
            "Partnership yang segera berakhir",
            "Urut dari yang paling dekat berakhir. Hanya partnership aktif.",
        )

        rows = expiring_card_rows(data["expiring"])
        if rows:
            critical = sum(
                1 for row in rows if row["urgency"] == metrics.URGENCY_CRITICAL
            )
            warning = sum(
                1 for row in rows if row["urgency"] == metrics.URGENCY_WARNING
            )
            items: list[tuple[str, str]] = []
            if critical:
                items.append(
                    ("alert", f"{protected(number(critical))} berakhir dalam "
                              f"{metrics.EXPIRY_CRITICAL_DAYS} hari atau kurang")
                )
            if warning:
                items.append(
                    ("info", f"{protected(number(warning))} berakhir dalam "
                             f"{metrics.EXPIRY_CRITICAL_DAYS + 1}–"
                             f"{metrics.EXPIRY_WARNING_DAYS} hari")
                )
            items.append(
                ("muted", f"{protected(number(len(rows)))} partnership aktif dipantau")
            )
            pills(items)

        expiry_cards(rows)


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


# ---------------------------------------------------------------------------
# Penyiapan baris tampilan: post-partnership & masa berakhir
# ---------------------------------------------------------------------------


def date_label(value) -> str:
    """Tanggal panjang yang enak dibaca: 30 September 2026."""
    if value is None or pd.isna(value):
        return "—"
    return f"{pd.Timestamp(value):%d %B %Y}"


def days_remaining_label(days) -> str:
    """Sisa hari dalam bentuk kalimat pendek."""
    if days is None or pd.isna(days):
        return "No end date"
    remaining = int(days)
    if remaining == 0:
        return "Ends today"
    if remaining == 1:
        return "1 day remaining"
    return f"{remaining} days remaining"


def end_label(end_date, end_month_raw) -> str:
    """Label tanggal akhir. Kembali ke teks mentah sheet kalau tidak terurai."""
    if end_date is not None and not pd.isna(end_date):
        return date_label(end_date)
    raw = text_or_none(end_month_raw)
    return raw or "Tanpa tanggal akhir"


def post_status_rows(status: pd.DataFrame) -> list[dict]:
    """Baris tabel "Partnership Report & Survey Tracker" (semua partner)."""
    if status is None or status.empty:
        return []
    rows: list[dict] = []
    for item in status.itertuples():
        stakeholder = text_or_none(item.stakeholder) or "Tanpa stakeholder"
        rows.append(
            {
                "name": item.partner_name,
                "meta": stakeholder,
                "end_label": end_label(item.end_date, item.end_month_raw),
                "survey_status": item.survey_status,
                "report_status": item.report_status,
                "full": bool(
                    item.survey_status == metrics.POST_STATUS_COMPLETED
                    and item.report_status == metrics.POST_STATUS_COMPLETED
                ),
            }
        )
    return rows


def since_end_label(days_since_end, is_final_month: bool) -> str:
    """Keterangan singkat sejak/menuju akhir partnership.

    Sejak aturan post-partnership memakai BULAN, partner yang berakhir bulan
    ini sudah masuk tracker walau tanggalnya belum tiba - jadi sisa harinya
    bisa negatif dan tidak boleh ditulis "x hari lalu".
    """
    if days_since_end is None or pd.isna(days_since_end):
        return "berakhir bulan ini" if is_final_month else ""
    days = int(days_since_end)
    if days > 0:
        return f"selesai {days} hari lalu"
    if days == 0:
        return "berakhir hari ini"
    return f"berakhir bulan ini · {abs(days)} hari lagi"


def post_tracker_rows(tracker: pd.DataFrame) -> list[dict]:
    """Baris tabel "Completed Partnership Tracker" (hanya yang sudah ditagih)."""
    if tracker is None or tracker.empty:
        return []
    rows: list[dict] = []
    for item in tracker.itertuples():
        stakeholder = text_or_none(item.stakeholder) or "Tanpa stakeholder"
        since = since_end_label(
            item.days_since_end, bool(getattr(item, "is_final_month", False))
        )
        rows.append(
            {
                "name": item.partner_name,
                "meta": f"{stakeholder} · {since}" if since else stakeholder,
                "end_label": end_label(item.end_date, item.end_month_raw),
                "survey_status": item.survey_status,
                "report_status": item.report_status,
                "full": bool(item.is_fully_completed),
            }
        )
    return rows


def expiring_card_rows(expiring: pd.DataFrame) -> list[dict]:
    """Baris kartu partner yang segera berakhir. Urutan dari metrics dijaga."""
    if expiring is None or expiring.empty:
        return []
    rows: list[dict] = []
    for item in expiring.itertuples():
        stakeholder = text_or_none(item.stakeholder) or "Tanpa stakeholder"
        rows.append(
            {
                "name": item.partner_name,
                "pic": text_or_none(item.pic_aiesec),
                "meta": stakeholder,
                "end_label": end_label(item.end_date, item.end_month_raw),
                "days_label": days_remaining_label(item.days_remaining),
                "urgency": item.urgency,
            }
        )
    return rows


def build_view_data(
    frame: periods.PeriodFrames,
    today: pd.Timestamp,
    df_report: pd.DataFrame | None = None,
    df_survey: pd.DataFrame | None = None,
    post_unavailable: list[str] | None = None,
    invoice_exceptions: dict[str, str] | None = None,
) -> dict:
    """Semua angka & tabel yang dipakai halaman, semuanya lewat metrics.py.

    df_report (National_1.2) & df_survey (PSC) TIDAK disaring per bulan.
    Status post-partnership adalah metrik POSISI seperti Active Partners:
    artinya hanya ada relatif terhadap satu tanggal, jadi yang dipakai
    adalah frame.reference_date - bukan penyaringan baris per periode
    (lihat aturan 4 di periods.py).
    """
    summary = metrics.get_kpi_summary(
        frame.df_mr, frame.df_partner, frame.df_conversion,
        frame.df_financial, frame.df_inkind,
        scope=frame.conversion_scope or "",
    )
    summary["active_partners"] = metrics.get_active_partner_count(frame.df_partner_active)
    expiry_detail = metrics.get_partnership_expiry(
        frame.df_partner, reference_date=frame.reference_date
    )
    post_unavailable = list(post_unavailable or [])
    post_tracker = metrics.get_post_partnership_tracker(
        frame.df_partner, df_report, df_survey,
        reference_date=frame.reference_date,
        unavailable=post_unavailable,
    )
    expiring = metrics.get_expiring_partners(
        frame.df_partner, reference_date=frame.reference_date, df_report=df_report
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
        "completeness": metrics.get_document_completeness(
            frame.df_partner_active, invoice_exceptions
        ),
        "tracker": metrics.get_document_tracker(
            frame.df_partner_active, invoice_exceptions
        ),
        "expiry_summary": metrics.get_expiry_summary(
            frame.df_partner, reference_date=frame.reference_date
        ),
        "expiry_detail": expiry_detail,
        "active_trend": active_partner_trend(frame.df_partner, today),
        "watchlist": watchlist_rows(expiry_detail),
        # ---- post-partnership ----
        "post_tracker": post_tracker,
        "post_summary": metrics.get_post_partnership_summary(post_tracker),
        "post_completeness": metrics.get_post_partnership_completeness(post_tracker),
        "post_status": metrics.get_partnership_document_status(
            frame.df_partner, df_report, df_survey,
            reference_date=frame.reference_date,
            unavailable=post_unavailable,
        ),
        "post_unavailable": post_unavailable,
        "expiring": expiring,
    }

def mask_anonymous_data(data: dict) -> dict:
    """
    Membuat copy data yang aman untuk tampilan anonymous.

    Data asli tidak diubah.
    Nama partner/PIC dianonimkan dan nilai numerik diganti
    dengan demo values agar layout/chart tetap terlihat.
    """
    import copy

    masked = copy.deepcopy(data)
    masked["anonymous_demo"] = True

    # =====================================================
    # HELPER
    # =====================================================

    def demo_values(length: int, start: int = 10) -> list[int]:
        """
        Generate angka demo deterministic.
        Tidak berasal dari nilai ESSM asli.
        """
        pattern = [12, 18, 15, 24, 20, 28, 23, 31, 26, 22, 17, 14]

        return [
            pattern[i % len(pattern)] + start
            for i in range(length)
        ]

    def anonymize_names(df, column: str, prefix: str):
        """Ganti nama asli dengan label anonymous."""
        if df is None or df.empty or column not in df.columns:
            return df

        df = df.copy()

        unique_names = df[column].dropna().astype(str).unique()

        mapping = {
            name: f"{prefix} {i:02d}"
            for i, name in enumerate(unique_names, start=1)
        }

        df[column] = (
            df[column]
            .astype(str)
            .map(mapping)
            .fillna(f"{prefix} XX")
        )

        return df

    def replace_numeric(df, columns):
        """Ganti kolom numerik dengan demo values."""
        if df is None or df.empty:
            return df

        df = df.copy()

        for column in columns:
            if column in df.columns:
                df[column] = demo_values(len(df))

        return df

    # =====================================================
    # SUMMARY / KPI
    # =====================================================

    summary = masked.get("summary", {})

    if summary:
        summary["total_mr"] = 30
        summary["active_partners"] = 18
        summary["partner_count"] = 30
        summary["conversion_rate"] = 40.0
        summary["financial_revenue"] = 10_000_000
        summary["inkind_value"] = 5_000_000

    # Nilai di atas hanya dummy untuk kebutuhan chart.
    # Tulisan KPI tetap menjadi XXX karena protected().

    # =====================================================
    # MARKET RESEARCH
    # =====================================================

    mr_pic = masked.get("mr_by_pic")

    if mr_pic is not None:
        mr_pic = anonymize_names(
            mr_pic,
            "pic_aiesec",
            "Anonymous"
        )

        mr_pic = replace_numeric(
            mr_pic,
            ["total_mr", "share_percent"]
        )

        masked["mr_by_pic"] = mr_pic


    mr_month = masked.get("mr_by_month")

    if mr_month is not None and not mr_month.empty:

        mr_month = mr_month.copy()

        if "total_mr" in mr_month.columns:
            mr_month["total_mr"] = demo_values(
                len(mr_month),
                start=5
            )

        masked["mr_by_month"] = mr_month

    # =====================================================
    # ACTIVE PARTNER TREND
    # =====================================================

    trend = masked.get("active_trend")

    if trend:
        masked["active_trend"] = [
            8, 10, 12, 11, 14, 16,
            18, 17, 20, 21, 19, 22
        ][:len(trend)]

    # =====================================================
    # STAKEHOLDER
    # =====================================================

    stakeholder = masked.get("stakeholder")

    if stakeholder is not None and not stakeholder.empty:

        stakeholder = stakeholder.copy()

        numeric_columns = stakeholder.select_dtypes(
            include="number"
        ).columns

        for column in numeric_columns:
            stakeholder[column] = demo_values(
                len(stakeholder),
                start=3
            )

        masked["stakeholder"] = stakeholder

    # =====================================================
    # CONVERSION FUNNEL
    # =====================================================

    funnel = masked.get("funnel")

    if funnel is not None and not funnel.empty:

        funnel = funnel.copy()

        numeric_columns = funnel.select_dtypes(
            include="number"
        ).columns

        values = [50, 38, 27, 19, 12]

        for column in numeric_columns:
            funnel[column] = [
                values[i % len(values)]
                for i in range(len(funnel))
            ]

        masked["funnel"] = funnel

    # =====================================================
    # CONVERSION PER MONTH
    # =====================================================

    conversion = masked.get("conversion_monthly")

    if conversion is not None and not conversion.empty:

        conversion = conversion.copy()

        if "conversion_rate" in conversion.columns:
            conversion["conversion_rate"] = [
                25, 30, 28, 35, 40, 38,
                45, 42, 48, 44, 39, 41
            ][:len(conversion)]

        masked["conversion_monthly"] = conversion

    # =====================================================
    # FINANCIAL / IN-KIND MONTHLY
    # =====================================================

    for key in ["financial_monthly", "inkind_monthly"]:

        df = masked.get(key)

        if df is not None and not df.empty:

            df = df.copy()

            if "amount" in df.columns:
                df["amount"] = [
                    value * 100_000
                    for value in demo_values(len(df))
                ]

            masked[key] = df

    # =====================================================
    # REVENUE PER PARTNER
    # =====================================================

    for key in ["financial_partner", "inkind_partner"]:

        df = masked.get(key)

        if df is not None and not df.empty:

            df = anonymize_names(
                df,
                "partner_name",
                "Partner"
            )

            numeric_columns = df.select_dtypes(
                include="number"
            ).columns

            for column in numeric_columns:
                df[column] = [
                    value * 100_000
                    for value in demo_values(len(df))
                ]

            masked[key] = df

    # =====================================================
    # WATCHLIST
    # =====================================================

    watchlist = masked.get("watchlist", [])

    anonymous_watchlist = []

    for i, row in enumerate(watchlist, start=1):

        new_row = row.copy()

        new_row["name"] = f"Partner {i:02d}"
        new_row["meta"] = "Internal partnership · details hidden"
        new_row["tag"] = "Hidden"

        anonymous_watchlist.append(new_row)

    masked["watchlist"] = anonymous_watchlist

    # =====================================================
    # DOCUMENT TRACKER
    # =====================================================

    # Fixed demo rows: neither status nor partner count comes from ESSM.
    demo_tracker = pd.DataFrame([
        {
            "partner_name": f"Partner {index + 1:02d}",
            "stakeholder": "Hidden",
            "has_proposal": index % 4 != 0,
            "has_mom": index % 3 != 0,
            "has_loa": index % 5 != 0,
            "has_invoice": index % 2 == 0,
            "invoice_exception": None,
            "invoice_satisfied": index % 2 == 0,
        }
        for index in range(8)
    ])
    document_cols = ["has_proposal", "has_mom", "has_loa", "invoice_satisfied"]
    demo_tracker["documents_missing"] = 4 - demo_tracker[document_cols].sum(axis=1)
    demo_tracker["documents_complete"] = demo_tracker["documents_missing"].eq(0)
    demo_tracker["document_compliance_percent"] = (
        demo_tracker[document_cols].sum(axis=1) / 4 * 100
    )
    masked["tracker"] = demo_tracker
    masked["completeness"] = pd.DataFrame([
        {
            "document": document,
            "available": int(demo_tracker[column].sum()),
            "missing": 8 - int(demo_tracker[column].sum()),
            "available_percent": round(float(demo_tracker[column].mean()) * 100, 2),
        }
        for document, column in zip(metrics.DOCUMENT_FIELDS, document_cols)
    ])

    # =====================================================
    # EXPIRY DETAIL
    # =====================================================

    expiry = masked.get("expiry_detail")

    if expiry is not None and not expiry.empty:

        expiry = anonymize_names(
            expiry,
            "partner_name",
            "Partner"
        )

        # Jangan tampilkan bulan kontrak asli
        if "end_month_raw" in expiry.columns:
            expiry["end_month_raw"] = "Hidden"

        if "months_remaining" in expiry.columns:
            expiry["months_remaining"] = 3

        masked["expiry_detail"] = expiry

    # =====================================================
    # EXPIRY SUMMARY
    # =====================================================

    expiry_summary = masked.get("expiry_summary")

    if expiry_summary is not None and not expiry_summary.empty:

        expiry_summary = expiry_summary.copy()

        numeric_columns = expiry_summary.select_dtypes(
            include="number"
        ).columns

        for column in numeric_columns:
            expiry_summary[column] = demo_values(
                len(expiry_summary),
                start=2
            )

        masked["expiry_summary"] = expiry_summary

    # =====================================================
    # POST-PARTNERSHIP (Report & Survey)
    # =====================================================
    #
    # Nama partner disamarkan. STATUS tidak diubah: statusnya bukan data
    # sensitif dan justru bagian yang ingin diperlihatkan di mode publik.
    # Tanggal akhir partnership disembunyikan karena itu isi kontrak.

    for key in ["post_tracker", "post_status"]:

        df = masked.get(key)

        if df is not None and not df.empty:

            df = anonymize_names(
                df,
                "partner_name",
                "Partner"
            )

            for column in ["stakeholder", "survey_source_name", "report_source_name"]:
                if column in df.columns:
                    df[column] = "Hidden"

            if "end_date" in df.columns:
                df["end_date"] = pd.NaT

            if "end_month_raw" in df.columns:
                df["end_month_raw"] = "Hidden"

            if "days_since_end" in df.columns:
                df["days_since_end"] = 30

            # is_final_month tidak disamarkan: nilainya boolean dan tidak
            # menunjukkan tanggal apa pun, sementara label barisnya butuh itu
            # agar tidak salah menulis "x hari lalu".

            masked[key] = df

    expiring = masked.get("expiring")

    if expiring is not None and not expiring.empty:

        expiring = anonymize_names(
            expiring,
            "partner_name",
            "Partner"
        )

        expiring["stakeholder"] = "Internal partnership · details hidden"

        # PIC adalah nama anggota AIESEC -> tidak boleh muncul di mode publik.
        if "pic_aiesec" in expiring.columns:
            expiring["pic_aiesec"] = None

        if "end_date" in expiring.columns:
            expiring["end_date"] = pd.NaT

        if "end_month_raw" in expiring.columns:
            expiring["end_month_raw"] = "Hidden"

        # Sisa hari ikut disamarkan: kalau dibiarkan asli, tanggal akhir
        # kontrak bisa dihitung ulang dari angka itu. Nilai penggantinya
        # tetap sejalan dengan tingkat urgensinya supaya kartu tidak
        # menjadi tidak konsisten dengan badge-nya.
        if "days_remaining" in expiring.columns and "urgency" in expiring.columns:
            demo_days = {
                metrics.URGENCY_CRITICAL: 5,
                metrics.URGENCY_WARNING: 20,
                metrics.URGENCY_NORMAL: 60,
            }
            expiring["days_remaining"] = expiring["urgency"].map(
                lambda level: demo_days.get(level, 60)
            )

        masked["expiring"] = expiring

    post_completeness = masked.get("post_completeness")

    if post_completeness is not None and not post_completeness.empty:

        post_completeness = post_completeness.copy()

        numeric_columns = post_completeness.select_dtypes(
            include="number"
        ).columns

        for column in numeric_columns:
            post_completeness[column] = demo_values(
                len(post_completeness),
                start=1
            )

        masked["post_completeness"] = post_completeness

    # Use fixed public demo rows rather than source-shaped partner lists.
    for key in ["financial_partner", "inkind_partner"]:
        masked[key] = pd.DataFrame([
            {"partner_name": f"Partner {index + 1:02d}",
             "records": index + 1, "amount": (8 - index) * 500_000}
            for index in range(6)
        ])
    masked["stakeholder"] = pd.DataFrame([
        {"stakeholder": f"Category {index + 1}",
         "partner_count": value, "share_percent": round(value / 30 * 100, 2)}
        for index, value in enumerate([12, 8, 6, 4])
    ])
    masked["watchlist"] = [
        {"name": f"Partner {index:02d}", "meta": "Demo partnership · details hidden",
         "tag": "Demo", "color": charts.PRIMARY}
        for index in range(1, 5)
    ]
    demo_month = pd.Timestamp.today().normalize().replace(day=1)
    masked["expiry_detail"] = pd.DataFrame([
        {
            "partner_name": f"Partner {index + 1:02d}",
            "end_month": demo_month + pd.DateOffset(months=index % 6),
            "expiry_category": (metrics.EXPIRY_THIS_MONTH if index == 0
                                else metrics.EXPIRY_SOON if index < 4
                                else metrics.EXPIRY_LATER),
            "is_active": True,
        }
        for index in range(8)
    ])
    demo_post_status = [
        metrics.POST_STATUS_COMPLETED, metrics.POST_STATUS_MISSING,
        metrics.POST_STATUS_NOT_REQUIRED, metrics.POST_STATUS_NO_END_DATE,
    ]
    masked["post_status"] = pd.DataFrame([
        {
            "partner_name": f"Partner {index + 1:02d}",
            "stakeholder": "Hidden", "end_month_raw": "Hidden",
            "end_date": pd.NaT, "is_due": index % 4 < 2,
            "is_final_month": False,
            "survey_status": demo_post_status[index % 4],
            "report_status": demo_post_status[(index + 1) % 4],
            "survey_submitted_early": False, "report_submitted_early": False,
            "survey_source_name": "Hidden", "report_source_name": "Hidden",
        }
        for index in range(8)
    ])
    masked["post_tracker"] = pd.DataFrame([
        {
            "partner_name": f"Partner {index + 1:02d}",
            "stakeholder": "Hidden", "end_month_raw": "Hidden",
            "end_date": pd.NaT, "days_since_end": 30,
            "is_final_month": False,
            "survey_status": (metrics.POST_STATUS_COMPLETED if index % 2 == 0
                              else metrics.POST_STATUS_MISSING),
            "report_status": (metrics.POST_STATUS_COMPLETED if index % 3 == 0
                              else metrics.POST_STATUS_MISSING),
            "survey_source_name": "Hidden", "report_source_name": "Hidden",
            "report_recorded": False,
            "is_fully_completed": index % 6 == 0,
        }
        for index in range(6)
    ])
    masked["post_summary"] = metrics.get_post_partnership_summary(masked["post_tracker"])
    masked["post_completeness"] = metrics.get_post_partnership_completeness(masked["post_tracker"])
    masked["expiring"] = pd.DataFrame([
        {
            "partner_name": f"Partner {index + 1:02d}",
            "stakeholder": "Demo partnership · details hidden",
            "pic_aiesec": None, "end_date": pd.NaT,
            "end_month_raw": "Hidden", "days_remaining": day,
            "months_remaining": 1,
            "urgency": (metrics.URGENCY_CRITICAL if day <= 7
                        else metrics.URGENCY_WARNING if day <= 30
                        else metrics.URGENCY_NORMAL),
        }
        for index, day in enumerate([5, 20, 60])
    ])

    return masked

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    selection, refresh = sidebar_controls()
    if refresh:
        load_frames.clear()
        load_post_partnership_frames.clear()

    today = pd.Timestamp.today().normalize()
    try:
        frames = load_frames(today)
    except Exception as exc:  # noqa: BLE001 - pesan ramah, detail tidak diteruskan
        # st.exception() sengaja TIDAK dipakai: stack trace gspread memuat
        # pesan API yang bisa berisi URL spreadsheet, dan aturan proyek ini
        # melarang rahasia atau URL privat muncul di UI maupun log.
        # Yang ditampilkan hanya penyebab yang bisa ditindaklanjuti.
        st.error(friendly_load_error(exc), icon=":material/error:")
        return

    # Sumber post-partnership dimuat terpisah: kalau National_1.2 atau PSC
    # bermasalah, dashboard tetap tampil dan hanya section terkait yang
    # memberi keterangan bahwa datanya belum tersedia.
    post_frames = load_post_partnership_frames()

    frame = periods.apply_period(selection, today=today, **frames)
    try:
        invoice_exceptions = get_store().get_invoice_exceptions() if IS_MEMBER else {}
    except SNDStoreError as exc:
        st.warning(str(exc))
        invoice_exceptions = {}
    data = build_view_data(
        frame,
        today,
        df_report=post_frames["df_report"],
        df_survey=post_frames["df_survey"],
        post_unavailable=post_frames["unavailable"],
        invoice_exceptions=invoice_exceptions,
    )
    # Anonymous tidak menerima data asli untuk visualisasi
    if not IS_MEMBER:
        data = mask_anonymous_data(data)

    header(frame, pd.Timestamp.now())
    kpi_row(data)
    alert_row(data)

    overview, market, partner, revenue, documents, snd = st.tabs(
        ["Ringkasan", "Market Research", "Partner & Funnel", "Revenue",
         "Dokumen & Kontrak", "S&D Tracker"]
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
    with snd:
        tab_snd_tracker(data)


if __name__ == "__main__":
    main()
