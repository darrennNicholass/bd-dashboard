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
from google.oauth2.service_account import Credentials

from .sheet_ids import parse_sheet_id, sheet_id_hint

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

# Market Research database baru (berlaku mulai 1 Oktober 2026). Menerima ID
# spreadsheet maupun URL penuh. Active ESSM TIDAK diubah: ia tetap sumber
# data historis sampai September 2026.
ENV_MARKET_RESEARCH_DB = "MARKET_RESEARCH_DB_URL"
# Opsional: nama tab yang dipakai. Boleh beberapa, dipisah koma. Kalau tidak
# diisi, semua tab dipindai dan hanya tab yang skemanya cocok yang dipakai.
ENV_MARKET_RESEARCH_WORKSHEET = "MARKET_RESEARCH_DB_WORKSHEET"

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
    # Read only the two simple values owned by this module.  The same .env may
    # contain pretty-printed multiline GOOGLE_SERVICE_ACCOUNT JSON for the S&D
    # tracker; asking a generic dotenv parser to consume that block emits parse
    # warnings and can truncate the credential to just "{".
    if not ENV_PATH.is_file():
        return
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    wanted = {
        ENV_ACTIVE_ESSM,
        ENV_NATIONAL_SOURCE,
        ENV_MARKET_RESEARCH_DB,
        ENV_MARKET_RESEARCH_WORKSHEET,
    }
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in wanted or os.getenv(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            os.environ[name] = value


def env_status() -> dict[str, bool]:
    """Laporkan APAKAH env var terisi, tanpa membocorkan nilainya.

    Returns:
        dict nama_env -> True/False
    """
    load_environment()
    return {
        ENV_ACTIVE_ESSM: bool(os.getenv(ENV_ACTIVE_ESSM, "").strip()),
        ENV_NATIONAL_SOURCE: bool(os.getenv(ENV_NATIONAL_SOURCE, "").strip()),
        ENV_MARKET_RESEARCH_DB: bool(_optional_setting(ENV_MARKET_RESEARCH_DB)),
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


# ===========================================================================
# MARKET RESEARCH DATABASE (sumber baru, berlaku 1 Oktober 2026)
# ===========================================================================
#
# Spreadsheet ini TERPISAH dari Active ESSM dan strukturnya berbeda. Modul ini
# hanya membuka dan membaca; pemetaan skema ada di src/mr_sources.py.
#
# Kegagalan dibedakan dengan sengaja supaya dashboard tidak pernah diam-diam
# berpindah ke sumber yang salah:
#   MRNotConfiguredError  -> MARKET_RESEARCH_DB_URL belum diisi
#   MRConfigValueError    -> nilainya ada tapi bukan ID/URL spreadsheet
#   MRAccessDeniedError   -> service account belum diberi akses (403/404)
#   MRWorksheetError      -> nama tab tidak ada (daftar tab disebutkan)
#   MRAPIError            -> kuota, jaringan, atau API Sheets dimatikan
# Spreadsheet KOSONG bukan error: itu dilaporkan sebagai nol baris.


class MRSourceError(RuntimeError):
    """Kesalahan sumber Market Research yang aman ditampilkan ke pengguna.

    Pesannya tidak pernah memuat URL, ID spreadsheet, atau kredensial.
    """


class MRNotConfiguredError(MRSourceError):
    """MARKET_RESEARCH_DB_URL belum tersedia di .env maupun Streamlit Secrets."""


class MRConfigValueError(MRSourceError):
    """Nilai konfigurasi ada, tapi bukan ID atau URL spreadsheet yang sah."""


class MRAccessDeniedError(MRSourceError):
    """Service account tidak punya akses baca ke spreadsheet tersebut."""


class MRWorksheetError(MRSourceError):
    """Tab yang diminta tidak ada di spreadsheet."""


class MRAPIError(MRSourceError):
    """Google Sheets API menolak permintaan (kuota, API mati, jaringan)."""


def _optional_setting(name: str) -> str:
    """Ambil konfigurasi opsional dari env/.env lalu Streamlit Secrets.

    Mengembalikan string kosong kalau belum diisi. Nilainya tidak pernah
    dicetak oleh modul ini.
    """
    load_environment()
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets[name]).strip()
    except (KeyError, FileNotFoundError, AttributeError):
        return ""


def market_research_db_configured() -> bool:
    """True kalau sumber Market Research baru sudah dikonfigurasi."""
    return bool(_optional_setting(ENV_MARKET_RESEARCH_DB))


def market_research_db_id() -> str:
    """ID spreadsheet Market Research database.

    Raises:
        MRNotConfiguredError: konfigurasi belum ada.
        MRConfigValueError: nilainya bukan ID/URL spreadsheet.
    """
    raw = _optional_setting(ENV_MARKET_RESEARCH_DB)
    if not raw:
        raise MRNotConfiguredError(
            f"Market Research database belum dikonfigurasi. Isi {ENV_MARKET_RESEARCH_DB} "
            "dengan ID atau URL spreadsheet Market Research yang baru, lalu bagikan "
            "spreadsheet itu ke service account sebagai Viewer."
        )
    sheet_id = parse_sheet_id(raw)
    if not sheet_id:
        raise MRConfigValueError(
            f"{ENV_MARKET_RESEARCH_DB} harus berupa ID spreadsheet atau URL Google Sheets: "
            f"{sheet_id_hint(raw)}."
        )
    return sheet_id


def market_research_db_worksheet_names() -> tuple[str, ...]:
    """Tab yang dipilih lewat konfigurasi. Kosong = pindai semua tab."""
    raw = _optional_setting(ENV_MARKET_RESEARCH_WORKSHEET)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _classify_api_error(exc: gspread.exceptions.APIError) -> MRSourceError:
    """Terjemahkan APIError menjadi kesalahan yang bisa ditindaklanjuti."""
    status = str(exc.error.get("status", "")) if isinstance(exc.error, dict) else ""
    code = getattr(exc, "code", None)
    if status == "SERVICE_DISABLED":
        return MRAPIError(
            "Google Sheets API belum diaktifkan di project service account. "
            "Aktifkan Google Sheets API, tunggu beberapa menit, lalu coba lagi."
        )
    if code in (401, 403) or status in ("PERMISSION_DENIED", "UNAUTHENTICATED"):
        return MRAccessDeniedError(
            "Service account belum punya akses baca ke Market Research database. "
            "Bagikan spreadsheet itu ke alamat service account dashboard "
            "(minimal Viewer), bukan ke akun pribadi."
        )
    if code == 404 or status == "NOT_FOUND":
        return MRAccessDeniedError(
            "Market Research database tidak ditemukan dengan ID yang dikonfigurasi. "
            f"Periksa kembali nilai {ENV_MARKET_RESEARCH_DB} dan akses service account."
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return MRAPIError(
            "Kuota Google Sheets API terlampaui. Coba lagi beberapa menit lagi."
        )
    return MRAPIError(
        f"Google Sheets API gagal merespons saat membaca Market Research database "
        f"(kode {code if code is not None else 'tidak diketahui'})."
    )


@lru_cache(maxsize=1)
def open_market_research_db() -> gspread.Spreadsheet:
    """Buka Market Research database baru (read-only, lewat client yang sama)."""
    sheet_id = market_research_db_id()
    try:
        return get_client().open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound as exc:
        raise MRAccessDeniedError(
            "Market Research database tidak ditemukan atau belum dibagikan ke "
            "service account dashboard. Bagikan spreadsheet itu sebagai Viewer."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise _classify_api_error(exc) from exc


def market_research_db_worksheets() -> list[gspread.Worksheet]:
    """Worksheet yang akan dibaca dari Market Research database.

    Kalau MARKET_RESEARCH_DB_WORKSHEET diisi, hanya tab itu yang dipakai dan
    nama tab yang salah menimbulkan MRWorksheetError berisi daftar tab yang
    tersedia. Kalau tidak diisi, semua tab dikembalikan dan penyaring skema
    di mr_sources.py yang memutuskan tab mana yang berisi data MR.
    """
    spreadsheet = open_market_research_db()
    try:
        available = spreadsheet.worksheets()
    except gspread.exceptions.APIError as exc:
        raise _classify_api_error(exc) from exc

    requested = market_research_db_worksheet_names()
    if not requested:
        return list(available)

    by_name = {worksheet.title.strip().casefold(): worksheet for worksheet in available}
    selected: list[gspread.Worksheet] = []
    for name in requested:
        worksheet = by_name.get(name.strip().casefold())
        if worksheet is None:
            raise MRWorksheetError(
                f"Tab '{name}' tidak ada di Market Research database. "
                f"Tab yang tersedia: {', '.join(sorted(item.title for item in available))}."
            )
        selected.append(worksheet)
    return selected


def read_market_research_db_grids() -> list[tuple[str, list[list[str]]]]:
    """Baca raw values setiap tab Market Research database.

    Returns:
        list pasangan (nama_tab, grid). List kosong hanya terjadi kalau
        spreadsheet-nya benar-benar tidak punya tab.
    """
    grids: list[tuple[str, list[list[str]]]] = []
    for worksheet in market_research_db_worksheets():
        try:
            grids.append((worksheet.title, read_worksheet_values(worksheet)))
        except gspread.exceptions.APIError as exc:
            raise _classify_api_error(exc) from exc
    return grids


def market_research_db_access_report() -> dict[str, object]:
    """Pemeriksaan akses READ-ONLY yang tidak menulis apa pun.

    Dipakai oleh `python -m src.mr_sources preflight` dan oleh diagnostik
    dashboard. Tidak pernah memuat URL, ID, atau kredensial di hasilnya.
    """
    report: dict[str, object] = {
        "configured": market_research_db_configured(),
        "ok": False,
        "error": None,
        "error_kind": None,
        "spreadsheet_title": None,
        "worksheets": [],
        "selected_worksheets": list(market_research_db_worksheet_names()),
        "service_account": service_account_email(),
    }
    if not report["configured"]:
        report["error_kind"] = "not_configured"
        report["error"] = (
            f"{ENV_MARKET_RESEARCH_DB} belum diisi di .env maupun Streamlit Secrets."
        )
        return report
    try:
        spreadsheet = open_market_research_db()
        report["spreadsheet_title"] = spreadsheet.title
        report["worksheets"] = [worksheet.title for worksheet in spreadsheet.worksheets()]
        # Memastikan nama tab yang dikonfigurasi benar-benar ada.
        market_research_db_worksheets()
    except MRSourceError as exc:
        report["error_kind"] = type(exc).__name__
        report["error"] = str(exc)
        return report
    except gspread.exceptions.APIError as exc:  # pragma: no cover - dijaga ganda
        classified = _classify_api_error(exc)
        report["error_kind"] = type(classified).__name__
        report["error"] = str(classified)
        return report
    report["ok"] = True
    return report


def service_account_email() -> str | None:
    """Alamat service account yang dipakai dashboard (bukan rahasia).

    Diperlukan untuk pesan "bagikan spreadsheet ke alamat ini". Kunci privat
    tidak pernah dibaca ke output.
    """
    if credentials_exist():
        try:
            import json

            with CREDENTIALS_PATH.open(encoding="utf-8") as handle:
                return json.load(handle).get("client_email")
        except (OSError, ValueError):
            return None
    try:
        return str(dict(st.secrets["gcp_service_account"]).get("client_email") or "") or None
    except (KeyError, FileNotFoundError, AttributeError, TypeError):
        return None
