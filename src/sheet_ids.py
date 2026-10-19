"""sheet_ids.py — shared parsing of Google Spreadsheet IDs and URLs.

Satu implementasi untuk semua konfigurasi spreadsheet (S&D store maupun
Market Research database), supaya aturan "terima ID atau URL" tidak
berbeda-beda antar modul.

Modul ini TIDAK punya dependensi internal, jadi boleh diimpor dari layer
mana pun tanpa risiko import melingkar. Nilai konfigurasi tidak pernah
dicetak oleh modul ini; `sheet_id_hint()` hanya menjelaskan BENTUK nilai
yang salah, bukan isinya.
"""
from __future__ import annotations

import re

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))
_WRAPPERS = "'\"`<>()[]{} \t\r\n\u00a0"
# A published-to-web link exposes a different identifier that the API cannot open.
_PUBLISHED_URL = re.compile(r"/spreadsheets(?:/u/\d+)?/d/e/", re.IGNORECASE)
_SHEET_URL = re.compile(r"/spreadsheets(?:/u/\d+)?/d/(?!e/)([A-Za-z0-9_-]+)", re.IGNORECASE)
_QUERY_ID = re.compile(r"[?&#](?:id|key)=([A-Za-z0-9_-]+)")


def clean_setting(value: str | None) -> str:
    """Strip invisible characters and wrapping punctuation left by copy/paste."""
    return str(value or "").translate(_INVISIBLE).strip(_WRAPPERS)


def parse_sheet_id(value: str | None) -> str | None:
    """Accept a bare spreadsheet ID or a full Google Sheets URL.

    Tolerates surrounding whitespace, quotes or angle brackets added by pasting,
    `/u/0/` account prefixes, and any `?query`/`#fragment`. A published
    `/d/e/<token>/pubhtml` link is rejected: that token is not a spreadsheet ID.
    """
    text = clean_setting(value)
    if not text:
        return None
    match = _SHEET_URL.search(text)
    if match:
        return match.group(1)
    if _PUBLISHED_URL.search(text):
        return None
    match = _QUERY_ID.search(text)
    if match:
        return match.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_-]+", text) else None


def sheet_id_hint(value: str | None) -> str:
    """Non-sensitive description of an unparseable value, for error messages."""
    text = clean_setting(value)
    if not text:
        return "the value is empty after trimming quotes and whitespace"
    if _PUBLISHED_URL.search(text):
        return ("it is a published-to-web link (/d/e/...); open the spreadsheet and copy the "
                "normal /spreadsheets/d/<ID>/edit address instead")
    if "drive.google.com" in text.casefold() and "/folders/" in text.casefold():
        return "it is a Drive folder link, not a spreadsheet link"
    if "docs.google.com" in text.casefold() or text.casefold().startswith("http"):
        return "it looks like a URL but contains no /spreadsheets/d/<ID> path"
    invalid = sorted({character for character in text if not re.fullmatch(r"[A-Za-z0-9_-]", character)})
    if invalid:
        return (f"it is {len(text)} characters long and contains "
                f"{len(invalid)} character type(s) that a spreadsheet ID never has")
    return f"it is {len(text)} characters long"
