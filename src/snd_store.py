"""Persistent, member-only S&D configuration and invoice exceptions.

SQLite is the local default; a dedicated Google Sheet is the durable Cloud option.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from src.preparation import normalize_partner_name, normalize_partner_function
from src.snd_requirements import validate_requirements, requirement_record, clean_check_result

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".local" / "snd_tracker.sqlite3"
FUNCTIONS = {"ELDs", "EwAs", "SS"}
INVOICE_REASONS = {"In-Kind", "No Membership"}


class SNDStoreError(RuntimeError):
    """Safe configuration-store error for display in the dashboard."""


def partner_key(name: str) -> str:
    key = normalize_partner_name(name)
    if not key:
        raise ValueError("Nama partner wajib diisi.")
    return key


class SNDStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from src.google_drive_service import optional_setting

            path = optional_setting("SND_DB_PATH")
        self.path = Path(path or DEFAULT_DB)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
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
            """)
            self._migrate_legacy(connection)
            yield connection
            connection.commit()
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
            connection.execute("ALTER TABLE partner_functions ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
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
                        "VALUES (?,?,?,'manual')",
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

    def replace_requirements(
        self, partner_name: str, function: str, requirements: list[dict], *, function_source: str = "manual"
    ) -> list[dict]:
        function = normalize_partner_function(function)
        if function not in FUNCTIONS:
            raise ValueError("Fungsi partner harus ELDs, EwAs, atau SS.")
        if function_source not in {"manual", "essm"}:
            raise ValueError("Sumber fungsi partner tidak valid.")
        key = partner_key(partner_name)
        validated = validate_requirements(requirements)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT function,source FROM partner_functions WHERE partner_key=?", (key,)
            ).fetchone()
            if existing and existing["source"] == "manual" and function_source == "essm":
                function = normalize_partner_function(existing["function"]) or function
                function_source = "manual"
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
        with self._connect() as connection:
            rows = connection.execute("SELECT partner_key, reason FROM invoice_exceptions")
            return {row["partner_key"]: row["reason"] for row in rows}

    def set_invoice_exception(self, partner_name: str, reason: str | None) -> None:
        key = partner_key(partner_name)
        if reason is not None and reason not in INVOICE_REASONS:
            raise ValueError("Alasan pengecualian Invoice tidak valid.")
        with self._connect() as connection:
            if reason is None:
                connection.execute("DELETE FROM invoice_exceptions WHERE partner_key=?", (key,))
            else:
                connection.execute(
                    "INSERT INTO invoice_exceptions VALUES (?,?,?) "
                    "ON CONFLICT(partner_key) DO UPDATE SET reason=excluded.reason, "
                    "updated_at=excluded.updated_at",
                    (key, reason, datetime.now(timezone.utc).isoformat()),
                )

    def save_check(self, partner_name: str, signature: str, result: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO fulfillment_checks VALUES (?,?,?,?) "
                "ON CONFLICT(partner_key) DO UPDATE SET "
                "requirements_signature=excluded.requirements_signature, "
                "result_json=excluded.result_json, checked_at=excluded.checked_at",
                (partner_key(partner_name), signature,
                 json.dumps(clean_check_result(result), ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()),
            )

    def get_check(self, partner_name: str, signature: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM fulfillment_checks WHERE partner_key=? "
                "AND requirements_signature=?", (partner_key(partner_name), signature),
            ).fetchone()
        return clean_check_result(json.loads(row["result_json"])) if row else None


def get_store():
    """Use a dedicated writable Sheet when configured, otherwise local SQLite."""
    from src.google_drive_service import optional_setting

    sheet_id = optional_setting("SND_CONFIG_SHEET_ID")
    if sheet_id:
        from src.snd_cloud_store import GoogleSheetsSNDStore

        return GoogleSheetsSNDStore(sheet_id)
    return SNDStore()
