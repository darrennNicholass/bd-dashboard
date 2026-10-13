"""Google Sheets-backed S&D configuration for ephemeral Streamlit deployments.

Only a separate, explicitly configured sheet is writable. The ESSM source
spreadsheets and S&D proof Drive remain read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import copy
import json
import random
import re
import threading
import time
from uuid import uuid4

import gspread
from gspread.utils import ValueRenderOption
from google.oauth2.service_account import Credentials

from src.google_drive_service import DriveServiceError, service_account_info
from src.preparation import normalize_partner_function
from src.snd_store import (
    FUNCTIONS, INVOICE_REASONS, SNDStoreError, SNDConflictError, CONFLICT_MESSAGE, partner_key,
)
from src.snd_requirements import (
    validate_requirements, requirement_record, clean_check_result, requirements_revision,
)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TAB = "SND_Config"
HEADER = ["partner_key", "payload_json", "updated_at"]
EVENT_MARKER = "__snd_event__"
CUTOVER_KEY = "__snd_cutover__"
CUTOVER_MARKER = "__snd_cutover_receipt__"
PATCH_FIELDS = {
    "partner_name", "function", "function_source", "requirements", "check",
    "invoice_exception", "migration_metadata",
}
CONFIG_FIELDS = {"requirements", "function", "function_source"}
DIRECT_PATCH_FIELDS = {"invoice_exception", "check"}
CELL_LIMIT = 45000  # Google Sheets rejects cells above 50,000 characters.
BATCH_ROWS = 500
BATCH_CHARS = 1_500_000  # Stay below the recommended 2 MB request payload.
# Streamlit reruns the whole script on every widget interaction. The service
# account is one Sheets "user" (60 reads/minute), so reruns share one handle and
# a short-lived copy of the journal. Every write invalidates it immediately.
VALUE_CACHE_SECONDS = 10.0
REQUEST_TIMEOUT = (10, 60)  # (connect, read) seconds; never hang a session forever.
ATTEMPTS = 3

NOT_VERIFIED_MESSAGE = (
    "S&D storage has not passed the verified migration cutover. "
    "Check SND_CUTOVER_ID and complete the migration before enabling production."
)
NOT_INITIALIZED_MESSAGE = (
    "The S&D storage spreadsheet has no initialized SND_Config worksheet, so it has not passed "
    "the verified migration. Run the cutover command with --apply before enabling production."
)
SAVE_UNCONFIRMED = "S&D save could not be confirmed. Reload saved requirements before retrying."
IMPORT_UNCONFIRMED = (
    "S&D import could not be confirmed. Rerun the same cutover command; rows that already "
    "match are skipped and nothing is overwritten."
)


def _revision(data: dict) -> str:
    return requirements_revision(data.get("requirements", []), _function_assignment(data))


def canonical_projection(partners: dict[str, dict]) -> dict[str, dict]:
    """Canonical current state shared by migration and receipt verification."""
    projection = {}
    for partner in sorted(partners):
        data = partners[partner]
        current = {key: data[key] for key in sorted(PATCH_FIELDS) if key in data}
        if "requirements" in current:
            current["requirements"] = sorted(
                current["requirements"], key=lambda row: str(row.get("id", "")),
            )
        projection[partner] = current
    return projection


def projection_sha256(partners: dict[str, dict]) -> str:
    encoded = json.dumps(canonical_projection(partners), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _function_assignment(data: dict) -> dict | None:
    function = normalize_partner_function(data.get("function"))
    if function:
        source = data.get("function_source")
        return {"function": function, "source": source if source in {"manual", "essm"} else "legacy"}
    # Compatibility with old rows that duplicated the function in requirements.
    legacy = {value for row in data.get("requirements", [])
              if (value := normalize_partner_function(row.get("function")))}
    return {"function": next(iter(legacy)), "source": "legacy"} if len(legacy) == 1 else None


def _normalized(data: dict) -> dict:
    data = dict(data)
    assignment = _function_assignment(data)
    if assignment:
        data.update(function=assignment["function"], function_source=assignment["source"])
    if "requirements" in data:
        data["requirements"] = [requirement_record(row) for row in data["requirements"]]
    if "check" in data:
        data["check"] = {**data["check"], "result": clean_check_result(data["check"]["result"])}
    return data


def _api_status(exc: BaseException) -> int | None:
    if isinstance(exc, gspread.exceptions.APIError):
        try:
            return int(exc.code)
        except (TypeError, ValueError):
            return None
    return None


def _reason(exc: BaseException) -> str:
    """Actionable description of a Sheets failure without IDs, URLs, or credentials."""
    status = _api_status(exc)
    if isinstance(exc, gspread.exceptions.SpreadsheetNotFound) or status == 404:
        return "the spreadsheet was not found; check SND_CONFIG_SHEET_ID"
    if isinstance(exc, PermissionError) or status == 403:
        return "the service account has no access; share the spreadsheet with it as Editor"
    if status == 429:
        return "the Google Sheets rate limit was reached; wait one minute and try again"
    if status is not None and status >= 500:
        return "Google Sheets is temporarily unavailable; try again shortly"
    if isinstance(exc, OSError):  # requests timeouts and connection errors are OSErrors.
        return "Google Sheets could not be reached in time"
    return "Google Sheets returned an unexpected response"


def _definitely_rejected(exc: BaseException) -> bool:
    """A 4xx response (except timeout) means the write was not applied."""
    status = _api_status(exc)
    return (isinstance(exc, (PermissionError, gspread.exceptions.SpreadsheetNotFound))
            or (status is not None and 400 <= status < 500 and status != 408))


def _transient(exc: BaseException, *, write: bool) -> bool:
    status = _api_status(exc)
    if status == 429:
        return True  # Rejected by quota, so repeating it cannot duplicate a write.
    if write:
        return False  # A 5xx/timeout write may already be applied; never repeat it blindly.
    if status is not None:
        return status >= 500
    return isinstance(exc, OSError) and not isinstance(exc, PermissionError)


def _retry(operation, *, write: bool = False):
    """Bounded jittered backoff (about 1 s then 2 s) so a UI rerun never stalls for long."""
    for attempt in range(ATTEMPTS):
        try:
            return operation()
        except Exception as exc:
            if attempt == ATTEMPTS - 1 or not _transient(exc, write=write):
                raise
            time.sleep(min(2 ** attempt, 4) + random.uniform(0, 0.5))


class _SharedSheet:
    """Process-wide handles and a short-lived journal copy for one spreadsheet."""

    def __init__(self):
        self.lock = threading.Lock()
        self.spreadsheet = None
        self.worksheet = None
        self.values = None
        self.fetched_at = 0.0
        self.generation = 0

    def cached(self) -> list[list[str]] | None:
        with self.lock:
            if self.values is not None and time.monotonic() - self.fetched_at < VALUE_CACHE_SECONDS:
                return copy.deepcopy(self.values)
        return None

    def current_generation(self) -> int:
        with self.lock:
            return self.generation

    def remember(self, values: list[list[str]], generation: int) -> None:
        with self.lock:
            # A write that finished while this read was in flight makes the read stale.
            if generation == self.generation:
                self.values = copy.deepcopy(values)
                self.fetched_at = time.monotonic()

    def invalidate(self, *, handles: bool = False) -> None:
        with self.lock:
            self.generation += 1
            self.values = None
            if handles:
                self.spreadsheet = None
                self.worksheet = None


_SHARED: dict[str, _SharedSheet] = {}
_SHARED_GUARD = threading.Lock()


def _shared_sheet(sheet_id: str) -> _SharedSheet:
    with _SHARED_GUARD:
        return _SHARED.setdefault(sheet_id, _SharedSheet())


def _open_spreadsheet(sheet_id: str):
    credentials = Credentials.from_service_account_info(service_account_info(), scopes=[SHEETS_SCOPE])
    client = gspread.authorize(credentials)
    client.set_timeout(REQUEST_TIMEOUT)
    return client.open_by_key(sheet_id)


class GoogleSheetsSNDStore:
    """Append changes to a dedicated Sheet; old rows remain recoverable.

    Legacy snapshot rows are the baseline. Versioned patches are replayed in
    physical append order. Competing edits to the same baseline cannot both
    apply, even when separate application processes append simultaneously.
    """

    storage_label = "Google Sheets (durable storage)"

    def __init__(self, sheet_id: str, spreadsheet=None, *, expected_cutover_id: str | None = None,
                 read_only: bool = False):
        self.sheet_id = sheet_id
        self.storage_identity = f"google_sheets:{sheet_id}"
        self.expected_cutover_id = expected_cutover_id
        self.read_only = read_only
        self._spreadsheet = spreadsheet
        # Injected spreadsheets (tests, migration verifier) always read fresh values.
        self._shared = _shared_sheet(sheet_id) if spreadsheet is None else None
        self._worksheet = None
        self._rows = None
        self._accepted_events = set()
        self._cutover_receipts = {}
        self._journal_rows = []
        self._has_gap = False

    @property
    def _may_initialize(self) -> bool:
        # Production is enabled only by a receipt the migration wrote, so it
        # never creates a worksheet or header; dry runs never write at all.
        return not self.read_only and not self.expected_cutover_id

    def _invalidate(self) -> None:
        if self._shared is not None:
            self._shared.invalidate()

    def _forget_handles(self) -> None:
        self._worksheet = None
        if self._shared is not None:
            self._spreadsheet = None
            self._shared.invalidate(handles=True)

    def _book(self):
        if self._spreadsheet is None and self._shared is not None:
            self._spreadsheet = self._shared.spreadsheet
        if self._spreadsheet is None:
            try:
                book = _retry(lambda: _open_spreadsheet(self.sheet_id))
            except DriveServiceError as exc:
                raise SNDStoreError(f"S&D storage is unavailable: {exc}") from exc
            except Exception as exc:
                raise SNDStoreError(
                    f"S&D storage spreadsheet could not be opened: {_reason(exc)}."
                ) from exc
            self._spreadsheet = book
            if self._shared is not None:
                self._shared.spreadsheet = book
        return self._spreadsheet

    def _tab(self, create: bool = False):
        if self._worksheet is None and self._shared is not None:
            self._worksheet = self._shared.worksheet
        if self._worksheet is not None:
            return self._worksheet
        book = self._book()
        try:
            tab = _retry(lambda: book.worksheet(TAB))
        except gspread.exceptions.WorksheetNotFound:
            if not create:
                return None
            tab = self._create_tab(book)
        except Exception as exc:
            self._forget_handles()
            raise SNDStoreError(f"S&D storage worksheet could not be read: {_reason(exc)}.") from exc
        self._worksheet = tab
        if self._shared is not None:
            self._shared.worksheet = tab
        return tab

    @staticmethod
    def _create_tab(book):
        try:
            near_matches = [sheet.title for sheet in book.worksheets()
                            if sheet.title.strip().casefold() == TAB.casefold()
                            and sheet.title != TAB]
        except Exception:
            near_matches = []
        if near_matches:
            raise SNDStoreError(
                "A worksheet similar to SND_Config already exists. It was left unchanged; "
                "rename it deliberately or use a new empty spreadsheet."
            )
        try:
            return book.add_worksheet(title=TAB, rows=1000, cols=3)
        except Exception as exc:
            # Another instance may have created it between lookup and create.
            try:
                return book.worksheet(TAB)
            except Exception:
                raise SNDStoreError(
                    f"S&D storage worksheet could not be created: {_reason(exc)}."
                ) from exc

    def _read_values(self, tab, *, force: bool) -> list[list[str]]:
        shared = self._shared
        if shared is not None and not force:
            cached = shared.cached()
            if cached is not None:
                return cached
        generation = shared.current_generation() if shared is not None else 0
        try:
            values = _retry(lambda: tab.get_all_values(value_render_option=ValueRenderOption.formula))
        except Exception as exc:
            self._forget_handles()
            raise SNDStoreError(
                f"S&D storage could not be read from Google Sheets: {_reason(exc)}."
            ) from exc
        if shared is not None:
            shared.remember(values, generation)
        return values

    def initialize(self, *, force: bool = False) -> list[list[str]]:
        """Create the application tab and header without replacing any cell data.

        A missing tab or an entirely blank tab is safe to initialize. Any existing
        content with a different header is left untouched and blocks the store.
        Read-only stores treat a missing/blank tab as empty and never write.
        """
        create = self._may_initialize
        tab = self._tab(create=create)
        if tab is None:
            if self.read_only:
                return []
            raise SNDStoreError(NOT_INITIALIZED_MESSAGE)
        values = self._read_values(tab, force=force)
        if not values:
            if self.read_only:
                return []
            if not create:
                raise SNDStoreError(NOT_INITIALIZED_MESSAGE)
            values = self._write_header(tab)
        if values[0][:3] != HEADER:
            raise SNDStoreError(
                "SND_Config already contains data or an incompatible header. "
                "It was left unchanged; use a new empty spreadsheet or migrate it explicitly."
            )
        return values

    def _write_header(self, tab) -> list[list[str]]:
        def read():
            return _retry(lambda: tab.get_all_values(value_render_option=ValueRenderOption.formula))

        try:
            # Recheck immediately before the only initialization write. This
            # also sees formulas whose displayed result is an empty string.
            values = read()
            if not values:
                # Append is non-overwriting. If a person adds content after our
                # blank read, the header lands below it and validation fails
                # while preserving that content.
                _retry(lambda: tab.append_row(HEADER, value_input_option="RAW",
                                              insert_data_option="INSERT_ROWS"), write=True)
                values = read()
        except Exception as exc:
            # A concurrent initializer or a lost response may have completed the header.
            try:
                values = read()
            except Exception:
                values = []
            if not values:
                raise SNDStoreError(
                    "SND_Config is blank but its header could not be initialized. "
                    "No existing data was overwritten."
                ) from exc
        finally:
            self._invalidate()
        return values

    def _load(self, *, refresh: bool = False) -> dict[str, tuple[int, dict]]:
        if refresh:
            self._rows = None
        if self._rows is not None:
            if self.expected_cutover_id and self.expected_cutover_id not in self._cutover_receipts:
                raise SNDStoreError(NOT_VERIFIED_MESSAGE)
            return self._rows
        values = self.initialize(force=refresh)
        if values and values[0][:3] != HEADER:
            raise SNDStoreError("Header SND_Config tidak sesuai.")
        rows = {}
        accepted = set()
        receipts = {}
        seen_events = {}
        blank_seen = gap = False
        try:
            for index, row in enumerate(values[1:], start=2):
                if not row or not any(str(cell).strip() for cell in row):
                    blank_seen = True
                    continue
                gap = gap or blank_seen
                if row[:3] == HEADER and not any(str(cell).strip() for cell in row[3:]):
                    continue  # Harmless duplicate from concurrent blank-tab initializers.
                if not row[0]:
                    raise ValueError("non-empty row has no partner key")
                key = row[0]
                data = json.loads(row[1])
                if not isinstance(data, dict):
                    raise ValueError("payload must be an object")
                if key == CUTOVER_KEY:
                    receipt_id = data.get("migration_id")
                    hashes = (data.get("source_backup_sha256"), data.get("source_state_sha256"),
                              data.get("destination_state_sha256"))
                    valid_receipt = (
                        data.get(CUTOVER_MARKER) == 1
                        and isinstance(receipt_id, str) and bool(receipt_id)
                        and data.get("verified") is True
                        and data.get("ready_for_production_switch") is True
                        and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                                for value in hashes)
                        and data.get("source_state_sha256") == data.get("destination_state_sha256")
                        and projection_sha256({key: item[1] for key, item in rows.items()})
                        == data.get("destination_state_sha256")
                    )
                    if not valid_receipt:
                        continue  # Preserve but never trust an invalid/stale receipt row.
                    if receipt_id in receipts and receipts[receipt_id] != data:
                        raise ValueError("cutover receipt ID reused")
                    receipts[receipt_id] = data
                    continue
                if EVENT_MARKER not in data:
                    if key in rows:
                        raise ValueError("duplicate legacy key")
                    if not isinstance(data.get("requirements", []), list):
                        raise ValueError("invalid requirements")
                    for item in data.get("requirements", []):
                        if not isinstance(item, dict) or not {"id", "kind", "name", "quantity"} <= item.keys():
                            raise ValueError("invalid requirement")
                    rows[key] = (index, data)
                    continue
                event_id = data["id"]
                changes = data["changes"]
                if (data[EVENT_MARKER] != 1 or not isinstance(event_id, str) or not event_id
                        or not isinstance(changes, dict) or not changes.keys() <= PATCH_FIELDS):
                    raise ValueError("invalid event")
                if event_id in seen_events:
                    if seen_events[event_id] != (key, data):
                        raise ValueError("event ID reused")
                    continue  # An ambiguous transport retry may append the same event twice.
                seen_events[event_id] = (key, data)
                current = copy.deepcopy(rows.get(key, (0, {}))[1])
                changes_config = bool(CONFIG_FIELDS & changes.keys())
                if changes_config and not isinstance(data.get("expected_revision"), str):
                    raise ValueError("missing expected revision")
                expected = data.get("expected_revision")
                if data.get("expected_absent") and key in rows:
                    continue
                if expected is not None and expected != _revision(current):
                    continue  # Retain the conflicting event for recovery, never overwrite the winner.
                for field, value in changes.items():
                    if value is None:
                        current.pop(field, None)
                    else:
                        current[field] = value
                if "requirements" in current:
                    validate_requirements(current["requirements"])
                rows[key] = (index, current)
                accepted.add(event_id)
        except (IndexError, KeyError, ValueError, TypeError, AttributeError) as exc:
            raise SNDStoreError("Isi SND_Config tidak valid; periksa baris JSON dan duplikat.") from exc
        if self.expected_cutover_id and self.expected_cutover_id not in receipts:
            self._rows = None
            self._cutover_receipts = receipts
            raise SNDStoreError(NOT_VERIFIED_MESSAGE)
        self._rows = rows
        self._accepted_events = accepted
        self._cutover_receipts = receipts
        self._journal_rows = copy.deepcopy(values)
        self._has_gap = gap
        return rows

    def _ensure_writable(self) -> None:
        """Call after _load so the journal shape is known."""
        if self.read_only:
            raise SNDStoreError("This S&D storage connection is read-only; nothing was saved.")
        if self._has_gap:
            raise SNDStoreError(
                "SND_Config contains a blank row between saved records, so a new save could be "
                "inserted out of order. Nothing was saved. Delete only the empty row (keep every "
                "record) and reload."
            )

    def _append(self, rows: list[list[str]], *, uncertain: str) -> None:
        tab = self._tab(create=self._may_initialize)
        if tab is None:
            raise SNDStoreError(NOT_INITIALIZED_MESSAGE)
        try:
            if len(rows) == 1:
                _retry(lambda: tab.append_row(rows[0], value_input_option="RAW",
                                              insert_data_option="INSERT_ROWS"), write=True)
            else:
                _retry(lambda: tab.append_rows(rows, value_input_option="RAW",
                                               insert_data_option="INSERT_ROWS"), write=True)
        except Exception as exc:
            self._rows = None
            self._forget_handles()
            if _definitely_rejected(exc):
                raise SNDStoreError(f"S&D save failed: {_reason(exc)}. Nothing was saved.") from exc
            raise SNDStoreError(uncertain) from exc
        finally:
            self._invalidate()

    def _data(self, name: str) -> dict:
        return copy.deepcopy(self._load().get(partner_key(name), (0, {}))[1])

    def _save(self, name: str, data: dict, *, expected_revision: str | None = None,
              expected_absent: bool = False) -> None:
        data = _normalized(data)
        key = partner_key(name)
        before = self._data(name)
        changes = {field: data.get(field) for field in PATCH_FIELDS
                   if before.get(field) != data.get(field)}
        if CONFIG_FIELDS & changes.keys():
            expected_revision = expected_revision or _revision(before)
        self._load(refresh=True)
        self._ensure_writable()
        if expected_absent and key in self._rows:
            raise SNDConflictError("S&D import stopped: partner already exists in the destination.")
        if expected_revision is not None and _revision(self._data(name)) != expected_revision:
            raise SNDConflictError(CONFLICT_MESSAGE)
        if not changes:
            return
        event = {EVENT_MARKER: 1, "id": str(uuid4()), "expected_revision": expected_revision,
                 "expected_absent": expected_absent,
                 "changes": changes}
        values = [key, json.dumps(event, ensure_ascii=False), _now()]
        if len(values[1]) > CELL_LIMIT:
            raise SNDStoreError("S&D configuration is too large for one Sheet cell. No changes were saved.")
        self._append([values], uncertain=SAVE_UNCONFIRMED)
        self._load(refresh=True)
        if event["id"] not in self._accepted_events:
            raise SNDConflictError(CONFLICT_MESSAGE)

    def _patch(self, name: str, changes: dict) -> None:
        """Append invoice/proof fields. They can never replace requirements or Function.

        A revision-free patch always applies during replay, so it is mirrored
        locally instead of re-reading the journal. Bulk invoice edits therefore
        cost one write each and no reads.
        """
        if not changes or not changes.keys() <= DIRECT_PATCH_FIELDS:
            raise ValueError("Only invoice and proof-check fields can be patched directly.")
        key = partner_key(name)
        self._load()
        self._ensure_writable()
        event = {EVENT_MARKER: 1, "id": str(uuid4()), "expected_revision": None,
                 "expected_absent": False, "changes": changes}
        values = [key, json.dumps(event, ensure_ascii=False), _now()]
        if len(values[1]) > CELL_LIMIT:
            raise SNDStoreError("S&D proof result is too large for one Sheet cell. Nothing was saved.")
        self._append([values], uncertain=SAVE_UNCONFIRMED)
        current = copy.deepcopy(self._rows.get(key, (0, {}))[1])
        for field, value in changes.items():
            if value is None:
                current.pop(field, None)
            else:
                current[field] = value
        self._rows[key] = (len(self._journal_rows) + 1, current)
        self._accepted_events.add(event["id"])
        self._journal_rows.append(copy.deepcopy(values))

    def all_requirements(self) -> list[dict]:
        return [requirement_record({"partner_key": key, "partner_name": data.get("partner_name", key), **row})
                for key, (_, data) in self._load().items() for row in data.get("requirements", [])]

    def export_snapshot(self, *, refresh: bool = False) -> dict:
        """Include the complete journal, including superseded and conflicting edits."""
        self._load(refresh=refresh)
        return {"format": "snd-sheet-journal-v1", "exported_at": _now(),
                "rows": copy.deepcopy(self._journal_rows)}

    def partner_snapshots(self, *, refresh: bool = False) -> dict[str, dict]:
        """Return the complete current projection for migration verification."""
        return {key: copy.deepcopy(data) for key, (_, data) in self._load(refresh=refresh).items()}

    def record_cutover(self, receipt: dict) -> dict:
        """Append a verified migration receipt and confirm it can be read back."""
        data = copy.deepcopy(receipt)
        data[CUTOVER_MARKER] = 1
        data["verified"] = True
        receipt_id = data.get("migration_id")
        if (not isinstance(receipt_id, str) or not receipt_id
                or not isinstance(data.get("source_backup_sha256"), str)
                or not isinstance(data.get("source_state_sha256"), str)):
            raise SNDStoreError("Cutover receipt is incomplete; production was not enabled.")
        self._load(refresh=True)
        self._ensure_writable()
        existing = self._cutover_receipts.get(receipt_id)
        if existing:
            if existing != data:
                raise SNDStoreError("Cutover receipt ID already exists with different content.")
            return copy.deepcopy(existing)
        values = [CUTOVER_KEY, json.dumps(data, ensure_ascii=False), _now()]
        if len(values[1]) > CELL_LIMIT:
            raise SNDStoreError("Cutover receipt is too large for Google Sheets.")
        self._append([values], uncertain="Cutover receipt could not be confirmed; production remains disabled.")
        self._load(refresh=True)
        if self._cutover_receipts.get(receipt_id) != data:
            raise SNDStoreError("Cutover receipt verification failed; production remains disabled.")
        return copy.deepcopy(data)

    def import_partner_snapshots(self, partners: dict[str, dict]) -> int:
        """Recover missing partners without replacing any destination data.

        Every row is validated before the first write and appended in as few
        requests as possible. Replay rejects any event whose partner already exists.
        """
        if not partners:
            return 0
        self._load(refresh=True)
        self._ensure_writable()
        if set(partners) & set(self._rows):
            raise SNDConflictError("S&D import stopped: partner already exists in the destination.")
        rows, event_ids = [], []
        for key, raw in partners.items():
            if not key or partner_key(raw.get("partner_name", key)) != key:
                raise SNDStoreError("Source partner identity is invalid; nothing was imported.")
            data = _normalized(raw)
            if data.get("requirements"):
                validate_requirements(data["requirements"])
            changes = {field: data[field] for field in PATCH_FIELDS if data.get(field) is not None}
            event = {EVENT_MARKER: 1, "id": str(uuid4()),
                     "expected_revision": _revision({}) if CONFIG_FIELDS & changes.keys() else None,
                     "expected_absent": True, "changes": changes}
            payload = json.dumps(event, ensure_ascii=False)
            if len(payload) > CELL_LIMIT:
                raise SNDStoreError("A source partner record exceeds the Google Sheets cell limit; nothing was imported.")
            rows.append([key, payload, _now()])
            event_ids.append(event["id"])
        batch, size = [], 0
        for row in rows:
            if batch and (len(batch) >= BATCH_ROWS or size + len(row[1]) > BATCH_CHARS):
                self._append(batch, uncertain=IMPORT_UNCONFIRMED)
                batch, size = [], 0
            batch.append(row)
            size += len(row[1])
        self._append(batch, uncertain=IMPORT_UNCONFIRMED)
        self._load(refresh=True)
        if not set(event_ids) <= self._accepted_events:
            raise SNDConflictError(
                "S&D import could not be verified because another writer changed the destination. "
                "Nothing was overwritten."
            )
        return len(rows)

    def import_partner_snapshot(self, name: str, data: dict) -> None:
        """Recover one missing partner without replacing any destination data."""
        self.import_partner_snapshots({partner_key(name): data})

    def list_requirements(self, partner_name: str) -> list[dict]:
        return sorted(
            [requirement_record(row) for row in self._data(partner_name).get("requirements", [])],
            key=lambda row: (row["kind"] != "supply", row.get("created_at", ""), row["id"]),
        )

    def requirement_counts(self) -> dict[str, int]:
        return {key: len(data.get("requirements", [])) for key, (_, data) in self._load().items()}

    def replace_requirements(
        self, partner_name: str, function: str, requirements: list[dict], *,
        function_source: str = "manual", expected_revision: str | None = None,
    ) -> list[dict]:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner harus ELDs, EwAs, atau SS.")
        if function_source != "manual":
            raise ValueError("Fungsi S&D harus dipilih secara manual.")
        validated = validate_requirements(requirements)
        data = self._data(partner_name)
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
        self._save(partner_name, data, expected_revision=expected_revision)
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
        # Always written explicitly, so a stale cached value can never turn an
        # intended change into a silent no-op.
        self._patch(partner_name, {"invoice_exception": reason})

    def save_check(self, partner_name: str, signature: str, result: dict) -> dict:
        """Replace the partner's previous check and return the stored result."""
        checked_at = _now()
        stored = clean_check_result({key: value for key, value in result.items()
                                     if key != "checked_at"})
        self._patch(partner_name, {"check": {
            "signature": signature,
            "result": stored,
            "checked_at": checked_at,
        }})
        return {**stored, "checked_at": checked_at}

    def get_check(self, partner_name: str, signature: str) -> dict | None:
        check = self._data(partner_name).get("check", {})
        if check.get("signature") != signature:
            return None
        result = clean_check_result(check["result"])
        if check.get("checked_at"):
            result["checked_at"] = check["checked_at"]
        return result
