"""Google Sheets-backed S&D configuration for ephemeral Streamlit deployments.

Only a separate, explicitly configured sheet is writable. The ESSM source
spreadsheets and S&D proof Drive remain read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

import gspread
from google.oauth2.service_account import Credentials

from src.google_drive_service import service_account_info
from src.preparation import normalize_partner_function
from src.snd_store import (
    FUNCTIONS, INVOICE_REASONS, SNDStoreError, partner_key,
)
from src.snd_requirements import validate_requirements, requirement_record, clean_check_result

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TAB = "SND_Config"
HEADER = ["partner_key", "payload_json", "updated_at"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _function_assignment(data: dict) -> dict | None:
    function = normalize_partner_function(data.get("function"))
    if function:
        return {"function": function, "source": data.get("function_source", "manual")}
    # Compatibility with old rows that duplicated the function in requirements.
    legacy = {value for row in data.get("requirements", [])
              if (value := normalize_partner_function(row.get("function")))}
    return {"function": next(iter(legacy)), "source": "manual"} if len(legacy) == 1 else None


class GoogleSheetsSNDStore:
    """Store all settings for one partner in one JSON row of a dedicated Sheet."""

    def __init__(self, sheet_id: str, spreadsheet=None):
        self.sheet_id = sheet_id
        self._spreadsheet = spreadsheet
        self._worksheet = None
        self._rows = None

    def _book(self):
        if self._spreadsheet is None:
            try:
                credentials = Credentials.from_service_account_info(
                    service_account_info(), scopes=[SHEETS_SCOPE]
                )
                self._spreadsheet = gspread.authorize(credentials).open_by_key(self.sheet_id)
            except Exception as exc:
                raise SNDStoreError(
                    "S&D config sheet tidak dapat dibuka. Periksa ID dan akses Editor service account."
                ) from exc
        return self._spreadsheet

    def _tab(self, create: bool = False):
        if self._worksheet is not None:
            return self._worksheet
        try:
            self._worksheet = self._book().worksheet(TAB)
        except gspread.exceptions.WorksheetNotFound:
            if not create:
                return None
            try:
                self._worksheet = self._book().add_worksheet(title=TAB, rows=1000, cols=3)
                self._worksheet.update(range_name="A1:C1", values=[HEADER], value_input_option="RAW")
            except Exception as exc:
                raise SNDStoreError("S&D config tab tidak dapat dibuat.") from exc
        except Exception as exc:
            raise SNDStoreError("S&D config tab tidak dapat dibaca.") from exc
        return self._worksheet

    def _load(self) -> dict[str, tuple[int, dict]]:
        if self._rows is not None:
            return self._rows
        tab = self._tab()
        if tab is None:
            self._rows = {}
            return self._rows
        try:
            values = tab.get_all_values()
        except Exception as exc:
            raise SNDStoreError("S&D config sheet tidak dapat dibaca.") from exc
        if values and values[0][:3] != HEADER:
            raise SNDStoreError("Header SND_Config tidak sesuai.")
        rows = {}
        try:
            for index, row in enumerate(values[1:], start=2):
                if not row or not row[0]:
                    continue
                key = row[0]
                if key in rows:
                    raise ValueError("duplicate key")
                rows[key] = (index, json.loads(row[1]))
        except (IndexError, ValueError, TypeError) as exc:
            raise SNDStoreError("Isi SND_Config tidak valid; periksa baris JSON dan duplikat.") from exc
        self._rows = rows
        return rows

    def _data(self, name: str) -> dict:
        return dict(self._load().get(partner_key(name), (0, {}))[1])

    def _save(self, name: str, data: dict) -> None:
        data = dict(data)
        assignment = _function_assignment(data)
        if assignment:
            data.update(function=assignment["function"], function_source=assignment["source"])
        if "requirements" in data:
            data["requirements"] = [requirement_record(row) for row in data["requirements"]]
        if "check" in data:
            data["check"] = {**data["check"], "result": clean_check_result(data["check"]["result"])}
        key = partner_key(name)
        row_index = self._load().get(key, (0, {}))[0]
        tab = self._tab(create=True)
        values = [key, json.dumps(data, ensure_ascii=False), _now()]
        try:
            if row_index:
                tab.update(
                    range_name=f"A{row_index}:C{row_index}",
                    values=[values], value_input_option="RAW",
                )
            else:
                tab.append_row(values, value_input_option="RAW")
                row_index = len(tab.get_all_values())
        except Exception as exc:
            raise SNDStoreError("S&D config sheet tidak dapat disimpan.") from exc
        self._rows[key] = (row_index, data)

    def list_requirements(self, partner_name: str) -> list[dict]:
        return sorted(
            [requirement_record(row) for row in self._data(partner_name).get("requirements", [])],
            key=lambda row: (row["kind"] != "supply", row.get("created_at", ""), row["id"]),
        )

    def requirement_counts(self) -> dict[str, int]:
        return {key: len(data.get("requirements", [])) for key, (_, data) in self._load().items()}

    def replace_requirements(
        self, partner_name: str, function: str, requirements: list[dict], *, function_source: str = "manual"
    ) -> list[dict]:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner harus ELDs, EwAs, atau SS.")
        if function_source not in {"manual", "essm"}:
            raise ValueError("Sumber fungsi partner tidak valid.")
        validated = validate_requirements(requirements)
        data = self._data(partner_name)
        existing = _function_assignment(data)
        if existing and existing["source"] == "manual" and function_source == "essm":
            function = existing["function"]
            function_source = "manual"
        old = {item["id"]: item for item in data.get("requirements", [])}
        now = _now()
        data["partner_name"] = partner_name
        data["function"] = function
        data["function_source"] = function_source
        data["requirements"] = [
            {**item, "partner_key": partner_key(partner_name),
             "partner_name": partner_name,
             "created_at": old.get(item["id"], {}).get("created_at", now),
             "updated_at": now}
            for item in validated
        ]
        data.pop("check", None)
        self._save(partner_name, data)
        return self.list_requirements(partner_name)

    def get_partner_functions(self) -> dict[str, str]:
        return {key: value["function"] for key, value in self.get_partner_function_assignments().items()}

    def get_partner_function_assignments(self) -> dict[str, dict]:
        return {key: assignment for key, (_, data) in self._load().items()
                if (assignment := _function_assignment(data))}

    def set_partner_function(self, partner_name: str, function: str) -> None:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner tidak valid.")
        data = self._data(partner_name)
        data["function"] = function
        data["function_source"] = "manual"
        data.pop("check", None)
        self._save(partner_name, data)

    def get_invoice_exceptions(self) -> dict[str, str]:
        return {
            key: data["invoice_exception"] for key, (_, data) in self._load().items()
            if data.get("invoice_exception") in INVOICE_REASONS
        }

    def set_invoice_exception(self, partner_name: str, reason: str | None) -> None:
        if reason is not None and reason not in INVOICE_REASONS:
            raise ValueError("Alasan pengecualian Invoice tidak valid.")
        data = self._data(partner_name)
        if reason is None:
            data.pop("invoice_exception", None)
        else:
            data["invoice_exception"] = reason
        self._save(partner_name, data)

    def save_check(self, partner_name: str, signature: str, result: dict) -> None:
        data = self._data(partner_name)
        data["check"] = {"signature": signature, "result": clean_check_result(result), "checked_at": _now()}
        self._save(partner_name, data)

    def get_check(self, partner_name: str, signature: str) -> dict | None:
        check = self._data(partner_name).get("check", {})
        return clean_check_result(check["result"]) if check.get("signature") == signature else None
