"""Recovery hardening: configuration parsing, failure diagnosis, and preflight.

Covers the October 13 incident follow-up: a disabled Google Sheets API is
reported as such instead of as a sharing mistake, pasted spreadsheet IDs and
URLs are parsed consistently, and the production gate can be re-verified
read-only. The real recovered SQLite file drives an end-to-end cutover
rehearsal against an in-memory destination.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import gspread

from src import snd_backup, snd_cloud_store
from src.snd_backup import (
    merge_partner_sources, preflight, read_sqlite_snapshot, snapshot_partners,
    verified_cutover_partners,
)
from src.snd_cloud_store import GoogleSheetsSNDStore, SHEETS_API_DISABLED, TAB, _reason
from src.snd_store import (
    SNDStore, SNDStoreError, clean_setting, get_store, parse_sheet_id, sheet_id_hint,
)
from tests.fake_sheets import FakeBook, FakeWorksheet

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
REAL_SOURCE = ROOT / ".local" / "snd_tracker.sqlite3"
# Recovered from the deployed container's legacy rescue panel on October 13.
LEGACY_SOURCE = ROOT / ".local" / "incident-backups" / "snd-legacy-659134155cd6.sqlite3"
# Synthetic 44-character ID with the same shape as a real one. The production
# spreadsheet ID is configuration, not test data, and is never committed.
SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-aBcD"


class TempFilesMixin:
    """Temporary paths under tests/, removed even if a test fails."""

    def temp_path(self, suffix=".sqlite3"):
        path = TESTS / f".tmp_recovery_{uuid4().hex}{suffix}"
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True) if path.is_dir()
                        else path.unlink(missing_ok=True))
        return path


def req(uid="r1", name="Broadcast", quantity=1, kind="supply"):
    return {"id": uid, "kind": kind, "name": name, "quantity": quantity}


def api_error(code, status="PERMISSION_DENIED", message="fake", reason=None):
    class Response:
        status_code = code
        text = message

        def json(self):
            error = {"code": code, "message": message, "status": status}
            if reason:
                error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                     "reason": reason, "domain": "googleapis.com"}]
            return {"error": error}

    return gspread.exceptions.APIError(Response())


def sheets_api_disabled_error():
    """Reproduce gspread's real behaviour: a bare PermissionError whose cause
    carries the only actionable Google payload."""
    cause = api_error(
        403,
        message=("Google Sheets API has not been used in project 000000000000 before "
                 "or it is disabled."),
        reason="SERVICE_DISABLED",
    )
    error = PermissionError()
    error.__cause__ = cause
    return error


def migrate(book, partners=None, migration_id="cut-1"):
    return verified_cutover_partners(
        partners or {}, GoogleSheetsSNDStore("dest", spreadsheet=book), apply=True,
        migration_id=migration_id, source_backup_sha256="0" * 64,
    )


def production(book, migration_id="cut-1"):
    return GoogleSheetsSNDStore("dest", spreadsheet=book, expected_cutover_id=migration_id)


class SheetIdParsingTests(unittest.TestCase):
    def test_ids_and_urls_with_query_fragment_and_account_prefix_are_accepted(self):
        for value in (
            SHEET,
            f"  {SHEET}  ",
            f'"{SHEET}"',
            f"<{SHEET}>",
            f"\ufeff{SHEET}\u200b",
            f"https://docs.google.com/spreadsheets/d/{SHEET}/edit",
            f"https://docs.google.com/spreadsheets/d/{SHEET}/edit?gid=0#gid=0",
            f"https://docs.google.com/spreadsheets/d/{SHEET}/edit?usp=sharing",
            f"https://docs.google.com/spreadsheets/u/0/d/{SHEET}/edit#gid=123",
            f"https://docs.google.com/spreadsheets/d/{SHEET}",
            f"\n https://docs.google.com/spreadsheets/d/{SHEET}/edit?gid=0#gid=0 \n",
            f"https://drive.google.com/open?id={SHEET}",
        ):
            with self.subTest(value=value[:48]):
                self.assertEqual(parse_sheet_id(value), SHEET)

    def test_malformed_values_are_rejected_with_an_actionable_hint(self):
        published = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vAbCdEf/pubhtml")
        cases = {
            None: "empty",
            "": "empty",
            "   ": "empty",
            '""': "empty",
            published: "published-to-web",
            "https://drive.google.com/drive/folders/1AbCdEf": "Drive folder",
            "https://docs.google.com/document/d/abc/edit": "no /spreadsheets/d/",
            "not a sheet id!": "character type",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertIsNone(parse_sheet_id(value))
                self.assertIn(expected, sheet_id_hint(value))

    def test_clean_setting_only_strips_wrappers_and_invisible_characters(self):
        self.assertEqual(clean_setting(f" '{SHEET}' "), SHEET)
        self.assertEqual(clean_setting("\u00a0abc\u00a0"), "abc")
        self.assertEqual(clean_setting("a b"), "a b")  # Never silently joined.
        self.assertEqual(clean_setting(None), "")


class ConfigurationGateTests(unittest.TestCase):
    def settings(self, values):
        return patch("src.google_drive_service.optional_setting", side_effect=values.get)

    def assert_blocked(self, values, message):
        with self.settings(values), patch("src.snd_store.SNDStore") as local:
            with self.assertRaisesRegex(SNDStoreError, message):
                get_store()
            local.assert_not_called()  # Never an ephemeral SQLite fallback.

    def test_pasted_url_with_quotes_and_fragment_reaches_the_right_sheet(self):
        value = f'  "https://docs.google.com/spreadsheets/d/{SHEET}/edit?gid=0#gid=0"  '
        with self.settings({"SND_CONFIG_SHEET_ID": value, "SND_CUTOVER_ID": f' "cut-1" '}):
            store = get_store()
        self.assertEqual(store.sheet_id, SHEET)
        self.assertEqual(store.expected_cutover_id, "cut-1")
        self.assertEqual(store.storage_identity, f"google_sheets:{SHEET}")

    def test_malformed_sheet_id_explains_what_was_wrong_without_leaking_it(self):
        secret_looking = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSECRETTOKEN/pubhtml"
        with self.settings({"SND_CONFIG_SHEET_ID": secret_looking, "SND_CUTOVER_ID": "cut-1"}):
            with self.assertRaises(SNDStoreError) as caught:
                get_store()
        message = str(caught.exception)
        self.assertIn("spreadsheet ID", message)
        self.assertIn("published-to-web", message)
        self.assertNotIn("2PACX-1vSECRETTOKEN", message)

    def test_missing_blank_and_invalid_secrets_all_fail_closed(self):
        self.assert_blocked({}, "not configured")
        self.assert_blocked({"SND_CONFIG_SHEET_ID": "   "}, "not configured")
        self.assert_blocked({"SND_CONFIG_SHEET_ID": "not a sheet id!", "SND_CUTOVER_ID": "c"},
                            "spreadsheet ID")
        self.assert_blocked({"SND_CONFIG_SHEET_ID": SHEET}, "SND_CUTOVER_ID")
        self.assert_blocked({"SND_CONFIG_SHEET_ID": SHEET, "SND_CUTOVER_ID": "   "}, "SND_CUTOVER_ID")
        self.assert_blocked({"SND_CONFIG_SHEET_ID": SHEET, "SND_CUTOVER_ID": "has space"},
                            "exact migration_id")
        self.assert_blocked({"SND_STORAGE_BACKEND": "postgres"}, "must be google_sheets or sqlite")
        self.assert_blocked({"SND_STORAGE_BACKEND": "sqlite", "SND_CONFIG_SHEET_ID": SHEET,
                             "SND_DB_PATH": str(ROOT / "x.sqlite3")}, "SND_CONFIG_SHEET_ID unset")
        self.assert_blocked({"SND_STORAGE_BACKEND": "sqlite", "SND_DB_PATH": "relative.sqlite3"},
                            "absolute SND_DB_PATH")

    def test_quoted_backend_name_still_selects_google_sheets(self):
        with self.settings({"SND_STORAGE_BACKEND": ' "GOOGLE_SHEETS" ',
                            "SND_CONFIG_SHEET_ID": SHEET, "SND_CUTOVER_ID": "cut-1"}):
            self.assertIsInstance(get_store(), GoogleSheetsSNDStore)


class FailureDiagnosisTests(unittest.TestCase):
    def test_disabled_sheets_api_is_not_reported_as_a_sharing_mistake(self):
        reason = _reason(sheets_api_disabled_error())
        self.assertEqual(reason, SHEETS_API_DISABLED)
        self.assertIn("not enabled", reason)
        self.assertIn("Sharing the spreadsheet is not enough", reason)
        self.assertNotIn("share the spreadsheet with it as Editor", reason)

    def test_other_statuses_keep_their_own_actionable_message(self):
        cases = [
            (PermissionError(), "Editor"),
            (api_error(403, reason="ACCESS_DENIED"), "Editor"),
            (gspread.exceptions.SpreadsheetNotFound("x"), "not found"),
            (api_error(404, status="NOT_FOUND"), "not found"),
            (api_error(429, status="RESOURCE_EXHAUSTED"), "rate limit"),
            (api_error(503, status="UNAVAILABLE"), "temporarily unavailable"),
            (TimeoutError("slow"), "could not be reached"),
        ]
        for error, expected in cases:
            with self.subTest(expected):
                self.assertIn(expected, _reason(error))

    def test_disabled_api_blocks_every_operation_without_touching_sqlite(self):
        sheet_id = f"disabled-{uuid4().hex}"
        self.addCleanup(snd_cloud_store._SHARED.pop, sheet_id, None)
        with patch.object(snd_cloud_store, "_open_spreadsheet",
                          side_effect=sheets_api_disabled_error()), \
                patch("src.snd_store.SNDStore") as local:
            store = GoogleSheetsSNDStore(sheet_id, expected_cutover_id="cut-1")
            for operation in (store.requirement_counts,
                              lambda: store.set_invoice_exception("Demo", "In-Kind"),
                              lambda: store.replace_requirements("Demo", "ELDs", [req()])):
                with self.assertRaises(SNDStoreError) as caught:
                    operation()
                self.assertIn("not enabled", str(caught.exception))
            local.assert_not_called()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.sheet_id = f"pre-{uuid4().hex}"
        self.addCleanup(snd_cloud_store._SHARED.pop, self.sheet_id, None)
        account = patch("src.google_drive_service.service_account_info",
                        return_value={"client_email": "snd@example.iam.gserviceaccount.com",
                                      "project_id": "snd-project"})
        account.start()
        self.addCleanup(account.stop)
        settings = patch("src.google_drive_service.optional_setting", side_effect={}.get)
        settings.start()
        self.addCleanup(settings.stop)

    def run_preflight(self, book=None, *, cutover_id=None, error=None):
        target = patch.object(snd_cloud_store, "_open_spreadsheet",
                              side_effect=error) if error else \
            patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book)
        with target:
            return preflight(self.sheet_id, cutover_id=cutover_id)

    def results(self, report):
        return {item["check"]: item["ok"] for item in report["checks"]}

    def test_disabled_api_names_the_blocking_action(self):
        report = self.run_preflight(error=sheets_api_disabled_error())
        self.assertFalse(report["ready_for_production"])
        self.assertFalse(self.results(report)["google_sheets_reachable"])
        self.assertIn("Enable the Google Sheets API", report["blocking_action"])
        self.assertIn("snd-project", report["blocking_action"])

    def test_uninitialized_spreadsheet_is_reported_and_never_written(self):
        book = FakeBook()
        report = self.run_preflight(book)
        self.assertEqual(book.writes(), 0)
        self.assertFalse(self.results(report)["worksheet_initialized"])
        self.assertFalse(report["ready_for_production"])

    def test_verified_destination_is_ready_and_preflight_writes_nothing(self):
        book = FakeBook()
        receipt = migrate(book, {"demo": {"partner_name": "Demo", "function": "ELDs",
                                          "function_source": "manual", "requirements": [req()]}})
        writes = book.writes()
        report = self.run_preflight(book, cutover_id=receipt["migration_id"])
        self.assertEqual(book.writes(), writes)  # Strictly read-only.
        self.assertTrue(report["ready_for_production"])
        self.assertEqual(self.results(report)["cutover_id_matches_destination"], True)
        self.assertEqual(report["destination"]["partners"], 1)
        self.assertEqual(report["destination"]["requirements"], 1)
        self.assertEqual(report["destination"]["verified_cutover_ids"], [receipt["migration_id"]])
        self.assertFalse(report["destination"]["blank_row_gap"])

    def test_wrong_cutover_id_is_rejected_exactly_like_production(self):
        book = FakeBook()
        migrate(book, {"demo": {"partner_name": "Demo", "function": "ELDs",
                                "function_source": "manual", "requirements": [req()]}})
        report = self.run_preflight(book, cutover_id="not-the-receipt")
        self.assertFalse(report["ready_for_production"])
        self.assertFalse(self.results(report)["cutover_id_matches_destination"])
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            with self.assertRaisesRegex(SNDStoreError, "verified migration cutover"):
                production(book, "not-the-receipt").requirement_counts()

    def test_missing_cutover_id_is_reported_before_production_is_enabled(self):
        book = FakeBook()
        migrate(book, {"demo": {"partner_name": "Demo", "function": "ELDs",
                                "function_source": "manual", "requirements": [req()]}})
        report = self.run_preflight(book)
        self.assertFalse(report["cutover_id_configured"])
        self.assertFalse(report["ready_for_production"])

    def test_blank_row_gap_is_surfaced_as_a_blocking_condition(self):
        book = FakeBook()
        migrate(book, {"demo": {"partner_name": "Demo", "function": "ELDs",
                                "function_source": "manual", "requirements": [req()]}})
        book.config.values.insert(1, ["", "", ""])
        report = self.run_preflight(book, cutover_id="cut-1")
        self.assertFalse(self.results(report)["no_blank_row_gap"])
        self.assertFalse(report["ready_for_production"])

    def test_cli_preflight_exits_nonzero_while_storage_is_not_ready(self):
        output = io.StringIO()
        url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit?gid=0#gid=0"
        argv = ["snd_backup", "preflight", "--sheet-id", url]
        with patch.object(sys, "argv", argv), redirect_stdout(output), \
                patch.object(snd_cloud_store, "_open_spreadsheet",
                             side_effect=sheets_api_disabled_error()):
            with self.assertRaises(SystemExit) as caught:
                snd_backup.main()
        self.assertEqual(caught.exception.code, 1)
        report = json.loads(output.getvalue())
        self.assertIn("Enable the Google Sheets API", report["blocking_action"])

    def test_cli_rejects_a_published_url_with_an_actionable_message(self):
        errors = io.StringIO()
        argv = ["snd_backup", "preflight", "--sheet-id",
                "https://docs.google.com/spreadsheets/d/e/2PACX-1vAbC/pubhtml"]
        with patch.object(sys, "argv", argv), patch.object(sys, "stderr", errors):
            with self.assertRaises(SystemExit):
                snd_backup.main()
        self.assertIn("published-to-web", errors.getvalue())


@unittest.skipUnless(REAL_SOURCE.is_file(), "recovered S&D database is not present")
class RecoveredSourceCutoverTests(unittest.TestCase):
    """End-to-end rehearsal with the real recovered file, in-memory destination."""

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["snd_backup", *args]), redirect_stdout(output):
            snd_backup.main()
        return json.loads(output.getvalue())

    def setUp(self):
        self.artifacts = TESTS / f".tmp_recovery_{uuid4().hex}"
        self.addCleanup(shutil.rmtree, self.artifacts, True)
        self.sheet_id = f"real-{uuid4().hex}"
        self.addCleanup(snd_cloud_store._SHARED.pop, self.sheet_id, None)
        self.before = REAL_SOURCE.read_bytes()
        self.addCleanup(
            lambda: self.assertEqual(REAL_SOURCE.read_bytes(), self.before,
                                     "the recovered source file must never be modified"))

    def expected(self):
        partners = snapshot_partners(read_sqlite_snapshot(REAL_SOURCE))
        return {
            "partners": len(partners),
            "requirements": sum(len(v.get("requirements", [])) for v in partners.values()),
            "invoice": sum(1 for v in partners.values() if v.get("invoice_exception")),
        }

    def test_dry_run_is_read_only_then_apply_imports_every_record_once(self):
        book = FakeBook()
        counts = self.expected()
        url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit?gid=0#gid=0"
        command = ["cutover", "--sqlite", str(REAL_SOURCE), "--sheet-id", url,
                   "--artifacts-dir", str(self.artifacts)]
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            dry = self.run_cli(*command)
            self.assertEqual(book.writes(), 0, "a dry run must not write anything")
            self.assertEqual(dry["source_partners"], counts["partners"])
            self.assertEqual(dry["source_requirements"], counts["requirements"])
            self.assertEqual(dry["missing_partners"], counts["partners"])
            self.assertFalse(dry["ready_for_production_switch"])
            self.assertIn("No destination changes", dry["dry_run"])
            snd_cloud_store._SHARED.pop(self.sheet_id, None)

            applied = self.run_cli(*command, "--apply")
            self.assertTrue(applied["ready_for_production_switch"])
            self.assertEqual(applied["imported_partners"], counts["partners"])
            migration_id = applied["migration_id"]

            view = production(book, migration_id)
            self.assertEqual(sum(view.requirement_counts().values()), counts["requirements"])
            self.assertEqual(len(view.partner_snapshots()), counts["partners"])
            self.assertEqual(len(view.get_invoice_exceptions()), counts["invoice"])
            self.assertEqual(len(view.all_requirements()), counts["requirements"])

            # Re-running the same source is idempotent: nothing is duplicated.
            rows = len(book.config.values)
            snd_cloud_store._SHARED.pop(self.sheet_id, None)
            again = self.run_cli(*command, "--apply")
            self.assertEqual(again["missing_partners"], 0)
            self.assertEqual(again["imported_partners"], 0)
            self.assertEqual(again["already_verified_partners"], counts["partners"])
            fresh = production(book, migration_id)
            self.assertEqual(len(fresh.partner_snapshots()), counts["partners"])
            self.assertEqual(sum(fresh.requirement_counts().values()), counts["requirements"])
            # Only the second receipt row was added, no partner rows.
            self.assertEqual(len(book.config.values), rows + 1)

        receipts = sorted(path.name for path in self.artifacts.iterdir()
                          if path.name.startswith("snd-cutover-receipt"))
        self.assertEqual(len(receipts), 2)

    def test_identical_duplicate_sources_merge_without_duplicating_records(self):
        """Every recovered artifact holds the same records; supplying two is safe."""
        copy_path = self.artifacts / "duplicate.sqlite3"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL_SOURCE, copy_path)
        book = FakeBook()
        counts = self.expected()
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            applied = self.run_cli(
                "cutover", "--sqlite", str(REAL_SOURCE), "--sqlite", str(copy_path),
                "--sheet-id", self.sheet_id, "--artifacts-dir", str(self.artifacts), "--apply")
        self.assertTrue(applied["ready_for_production_switch"])
        self.assertEqual(applied["imported_partners"], counts["partners"])
        self.assertEqual(applied["superseded_values"], 0)
        self.assertEqual(len(applied["source_backup_sha256s"]), 2)
        view = production(book, applied["migration_id"])
        self.assertEqual(len(view.partner_snapshots()), counts["partners"])
        self.assertEqual(sum(view.requirement_counts().values()), counts["requirements"])

    def test_production_is_locked_before_the_receipt_and_unlocked_after(self):
        book = FakeBook()
        partners = snapshot_partners(read_sqlite_snapshot(REAL_SOURCE))
        locked = production(book, "cut-unverified")
        with self.assertRaisesRegex(SNDStoreError, "verified migration"):
            locked.requirement_counts()
        receipt = migrate(book, partners, migration_id="cut-real")
        self.assertTrue(receipt["ready_for_production_switch"])
        with self.assertRaisesRegex(SNDStoreError, "verified migration cutover"):
            production(book, "cut-unverified").requirement_counts()
        unlocked = production(book, "cut-real")
        self.assertEqual(len(unlocked.partner_snapshots()), len(partners))

    def test_create_edit_and_delete_persist_in_the_destination(self):
        book = FakeBook()
        migrate(book, snapshot_partners(read_sqlite_snapshot(REAL_SOURCE)), migration_id="cut-real")
        store = production(book, "cut-real")
        baseline = sum(store.requirement_counts().values())

        store.replace_requirements("Smoke Test Partner", "SS",
                                   [req("s1", "Test Deliverable", 2), req("s2", "Second", 1)])
        self.assertEqual(sum(production(book, "cut-real").requirement_counts().values()), baseline + 2)

        store = production(book, "cut-real")
        store.replace_requirements("Smoke Test Partner", "SS",
                                   [req("s1", "Edited Deliverable", 5), req("s2", "Second", 1)])
        reloaded = production(book, "cut-real").list_requirements("Smoke Test Partner")
        edited = {row["id"]: row for row in reloaded}
        self.assertEqual(edited["s1"]["name"], "Edited Deliverable")
        self.assertEqual(edited["s1"]["quantity"], 5)

        store = production(book, "cut-real")
        store.replace_requirements("Smoke Test Partner", "SS", [req("s2", "Second", 1)])
        after_delete = production(book, "cut-real").list_requirements("Smoke Test Partner")
        self.assertEqual([row["id"] for row in after_delete], ["s2"])
        self.assertEqual(sum(production(book, "cut-real").requirement_counts().values()), baseline + 1)

        store = production(book, "cut-real")
        store.set_invoice_exception("Smoke Test Partner", "In-Kind")
        self.assertEqual(production(book, "cut-real").get_invoice_exceptions()
                         .get("smoke test partner"), "In-Kind")
        store = production(book, "cut-real")
        store.set_invoice_exception("Smoke Test Partner", None)
        self.assertNotIn("smoke test partner", production(book, "cut-real").get_invoice_exceptions())
        # FakeWorksheet.update/clear raise on call, so reaching here proves the
        # whole create/edit/delete cycle only ever appended rows.
        self.assertGreater(len(book.config.values), 1)


class RealisticWorksheet(FakeWorksheet):
    """Mirrors the live Sheets API: an empty grid reads back as [[]], not []."""

    def get_all_values(self, **kwargs):
        return super().get_all_values(**kwargs) or [[]]


class RealisticBook(FakeBook):
    """FakeBook whose worksheets report emptiness the way Google really does."""

    def __init__(self, *, blank_config_tab=False):
        super().__init__()
        self.tabs = {"Sheet1": RealisticWorksheet("Sheet1", [["keep me"]])}
        if blank_config_tab:
            self.tabs[TAB] = RealisticWorksheet(TAB)

    def add_worksheet(self, title, rows, cols):
        with self.lock:
            if title in self.tabs:
                raise RuntimeError("duplicate title")
            self.created += 1
            self.tabs[title] = RealisticWorksheet(title)
            return self.tabs[title]


class EmptyGridInitializationTests(unittest.TestCase):
    """October 13 defect: a brand-new or cleared worksheet reads back as [[]].
    Treating that as existing data aborted the production cutover."""

    def test_blank_grid_recognizes_every_shape_the_api_returns(self):
        for values in ([], [[]], [[], []], [["", "   "]], [["", ""], [""]]):
            with self.subTest(values=values):
                self.assertTrue(snd_cloud_store._blank_grid(values))
        for values in ([["x"]], [[""], ["x"]], [["0"]], [["", "y"]]):
            with self.subTest(values=values):
                self.assertFalse(snd_cloud_store._blank_grid(values))

    def partners(self):
        return {"demo": {"partner_name": "Demo", "function": "EwAs",
                         "function_source": "manual", "requirements": [req()]}}

    def test_cutover_initializes_a_newly_created_worksheet(self):
        book = RealisticBook()
        receipt = migrate(book, self.partners(), migration_id="cut-empty")
        self.assertTrue(receipt["ready_for_production_switch"])
        self.assertEqual(book.config.values[0], snd_cloud_store.HEADER)
        self.assertEqual(production(book, "cut-empty").requirement_counts(), {"demo": 1})
        self.assertEqual(book.tabs["Sheet1"].values, [["keep me"]])  # Untouched.

    def test_cutover_initializes_a_preexisting_blank_worksheet(self):
        book = RealisticBook(blank_config_tab=True)
        receipt = migrate(book, self.partners(), migration_id="cut-blank")
        self.assertTrue(receipt["ready_for_production_switch"])
        self.assertEqual(book.config.values[0], snd_cloud_store.HEADER)
        self.assertEqual(book.created, 0, "the existing worksheet must be reused")
        self.assertEqual(production(book, "cut-blank").requirement_counts(), {"demo": 1})

    def test_read_only_store_sees_a_blank_grid_as_uninitialized_without_writing(self):
        book = RealisticBook(blank_config_tab=True)
        store = GoogleSheetsSNDStore("ro", spreadsheet=book, read_only=True)
        self.assertEqual(store.export_snapshot(refresh=True)["rows"], [])
        self.assertFalse(store.diagnostics()["worksheet_initialized"])
        self.assertEqual(book.writes(), 0)

    def test_production_still_refuses_to_initialize_a_blank_worksheet(self):
        book = RealisticBook(blank_config_tab=True)
        with self.assertRaisesRegex(SNDStoreError, "has not passed the verified migration"):
            production(book, "cut-x").requirement_counts()
        self.assertEqual(book.writes(), 0)

    def test_worksheet_holding_real_content_is_still_refused(self):
        book = RealisticBook()
        book.tabs[TAB] = RealisticWorksheet(TAB, [["unexpected", "content", "here"]])
        with self.assertRaisesRegex(SNDStoreError, "already contains data or an incompatible header"):
            migrate(book, self.partners(), migration_id="cut-dirty")
        self.assertEqual(book.config.values, [["unexpected", "content", "here"]])


class ComplementaryMergeTests(TempFilesMixin, unittest.TestCase):
    """The October 12 recovery shape: the Cloud database holds requirements and
    Functions, the local database holds invoice exceptions for some of the same
    partners. Neither may erase the other."""

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["snd_backup", *args]), redirect_stdout(output):
            snd_backup.main()
        return json.loads(output.getvalue())

    def build_sources(self):
        """cloud: 1 shared + 1 unique partner with requirements (newer).
        local: the same shared partner with only an invoice exception (older)."""
        cloud, local = self.temp_path(), self.temp_path()
        cloud_store, local_store = SNDStore(cloud), SNDStore(local)
        cloud_store.replace_requirements("Shared Partner", "EwAs",
                                         [req("c1", "Logo Placement", 2), req("c2", "Instagram Story", 3)])
        cloud_store.replace_requirements("Cloud Only", "EwAs", [req("c3", "Boothing", 1)])
        local_store.set_invoice_exception("Shared Partner", "In-Kind")
        local_store.set_invoice_exception("Local Only", "No Membership")
        return cloud, local

    def test_requirements_and_invoice_exceptions_both_survive_the_merge(self):
        cloud, local = self.build_sources()
        sources = [snapshot_partners(read_sqlite_snapshot(path)) for path in (cloud, local)]
        merged, report = merge_partner_sources(sources)
        # Order must not change the outcome.
        self.assertEqual(merge_partner_sources(list(reversed(sources)))[0], merged)
        self.assertEqual(report["superseded_values"], 0, "nothing should be discarded")
        self.assertEqual(set(merged), {"shared partner", "cloud only", "local only"})

        shared = merged["shared partner"]
        self.assertEqual(shared["function"], "EwAs")
        self.assertEqual(len(shared["requirements"]), 2)
        self.assertEqual(shared["invoice_exception"], "In-Kind")
        self.assertEqual(merged["local only"]["invoice_exception"], "No Membership")
        self.assertNotIn("requirements", merged["local only"])

        book = FakeBook()
        receipt = migrate(book, merged, migration_id="cut-merged")
        self.assertTrue(receipt["ready_for_production_switch"])
        view = production(book, "cut-merged")
        # An invoice-only partner is a real row, so it reports zero requirements.
        self.assertEqual(view.requirement_counts(),
                         {"shared partner": 2, "cloud only": 1, "local only": 0})
        self.assertEqual(view.get_invoice_exceptions(),
                         {"shared partner": "In-Kind", "local only": "No Membership"})
        self.assertEqual(view.get_partner_functions(),
                         {"shared partner": "EwAs", "cloud only": "EwAs"})

    def test_dry_run_marks_its_migration_id_provisional_and_writes_nothing(self):
        cloud, local = self.build_sources()
        artifacts = self.temp_path("")
        book = FakeBook()
        sheet_id = f"prov-{uuid4().hex}"
        self.addCleanup(snd_cloud_store._SHARED.pop, sheet_id, None)
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            dry = self.run_cli("cutover", "--sqlite", str(cloud), "--sqlite", str(local),
                               "--sheet-id", sheet_id, "--artifacts-dir", str(artifacts))
        self.assertEqual(book.writes(), 0)
        self.assertTrue(dry["migration_id_is_provisional"])
        self.assertFalse(dry["ready_for_production_switch"])
        self.assertIn("Do NOT use this migration_id as SND_CUTOVER_ID", dry["dry_run"])
        # The provisional ID must not unlock production, even by accident.
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            with self.assertRaisesRegex(SNDStoreError, "verified migration"):
                production(book, dry["migration_id"]).requirement_counts()
        # Every source was checksummed and backed up before any destination access.
        manifests = sorted(path.name for path in artifacts.iterdir()
                           if path.name.endswith(".manifest.json"))
        self.assertEqual(len(manifests), 2)
        self.assertEqual(len(dry["source_backup_sha256s"]), 2)

    def test_equal_timestamps_with_different_requirements_stop_the_merge(self):
        stamp = "2026-10-12T09:00:00+00:00"
        left = {"demo": {"partner_name": "Demo", "function": "EwAs",
                         "requirements": [{**req("x1", "A"), "updated_at": stamp}]}}
        right = {"demo": {"partner_name": "Demo", "function": "EwAs",
                          "requirements": [{**req("x1", "B"), "updated_at": stamp}]}}
        with self.assertRaisesRegex(SNDStoreError, "review the source backups manually"):
            merge_partner_sources([left, right])


@unittest.skipUnless(LEGACY_SOURCE.is_file(), "recovered legacy Cloud database is not present")
class RecoveredLegacyCloudTests(unittest.TestCase):
    """Guards the real two-source recovery totals discovered on October 13."""

    def test_legacy_and_local_merge_is_additive_and_order_independent(self):
        legacy = snapshot_partners(read_sqlite_snapshot(LEGACY_SOURCE))
        local = snapshot_partners(read_sqlite_snapshot(REAL_SOURCE))
        merged, report = merge_partner_sources([legacy, local])
        reversed_merge, _ = merge_partner_sources([local, legacy])
        self.assertEqual(merged, reversed_merge)
        self.assertEqual(report["superseded_values"], 0)

        # Additive: every requirement ID from both sources survives exactly once.
        def ids(source):
            return [(key, row["id"]) for key, data in source.items()
                    for row in data.get("requirements", [])]

        merged_ids = ids(merged)
        self.assertEqual(len(merged_ids), len(set(merged_ids)), "no duplicated requirements")
        self.assertEqual(set(ids(legacy)) | set(ids(local)), set(merged_ids))
        self.assertEqual(len(merged), len(set(legacy) | set(local)))
        self.assertEqual(
            sum(1 for data in merged.values() if data.get("invoice_exception")),
            sum(1 for data in local.values() if data.get("invoice_exception")))

    def test_merged_payload_passes_validation_before_any_remote_write(self):
        legacy = snapshot_partners(read_sqlite_snapshot(LEGACY_SOURCE))
        local = snapshot_partners(read_sqlite_snapshot(REAL_SOURCE))
        merged, _ = merge_partner_sources([legacy, local])
        snd_backup._validate_partner_payloads(merged)  # Must not raise.
        book = FakeBook()
        receipt = migrate(book, merged, migration_id="cut-real-merged")
        self.assertTrue(receipt["ready_for_production_switch"])
        view = production(book, "cut-real-merged")
        self.assertEqual(len(view.partner_snapshots()), len(merged))
        self.assertEqual(sum(view.requirement_counts().values()),
                         sum(len(d.get("requirements", [])) for d in merged.values()))


class UnrelatedRegressionTests(unittest.TestCase):
    """The S&D changes must not alter the rest of the dashboard."""

    def test_sqlite_backend_still_works_for_local_development(self):
        path = TESTS / f".tmp_recovery_{uuid4().hex}.sqlite3"
        self.addCleanup(path.unlink, True)
        store = SNDStore(path)
        store.replace_requirements("Local Partner", "ELDs", [req("l1", "Poster", 2)])
        self.assertEqual(SNDStore(path).list_requirements("Local Partner")[0]["name"], "Poster")
        with patch("src.google_drive_service.optional_setting",
                   side_effect={"SND_STORAGE_BACKEND": "sqlite",
                                "SND_DB_PATH": str(path.resolve())}.get):
            self.assertIsInstance(get_store(), SNDStore)

    def test_dashboard_modules_still_import_and_expose_their_entry_points(self):
        from src import charts, metrics, periods, preparation, sheets
        for module, name in ((periods, "month_to_period"), (preparation, "normalize_partner_name"),
                             (metrics, "__doc__"), (charts, "__doc__"), (sheets, "__doc__")):
            self.assertTrue(hasattr(module, name), f"{module.__name__}.{name} is missing")

    def test_worksheet_name_and_header_contract_are_unchanged(self):
        self.assertEqual(TAB, "SND_Config")
        self.assertEqual(snd_cloud_store.HEADER, ["partner_key", "payload_json", "updated_at"])


if __name__ == "__main__":
    unittest.main()
