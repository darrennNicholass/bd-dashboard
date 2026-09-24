"""
sheets.py — EXTRACTION LAYER

Satu-satunya file yang boleh berbicara langsung dengan Google Sheets API.

Tanggung jawab:
  - membaca environment variables (.env)
  - autentikasi Google Service Account
  - membuka Active ESSM
  - membuka National Mirror
  - mengambil worksheet & nilai mentah (raw values)

BUKAN tanggung jawab file ini:
  - cleaning / transformasi  -> src/preparation.py
  - perhitungan KPI          -> src/metrics.py
  - visualisasi / UI         -> src/charts.py, app.py

ATURAN DATA GOVERNANCE:
  National data HANYA boleh diambil dari NATIONAL_SOURCE_URL (mirror).
  File ini tidak pernah menyimpan atau mengakses URL National ESSM asli.
  Nilai URL tidak pernah di-print oleh modul ini.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os

import gspread
import streamlit as st
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Konstanta path & konfigurasi
# ---------------------------------------------------------------------------

# parents[1] = folder root proyek (karena file ini ada di <root>/src/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"

# Nama environment variable. Hanya NAMA yang boleh muncul di log, bukan nilainya.
ENV_ACTIVE_ESSM = "ACTIVE_ESSM_URL"
ENV_NATIONAL_SOURCE = "NATIONAL_SOURCE_URL"

# Tiga tab Market Research di Active ESSM.
# Sudah DIKONFIRMASI manual oleh BD (bukan hasil tebakan), lihat Phase 1.
ACTIVE_MR_WORKSHEETS = (
    "[ELDs] Market Research",
    "[EWAs] Market Research",
    "[SS] Market Research",
)

# Tab National Mirror, hasil inventarisasi Phase 1.
NATIONAL_PARTNER_WORKSHEET = "National_1.1"    # partner / sales funnel / dokumen
NATIONAL_FINANCIAL_WORKSHEET = "National_1.3"  # financial revenue
NATIONAL_INKIND_WORKSHEET = "National_1.4"     # in-kind value

# Tab post-partnership. Nama tab dikonfirmasi lewat list_worksheet_names()
# pada mirror, bukan hasil tebakan:
#   National_1.2 -> Post-Partnership Report (kolom "Link" berisi dokumennya)
#   PSC          -> respons Partnership Survey (satu baris = satu responden)
NATIONAL_REPORT_WORKSHEET = "National_1.2"
NATIONAL_SURVEY_WORKSHEET = "PSC"

# Scope READ-ONLY.
# Kita hanya perlu membaca, jadi kita minta izin seminimal mungkin
# (principle of least privilege). Dashboard ini tidak boleh menulis ke ESSM.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def load_environment() -> None:
    """Muat .env dari root proyek.

    override=False supaya environment variable yang sudah di-set di sistem
    (misalnya nanti di Streamlit Cloud) tidak tertimpa oleh file .env lokal.
    """
    load_dotenv(dotenv_path=ENV_PATH, override=False)


def env_status() -> dict[str, bool]:
    """Laporkan APAKAH env var terisi, tanpa membocorkan nilainya.

    Returns:
        dict nama_env -> True/False
    """
    load_environment()
    return {
        ENV_ACTIVE_ESSM: bool(os.getenv(ENV_ACTIVE_ESSM, "").strip()),
        ENV_NATIONAL_SOURCE: bool(os.getenv(ENV_NATIONAL_SOURCE, "").strip()),
    }


def credentials_exist() -> bool:
    """True jika credentials.json ada. Isinya tidak pernah dibaca ke output."""
    return CREDENTIALS_PATH.is_file()


def _require_env(name: str) -> str:
    """
    Ambil konfigurasi wajib.

    Prioritas:
    1. Environment variable / .env untuk development lokal
    2. Streamlit Secrets untuk deployment
    """
    load_environment()

    value = os.getenv(name, "").strip()

    if value:
        return value

    try:
        value = str(st.secrets[name]).strip()
        if value:
            return value
    except (KeyError, FileNotFoundError):
        pass

    raise RuntimeError(
        f"Konfigurasi '{name}' belum tersedia di environment "
        "variable maupun Streamlit Secrets."
    )


# ---------------------------------------------------------------------------
# Autentikasi
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_client() -> gspread.Client:
    """
    Autentikasi Google Service Account.

    Lokal:
        credentials.json

    Streamlit Cloud:
        st.secrets["gcp_service_account"]
    """

    # Development lokal
    if credentials_exist():
        creds = Credentials.from_service_account_file(
            str(CREDENTIALS_PATH),
            scopes=SCOPES,
        )

        return gspread.authorize(creds)

    # Streamlit Cloud
    try:
        service_account_info = dict(st.secrets["gcp_service_account"])

        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES,
        )

        return gspread.authorize(creds)

    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError(
            "Google Service Account credentials tidak ditemukan. "
            "Gunakan credentials.json untuk lokal atau "
            "gcp_service_account di Streamlit Secrets."
        ) from exc


# ---------------------------------------------------------------------------
# Koneksi spreadsheet
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def open_active_essm() -> gspread.Spreadsheet:
    """Buka Active ESSM (sumber Market Research)."""
    load_environment()
    return get_client().open_by_url(_require_env(ENV_ACTIVE_ESSM))


@lru_cache(maxsize=1)
def open_national_mirror() -> gspread.Spreadsheet:
    """Buka National Mirror.

    HANYA mirror. Jangan pernah menambahkan fungsi yang membuka
    National ESSM asli.
    """
    load_environment()
    return get_client().open_by_url(_require_env(ENV_NATIONAL_SOURCE))


# ---------------------------------------------------------------------------
# Helper worksheet
# ---------------------------------------------------------------------------


def list_worksheet_names(spreadsheet: gspread.Spreadsheet) -> list[str]:
    """Daftar nama semua tab dalam sebuah spreadsheet."""
    return [ws.title for ws in spreadsheet.worksheets()]


def get_worksheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    """Ambil worksheet berdasarkan nama.

    Kalau nama tidak ada, error-nya kita perjelas dengan menampilkan
    daftar nama yang tersedia -> mempercepat debugging.
    """
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound as exc:
        available = ", ".join(list_worksheet_names(spreadsheet))
        raise gspread.WorksheetNotFound(
            f"Worksheet '{title}' tidak ditemukan. Tab yang tersedia: {available}"
        ) from exc


def read_worksheet_values(worksheet: gspread.Worksheet) -> list[list[str]]:
    """Ambil SEMUA nilai worksheet sebagai list of list (raw, apa adanya).

    Kita sengaja memakai get_all_values(), bukan get_all_records(), karena:
      - get_all_records() menuntut header unik di baris 1
      - struktur ESSM (khususnya National) belum kita ketahui
    Raw values dulu, baru kita tentukan header row setelah inspeksi.
    """
    return worksheet.get_all_values()


def read_worksheet_formulas(worksheet: gspread.Worksheet) -> list[list[str]]:
    """Ambil RUMUS worksheet, bukan hasilnya.

    Dipakai untuk memeriksa konfigurasi IMPORTRANGE di National Mirror.

    PERINGATAN: rumus IMPORTRANGE memuat URL spreadsheet sumber.
    Pemanggil WAJIB menyensor bagian itu sebelum mencetak apa pun.
    """
    return worksheet.get_values(value_render_option="FORMULA")


def read_range(
    worksheet: gspread.Worksheet,
    a1_range: str,
    value_render_option: str | None = None,
) -> list[list[str]]:
    """Ambil sebagian kecil worksheet memakai notasi A1, contoh "A1:F5".

    Berguna untuk sampling/inspeksi: payload API kecil dan cepat,
    tidak perlu menarik seluruh sheet.

    value_render_option:
        None / "FORMATTED_VALUE"   -> apa yang DILIHAT user (mis. "18/02/2026")
        "UNFORMATTED_VALUE"        -> nilai mentah Sheets (tanggal jadi angka serial)
        "FORMULA"                  -> rumusnya, bukan hasilnya

    Membandingkan FORMATTED vs UNFORMATTED itu penting untuk kolom tanggal
    dan kolom uang, karena format tampilan bisa menyesatkan.
    """
    if value_render_option is None:
        return worksheet.get_values(a1_range)
    return worksheet.get_values(a1_range, value_render_option=value_render_option)


def column_letter(index: int) -> str:
    """Ubah nomor kolom 1-based menjadi huruf A1: 1 -> A, 27 -> AA, 73 -> BU."""
    if index < 1:
        raise ValueError("Nomor kolom dimulai dari 1.")
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def open_active_mr_worksheets() -> list[gspread.Worksheet]:
    """Ambil ketiga tab Market Research dari Active ESSM, sesuai urutan konstanta."""
    spreadsheet = open_active_essm()
    return [get_worksheet(spreadsheet, name) for name in ACTIVE_MR_WORKSHEETS]


def read_national_values(title: str) -> list[list[str]]:
    """Baca satu tab National Mirror sebagai raw values.

    Gagal keras kalau tab tidak ada: pemanggil yang memang boleh berjalan
    tanpa tab tersebut harus memakai try_read_national_values().
    """
    return read_worksheet_values(get_worksheet(open_national_mirror(), title))


def try_read_national_values(title: str) -> list[list[str]] | None:
    """Sama seperti read_national_values(), tapi None kalau tab tidak ada.

    Dipakai untuk tab post-partnership (National_1.2 & PSC): dashboard harus
    tetap hidup dan menampilkan empty state kalau tab itu belum dibuat atau
    sudah diganti nama, bukan menampilkan stack trace.

    Kegagalan lain (kredensial, kuota, jaringan) TIDAK ditelan di sini —
    itu masalah yang perlu diketahui pemanggil.
    """
    try:
        return read_national_values(title)
    except gspread.WorksheetNotFound:
        return None
