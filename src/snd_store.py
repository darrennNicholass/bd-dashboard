"""Persistent, member-only S&D configuration and invoice exceptions.

Cloud deployments require a dedicated Google Sheet. SQLite is explicit opt-in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3

from src.preparation import (
    INVOICE_EXCEPTION_REASONS,
    canonical_invoice_reason,
    invoice_exception_map,
    normalize_partner_name,
    normalize_partner_function,
)
from src.snd_requirements import (
    validate_requirements, requirement_record, clean_check_result, requirements_revision,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".local" / "snd_tracker.sqlite3"
FUNCTIONS = {"ELDs", "EwAs", "SS"}
# Bentuk kanonik yang disimpan. Ejaan lain ("inkind", "in kind", ...) tetap
# diterima saat menulis dan tetap terbaca saat membaca, lewat
# preparation.canonical_invoice_reason().
INVOICE_REASONS = set(INVOICE_EXCEPTION_REASONS)


class SNDStoreError(RuntimeError):
    """Safe configuration-store error for display in the dashboard."""


class SNDConflictError(SNDStoreError):
    """A stale edit must never replace a more recent saved configuration."""


CONFLICT_MESSAGE = "S&D requirements changed in another session. Reload saved requirements before saving."


def partner_key(name: str) -> str:
    key = normalize_partner_name(name)
    if not key:
        raise ValueError("Nama partner wajib diisi.")
    return key


class SNDStore:
    storage_label = "Local SQLite (requires a persistent disk and backups)"

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from src.google_drive_service import optional_setting

            path = optional_setting("SND_DB_PATH")
        self.path = Path(path or DEFAULT_DB)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        self.path = self.path.resolve()
        self.storage_identity = f"sqlite:{self.path}"

    @contextmanager
    def _connect(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=15)
        except (OSError, sqlite3.Error) as exc:
            raise SNDStoreError("S&D SQLite storage could not be opened. Check the configured disk and path.") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS requirements (
                id TEXT PRIMARY KEY,
                partner_key TEXT NOT NULL,
                partner_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('supply', 'demand')),
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS requirements_partner_idx
                ON requirements(partner_key);
            CREATE TABLE IF NOT EXISTS partner_functions (
                partner_key TEXT PRIMARY KEY,
                function TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual'
            );
            CREATE TABLE IF NOT EXISTS invoice_exceptions (
                partner_key TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fulfillment_checks (
                partner_key TEXT PRIMARY KEY,
                requirements_signature TEXT NOT NULL,
                result_json TEXT NOT NULL,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );
            """)
            self._migrate_legacy(connection)
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise SNDStoreError("S&D database operation failed; changes were rolled back.") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _migrate_legacy(connection):
        """Upgrade old SQLite data atomically; IDs and timestamps survive."""
        fields = {row["name"] for row in connection.execute("PRAGMA table_info(requirements)")}
        config_fields = {row["name"] for row in connection.execute("PRAGMA table_info(partner_functions)")}
        if not fields.intersection({"notes", "function"}) and "source" in config_fields:
            return
        connection.execute("BEGIN IMMEDIATE")
        if "source" not in config_fields:
            connection.execute("ALTER TABLE partner_functions ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
        if "function" in fields:
            legacy_functions: dict[str, set[str]] = {}
            for row in connection.execute("SELECT partner_key, function FROM requirements"):
                function = normalize_partner_function(row["function"])
                if function:
                    legacy_functions.setdefault(row["partner_key"], set()).add(function)
            for key, values in legacy_functions.items():
                if len(values) == 1:
                    connection.execute(
                        "INSERT OR IGNORE INTO partner_functions (partner_key,function,updated_at,source) "
                        "VALUES (?,?,?,'legacy')",
                        (key, next(iter(values)), datetime.now(timezone.utc).isoformat()),
                    )
        if fields.intersection({"notes", "function"}):
            connection.execute("""CREATE TABLE requirements_v2 (
                id TEXT PRIMARY KEY, partner_key TEXT NOT NULL, partner_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('supply', 'demand')),
                name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 1),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute(
                "INSERT INTO requirements_v2 SELECT id,partner_key,partner_name,kind,name,quantity,"
                "created_at,updated_at FROM requirements"
            )
            connection.execute("DROP TABLE requirements")
            connection.execute("ALTER TABLE requirements_v2 RENAME TO requirements")
            connection.execute("CREATE INDEX requirements_partner_idx ON requirements(partner_key)")

    def list_requirements(self, partner_name: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requirements WHERE partner_key=? "
                "ORDER BY kind DESC, created_at, id", (partner_key(partner_name),)
            ).fetchall()
        return [requirement_record(dict(row)) for row in rows]

    def requirement_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT partner_key, COUNT(*) AS count FROM requirements GROUP BY partner_key"
            ).fetchall()
        return {row["partner_key"]: row["count"] for row in rows}

    def all_requirements(self) -> list[dict]:
        """Include inactive and renamed partners; never apply dashboard filters."""
        with self._connect() as connection:
            return [requirement_record(dict(row)) for row in connection.execute(
                "SELECT * FROM requirements ORDER BY partner_key, kind DESC, created_at, id"
            )]

    def export_snapshot(self) -> dict:
        with self._connect() as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            tables = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                      for table in ("requirements", "partner_functions", "invoice_exceptions",
                                    "fulfillment_checks", "requirement_history")}
        return {"format": "snd-sqlite-snapshot-v1",
                "exported_at": datetime.now(timezone.utc).isoformat(), "tables": tables}

    def replace_requirements(
        self, partner_name: str, function: str, requirements: list[dict], *,
        function_source: str = "manual", expected_revision: str | None = None,
    ) -> list[dict]:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner harus ELDs, EwAs, atau SS.")
        if function_source != "manual":
            raise ValueError("Fungsi S&D harus dipilih secara manual.")
        key = partner_key(partner_name)
        validated = validate_requirements(requirements)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            previous = [requirement_record(dict(row)) for row in connection.execute(
                "SELECT * FROM requirements WHERE partner_key=?", (key,)
            )]
            assignment_row = connection.execute(
                "SELECT function,source FROM partner_functions WHERE partner_key=?", (key,)
            ).fetchone()
            assignment = dict(assignment_row) if assignment_row else None
            if expected_revision is not None and requirements_revision(previous, assignment) != expected_revision:
                raise SNDConflictError(CONFLICT_MESSAGE)
            # Archive before replacing in the SAME transaction, including removed rows.
            connection.execute(
                "INSERT INTO requirement_history (partner_key,payload_json,archived_at) VALUES (?,?,?)",
                (key, json.dumps({"requirements": previous, "assignment": assignment},
                                 ensure_ascii=False), now),
            )
            old = {
                row["id"]: row["created_at"] for row in connection.execute(
                    "SELECT id, created_at FROM requirements WHERE partner_key=?", (key,)
                )
            }
            connection.execute("DELETE FROM requirements WHERE partner_key=?", (key,))
            for item in validated:
                connection.execute(
                    "INSERT INTO requirements (id,partner_key,partner_name,kind,name,quantity,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (item["id"], key, partner_name, item["kind"],
                     item["name"], item["quantity"],
                     old.get(item["id"], now), now),
                )
            connection.execute(
                "INSERT INTO partner_functions (partner_key,function,updated_at,source) VALUES (?,?,?,?) "
                "ON CONFLICT(partner_key) DO UPDATE SET function=excluded.function, "
                "updated_at=excluded.updated_at, source=excluded.source", (key, function, now, function_source),
            )
            connection.execute("DELETE FROM fulfillment_checks WHERE partner_key=?", (key,))
        return self.list_requirements(partner_name)

    def get_partner_functions(self) -> dict[str, str]:
        return {key: value["function"] for key, value in self.get_partner_function_assignments().items()}

    def get_partner_function_assignments(self) -> dict[str, dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT partner_key, function, source FROM partner_functions")
            return {row["partner_key"]: {"function": function, "source": row["source"]}
                    for row in rows if (function := normalize_partner_function(row["function"]))}

    def set_partner_function(self, partner_name: str, function: str) -> None:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner tidak valid.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO partner_functions (partner_key,function,updated_at,source) VALUES (?,?,?,'manual') "
                "ON CONFLICT(partner_key) DO UPDATE SET function=excluded.function, "
                "updated_at=excluded.updated_at, source='manual'",
                (partner_key(partner_name), function, now),
            )
            connection.execute(
                "DELETE FROM fulfillment_checks WHERE partner_key=?",
                (partner_key(partner_name),),
            )

    def get_invoice_exceptions(self) -> dict[str, str]:
        """Pengecualian invoice dalam bentuk kanonik.

        Nilai lama yang ejaannya berbeda ("inkind", "in-kind", "IN KIND")
        tetap dibaca sebagai pengecualian yang sah; baris sumber tidak diubah.
        Nilai yang tidak dikenali tidak dikembalikan, supaya invoice tidak
        dianggap "tidak diperlukan" tanpa alasan yang jelas.
        """
        with self._connect() as connection:
            rows = connection.execute("SELECT partner_key, reason FROM invoice_exceptions")
            return invoice_exception_map(
                {row["partner_key"]: row["reason"] for row in rows}
            )

    def set_invoice_exception(self, partner_name: str, reason: str | None) -> None:
        key = partner_key(partner_name)
        stored = canonical_invoice_reason(reason)
        if reason is not None and stored is None:
            raise ValueError("Alasan pengecualian Invoice tidak valid.")
        with self._connect() as connection:
            if stored is None:
                connection.execute("DELETE FROM invoice_exceptions WHERE partner_key=?", (key,))
            else:
                connection.execute(
                    "INSERT INTO invoice_exceptions VALUES (?,?,?) "
                    "ON CONFLICT(partner_key) DO UPDATE SET reason=excluded.reason, "
                    "updated_at=excluded.updated_at",
                    (key, stored, datetime.now(timezone.utc).isoformat()),
                )

    def save_check(self, partner_name: str, signature: str, result: dict) -> dict:
        """Replace the partner's previous check and return the stored result."""
        checked_at = datetime.now(timezone.utc).isoformat()
        stored = clean_check_result({key: value for key, value in result.items()
                                     if key != "checked_at"})
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO fulfillment_checks VALUES (?,?,?,?) "
                "ON CONFLICT(partner_key) DO UPDATE SET "
                "requirements_signature=excluded.requirements_signature, "
                "result_json=excluded.result_json, checked_at=excluded.checked_at",
                (partner_key(partner_name), signature,
                 json.dumps(stored, ensure_ascii=False), checked_at),
            )
        return {**stored, "checked_at": checked_at}

    def get_check(self, partner_name: str, signature: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json, checked_at FROM fulfillment_checks WHERE partner_key=? "
                "AND requirements_signature=?", (partner_key(partner_name), signature),
            ).fetchone()
        if not row:
            return None
        return {
            **clean_check_result(json.loads(row["result_json"])),
            "checked_at": row["checked_at"],
        }


# Spreadsheet ID/URL parsing lives in src/sheet_ids.py so the S&D store and the
# Market Research database share one implementation. Re-exported here because
# callers and tests already import these names from this module.
from src.sheet_ids import clean_setting, parse_sheet_id, sheet_id_hint  # noqa: E402


def reject_source_sheet(sheet_id: str) -> None:
    """The writable S&D store must never be one of the read-only ESSM sources."""
    from src.google_drive_service import optional_setting

    for setting_name in ("ACTIVE_ESSM_URL", "NATIONAL_SOURCE_URL"):
        if parse_sheet_id(optional_setting(setting_name)) == sheet_id:
            raise SNDStoreError("SND_CONFIG_SHEET_ID must be a separate Sheet, not an ESSM or National source.")


def get_store():
    """Fail closed instead of silently creating an ephemeral production database."""
    from src.google_drive_service import optional_setting

    raw_sheet_id = optional_setting("SND_CONFIG_SHEET_ID")
    backend = clean_setting(optional_setting("SND_STORAGE_BACKEND")).lower() or "google_sheets"
    if backend == "google_sheets":
        if not clean_setting(raw_sheet_id):
            raise SNDStoreError(
                "S&D durable storage is not configured. Set SND_CONFIG_SHEET_ID to a dedicated "
                "Google Sheet shared with the service account as Editor. Saving is unavailable "
                "until storage is configured; existing local data has not been deleted."
            )
        sheet_id = parse_sheet_id(raw_sheet_id)
        if not sheet_id:
            raise SNDStoreError(
                "SND_CONFIG_SHEET_ID must be a spreadsheet ID or a Google Sheets URL: "
                f"{sheet_id_hint(raw_sheet_id)}. Paste either the bare ID or the full "
                "https://docs.google.com/spreadsheets/d/<ID>/edit address of the dedicated S&D sheet."
            )
        reject_source_sheet(sheet_id)
        cutover_id = clean_setting(optional_setting("SND_CUTOVER_ID"))
        if not cutover_id:
            raise SNDStoreError(
                "S&D storage is connected but the one-time migration has not been verified yet, so "
                "S&D is read/write disabled to protect existing records. Run the cutover command in "
                "docs/snd-data-loss-incident.md, then add the returned migration_id as SND_CUTOVER_ID."
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cutover_id):
            raise SNDStoreError(
                "SND_CUTOVER_ID is invalid. Use the exact migration_id value printed by "
                "`python -m src.snd_backup cutover ... --apply`, without quotes or extra characters."
            )
        from src.snd_cloud_store import GoogleSheetsSNDStore

        return GoogleSheetsSNDStore(sheet_id, expected_cutover_id=cutover_id)
    if backend == "sqlite":
        path = optional_setting("SND_DB_PATH")
        if clean_setting(raw_sheet_id) or not path or not Path(path).is_absolute():
            raise SNDStoreError(
                "SQLite requires SND_STORAGE_BACKEND=sqlite and an absolute SND_DB_PATH "
                "on persistent storage, with SND_CONFIG_SHEET_ID unset. Do not use SQLite on Streamlit Cloud."
            )
        return SNDStore(path)
    raise SNDStoreError("SND_STORAGE_BACKEND must be google_sheets or sqlite.")
