"""Production hardening of the Google Sheets S&D store. Uses in-memory fakes only."""
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
from src.google_drive_service import DriveServiceError
from src.snd_backup import (
    legacy_sqlite_backup, merge_partner_sources, read_sqlite_snapshot, snapshot_partners,
    verified_cutover_partners,
)
from src.snd_cloud_store import CELL_LIMIT, GoogleSheetsSNDStore, HEADER, TAB
from src.snd_store import SNDStore, SNDStoreError, get_store, parse_sheet_id
from src.snd_tracker import check_partner_fulfillment, compact_check_result
from tests.fake_sheets import FakeBook, api_error

TESTS = Path(__file__).resolve().parent


def req(uid="r1", name="Broadcast", quantity=1, kind="supply"):
    return {"id": uid, "kind": kind, "name": name, "quantity": quantity}


def legacy_row(key, name, requirements):
    return [key, json.dumps({"partner_name": name, "function": "ELDs", "function_source": "manual",
                             "requirements": requirements}), "old"]


def migrate(book, partners=None, migration_id="cut-1"):
    """Run the real verified cutover against an in-memory destination."""
    return verified_cutover_partners(
        partners or {}, GoogleSheetsSNDStore("dest", spreadsheet=book), apply=True,
        migration_id=migration_id, source_backup_sha256="0" * 64,
    )


def production(book):
    return GoogleSheetsSNDStore("dest", spreadsheet=book, expected_cutover_id="cut-1")


class TempFiles:
    def temp_path(self, suffix=".sqlite3"):
        path = TESTS / f".tmp_hardening_{uuid4().hex}{suffix}"
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True) if path.is_dir()
                        else path.unlink(missing_ok=True))
        return path


class EmptySpreadsheetLifecycleTests(unittest.TestCase):
    def test_dry_run_on_empty_spreadsheet_is_strictly_read_only(self):
        book = FakeBook()
        store = GoogleSheetsSNDStore("dest", spreadsheet=book, read_only=True)
        partners = {"demo": {"partner_name": "Demo", "function": "ELDs", "function_source": "manual",
                             "requirements": [req()]}}
        result = verified_cutover_partners(partners, store, apply=False, migration_id="dry",
                                           source_backup_sha256="0" * 64)
        self.assertEqual(result["missing_partners"], 1)
        self.assertFalse(result["ready_for_production_switch"])
        self.assertEqual(store.export_snapshot(refresh=True)["rows"], [])
        self.assertNotIn(TAB, book.tabs)
        self.assertEqual(book.writes(), 0)
        with self.assertRaisesRegex(SNDStoreError, "read-only"):
            store.replace_requirements("Demo", "ELDs", [req()])
        self.assertEqual(book.writes(), 0)

    def test_migration_initializes_empty_spreadsheet_then_production_saves_loads_and_updates(self):
        book = FakeBook()
        receipt = migrate(book, {"legacy partner": {
            "partner_name": "Legacy Partner", "function": "SS", "function_source": "manual",
            "requirements": [{**req("old-1", "Old Goodie Bag"), "created_at": "2026-10-04T13:00:00+00:00",
                              "updated_at": "2026-10-04T13:00:00+00:00"}],
        }})
        self.assertTrue(receipt["ready_for_production_switch"])
        self.assertEqual(book.config.values[0], HEADER)
        self.assertEqual(book.tabs["Sheet1"].values, [["keep me"]])  # Other tabs untouched.

        production(book).replace_requirements("New Partner", "ELDs", [req("n1", "Broadcast", 2)])
        journal = list(book.config.values)
        refreshed = production(book)  # A new session/process after a page refresh.
        self.assertEqual(refreshed.requirement_counts(), {"legacy partner": 1, "new partner": 1})
        self.assertEqual(refreshed.list_requirements("Legacy Partner")[0]["created_at"],
                         "2026-10-04T13:00:00+00:00")

        refreshed.replace_requirements("New Partner", "ELDs", [req("n1", "Broadcast", 5)])
        after = production(book)
        self.assertEqual(after.list_requirements("New Partner")[0]["quantity"], 5)
        self.assertEqual(after.list_requirements("Legacy Partner")[0]["name"], "Old Goodie Bag")
        self.assertEqual(book.config.values[:len(journal)], journal)  # Append-only journal.

    def test_production_never_creates_or_initializes_storage(self):
        cases = {
            "missing tab": FakeBook(),
            "blank tab": FakeBook(with_config=True),
            "header without receipt": FakeBook([HEADER], with_config=True),
        }
        for label, book in cases.items():
            with self.subTest(label):
                before = {title: list(tab.values) for title, tab in book.tabs.items()}
                with self.assertRaisesRegex(SNDStoreError, "verified migration"):
                    production(book).requirement_counts()
                with self.assertRaisesRegex(SNDStoreError, "verified migration"):
                    production(book).replace_requirements("Demo", "ELDs", [req()])
                with self.assertRaisesRegex(SNDStoreError, "verified migration"):
                    production(book).set_invoice_exception("Demo", "In-Kind")
                self.assertEqual({title: tab.values for title, tab in book.tabs.items()}, before)
                self.assertEqual(book.writes(), 0)


class JournalIntegrityTests(unittest.TestCase):
    def test_duplicate_header_from_concurrent_initializers_is_tolerated(self):
        book = FakeBook([HEADER, HEADER, legacy_row("demo", "Demo", [req()])], with_config=True)
        self.assertEqual(GoogleSheetsSNDStore("t", spreadsheet=book).requirement_counts(), {"demo": 1})

    def test_blank_row_between_records_blocks_writes_but_keeps_reads(self):
        values = [HEADER, legacy_row("demo", "Demo", [req()]), ["", "", ""],
                  legacy_row("other", "Other", [req("r2")])]
        book = FakeBook(values, with_config=True)
        store = GoogleSheetsSNDStore("t", spreadsheet=book)
        self.assertEqual(store.requirement_counts(), {"demo": 1, "other": 1})
        for action in (lambda: store.replace_requirements("Demo", "ELDs", [req(quantity=3)]),
                       lambda: store.set_invoice_exception("Demo", "In-Kind")):
            with self.assertRaisesRegex(SNDStoreError, "blank row"):
                action()
        self.assertEqual(book.config.values, values)


class QuotaAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.book = FakeBook()
        migrate(self.book)
        self.sheet_id = f"shared-{uuid4().hex}"
        opener = patch.object(snd_cloud_store, "_open_spreadsheet", return_value=self.book)
        self.open_spreadsheet = opener.start()
        self.addCleanup(opener.stop)
        self.addCleanup(snd_cloud_store._SHARED.pop, self.sheet_id, None)

    def store(self):
        return GoogleSheetsSNDStore(self.sheet_id, expected_cutover_id="cut-1")

    def test_reruns_share_one_connection_and_cached_journal(self):
        reads, metadata = self.book.config.reads, self.book.metadata_reads
        for _ in range(5):  # Five Streamlit reruns, two store instances each.
            self.store().get_invoice_exceptions()
            other = self.store()
            other.export_snapshot()
            other.get_partner_function_assignments()
            other.all_requirements()
        self.assertEqual(self.open_spreadsheet.call_count, 1)
        self.assertEqual(self.book.metadata_reads - metadata, 1)
        self.assertEqual(self.book.config.reads - reads, 1)

    def test_a_save_is_immediately_visible_to_other_sessions(self):
        self.store().requirement_counts()
        self.store().replace_requirements("Demo", "ELDs", [req()])
        self.assertEqual(self.store().list_requirements("Demo")[0]["name"], "Broadcast")

    def test_cached_journal_expires(self):
        self.store().requirement_counts()
        reads = self.book.config.reads
        with patch.object(snd_cloud_store, "VALUE_CACHE_SECONDS", 0.0):
            self.store().requirement_counts()
        self.assertEqual(self.book.config.reads, reads + 1)

    def test_bulk_invoice_save_costs_one_write_each_and_no_reads(self):
        store = self.store()
        store.get_invoice_exceptions()
        reads, appends = self.book.config.reads, self.book.config.appends
        for index in range(20):
            store.set_invoice_exception(f"Partner {index}", "In-Kind")
        self.assertEqual(self.book.config.reads, reads)
        self.assertEqual(self.book.config.appends - appends, 20)
        self.assertEqual(len(self.store().get_invoice_exceptions()), 20)

    def test_stale_session_invoice_change_is_never_a_silent_noop(self):
        production(self.book).set_invoice_exception("Demo", "In-Kind")
        stale = production(self.book)
        stale.get_invoice_exceptions()
        production(self.book).set_invoice_exception("Demo", None)
        stale.set_invoice_exception("Demo", "In-Kind")  # Same as its stale view.
        self.assertEqual(production(self.book).get_invoice_exceptions(), {"demo": "In-Kind"})


class RetryAndErrorTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(snd_cloud_store.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.book = FakeBook()
        migrate(self.book)

    def test_rate_limited_read_is_retried(self):
        self.book.config.read_errors = [api_error(429)]
        self.assertEqual(production(self.book).requirement_counts(), {})
        self.assertEqual(self.sleep.call_count, 1)

    def test_persistent_rate_limit_has_clear_message(self):
        self.book.config.read_errors = [api_error(429)] * 3
        with self.assertRaisesRegex(SNDStoreError, "rate limit"):
            production(self.book).requirement_counts()

    def test_rate_limited_save_is_retried_once_without_duplicates(self):
        rows = len(self.book.config.values)
        self.book.config.append_errors = [(api_error(429), False)]
        production(self.book).replace_requirements("Demo", "ELDs", [req()])
        self.assertEqual(len(self.book.config.values), rows + 1)
        self.assertEqual(production(self.book).requirement_counts(), {"demo": 1})

    def test_lost_save_response_is_never_repeated_or_reported_as_success(self):
        rows, appends = len(self.book.config.values), self.book.config.appends
        self.book.config.append_errors = [(TimeoutError("lost"), True)]
        with self.assertRaisesRegex(SNDStoreError, "could not be confirmed"):
            production(self.book).replace_requirements("Demo", "ELDs", [req()])
        self.assertEqual(self.book.config.appends, appends + 1)  # Never blindly repeated.
        self.assertEqual(len(self.book.config.values), rows + 1)
        self.assertEqual(production(self.book).requirement_counts(), {"demo": 1})  # Reload shows truth.

    def test_rejected_save_says_nothing_was_saved(self):
        rows = len(self.book.config.values)
        self.book.config.append_errors = [(api_error(403), False)]
        with self.assertRaisesRegex(SNDStoreError, "Editor.*Nothing was saved"):
            production(self.book).set_invoice_exception("Demo", "In-Kind")
        self.assertEqual(len(self.book.config.values), rows)

    def test_open_errors_are_actionable_and_never_fall_back_to_sqlite(self):
        cases = [
            (PermissionError(), "Editor"),
            (gspread.exceptions.SpreadsheetNotFound("x"), "not found"),
            (DriveServiceError("Google Service Account belum dikonfigurasi."), "Service Account"),
        ]
        for error, message in cases:
            sheet_id = f"open-{uuid4().hex}"
            self.addCleanup(snd_cloud_store._SHARED.pop, sheet_id, None)
            with self.subTest(message), \
                    patch.object(snd_cloud_store, "_open_spreadsheet", side_effect=error), \
                    patch("src.snd_store.SNDStore") as local:
                with self.assertRaisesRegex(SNDStoreError, message):
                    GoogleSheetsSNDStore(sheet_id, expected_cutover_id="cut-1").requirement_counts()
                local.assert_not_called()


class FulfillmentCheckSizeTests(unittest.TestCase):
    def test_large_drive_folder_check_is_compacted_and_persisted(self):
        book = FakeBook()
        migrate(book)
        store = production(book)
        store.replace_requirements("Demo", "ELDs", [req("r1", "Instagram Story")])
        evidence = [{"id": f"drive-file-id-{index:04d}-abcdefghijklmnopqrstuvwxyz",
                     "name": f"IMG_2026100{index:04d}_event_documentation.jpg",
                     "mime_type": "image/jpeg", "modified_time": "2026-10-10T10:00:00.000Z",
                     "kind": "supply"} for index in range(300)]

        class Drive:
            def scan_partner_evidence(self, partner_name, function, root_id):
                return {"partner_match": "matched", "function": function,
                        "evidence": evidence, "warnings": []}

        result = check_partner_fulfillment("Demo", store=store, drive=Drive(), root_id="root")
        self.assertEqual(result["unmatched_total"], 300)
        self.assertEqual(len(result["unmatched_evidence"]), 100)
        self.assertEqual(set(result["unmatched_evidence"][0]), {"kind", "name"})
        self.assertLess(len(book.config.values[-1][1]), CELL_LIMIT)
        fresh = production(book)
        signature = json.loads(book.config.values[-1][1])["changes"]["check"]["signature"]
        self.assertEqual(fresh.get_check("Demo", signature)["unmatched_total"], 300)
        self.assertEqual(compact_check_result(result), compact_check_result(compact_check_result(result)))


class RecoveryMergeTests(TempFiles, unittest.TestCase):
    def test_large_legacy_proof_check_is_compacted_during_migration(self):
        path = self.temp_path()
        store = SNDStore(path)
        saved = store.replace_requirements("Demo", "ELDs", [req()])
        unmatched = [{"id": f"drive-file-{index:04d}-abcdefghijklmnopqrstuvwxyz0123456789",
                      "name": f"IMG_{index:04d}_documentation.jpg", "mime_type": "image/jpeg",
                      "modified_time": "2026-10-10T10:00:00.000Z", "kind": "supply"}
                     for index in range(300)]
        store.save_check("Demo", "sig", {"status": "MISSING", "requirements": saved,
                                         "unmatched_evidence": unmatched})
        partners = snapshot_partners(read_sqlite_snapshot(path))
        self.assertEqual(partners["demo"]["check"]["result"]["unmatched_total"], 300)
        book = FakeBook()
        self.assertTrue(migrate(book, partners)["ready_for_production_switch"])
        self.assertEqual(production(book).get_check("Demo", "sig")["unmatched_total"], 300)
    def sources(self):
        local, live = self.temp_path(), self.temp_path()
        old = SNDStore(local)
        old.replace_requirements("Shared Partner", "ELDs", [req("s1", "Old Deliverable")])
        old.set_invoice_exception("Shared Partner", "In-Kind")
        old.replace_requirements("Local Only", "SS", [req("l1", "Local Deliverable")])
        new = SNDStore(live)  # Created later, as on the Cloud container.
        new.replace_requirements("Shared Partner", "EwAs", [req("s1", "New Deliverable", 4)])
        new.replace_requirements("Live Only", "ELDs", [req("v1", "Live Deliverable")])
        return [snapshot_partners(read_sqlite_snapshot(path)) for path in (local, live)]

    def test_newest_saved_value_wins_and_unique_records_are_kept(self):
        local, live = self.sources()
        merged, report = merge_partner_sources([local, live])
        self.assertEqual(merge_partner_sources([live, local])[0], merged)  # Order independent.
        shared = merged["shared partner"]
        self.assertEqual((shared["function"], shared["requirements"][0]["name"]), ("EwAs", "New Deliverable"))
        self.assertEqual(shared["invoice_exception"], "In-Kind")
        self.assertEqual(set(merged), {"shared partner", "local only", "live only"})
        self.assertEqual(report["superseded_values"], 1)
        self.assertEqual(report["partners"]["shared partner"]["config"]["superseded_sources"], [1])

        book = FakeBook()
        receipt = migrate(book, merged)
        self.assertTrue(receipt["ready_for_production_switch"])
        view = production(book)
        self.assertEqual(view.list_requirements("Shared Partner")[0]["name"], "New Deliverable")
        self.assertEqual(view.get_invoice_exceptions(), {"shared partner": "In-Kind"})
        self.assertEqual(view.requirement_counts(), {"shared partner": 1, "local only": 1, "live only": 1})

    def test_different_values_without_newer_timestamp_stop_the_merge(self):
        stamp = "2026-10-05T00:00:00+00:00"
        left = {"demo": {"partner_name": "Demo", "requirements": [{**req(name="A"), "updated_at": stamp}]}}
        right = {"demo": {"partner_name": "Demo", "requirements": [{**req(name="B"), "updated_at": stamp}]}}
        with self.assertRaisesRegex(SNDStoreError, "manually"):
            merge_partner_sources([left, right])

    def test_proof_check_is_kept_only_for_the_chosen_configuration(self):
        older = {"demo": {"partner_name": "Demo", "function": "ELDs",
                          "requirements": [{**req(name="A"), "updated_at": "2026-10-04T00:00:00+00:00"}],
                          "check": {"signature": "a", "result": {}, "checked_at": "2026-10-09T00:00:00+00:00"}}}
        newer = {"demo": {"partner_name": "Demo", "function": "ELDs",
                          "requirements": [{**req(name="B"), "updated_at": "2026-10-08T00:00:00+00:00"}]}}
        merged, _ = merge_partner_sources([older, newer])
        self.assertEqual(merged["demo"]["requirements"][0]["name"], "B")
        self.assertNotIn("check", merged["demo"])


class CutoverCommandTests(TempFiles, unittest.TestCase):
    """Runs the exact CLI given to the user, against an in-memory destination."""

    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["snd_backup", *args]), redirect_stdout(output):
            snd_backup.main()
        return json.loads(output.getvalue())

    def test_dry_run_then_apply_with_two_recovered_databases(self):
        local, live, artifacts = self.temp_path(), self.temp_path(), self.temp_path("")
        SNDStore(local).replace_requirements("Old Partner", "ELDs", [req("o1")])
        SNDStore(live).replace_requirements("Today Partner", "SS", [req("t1")])
        local_bytes, live_bytes = local.read_bytes(), live.read_bytes()
        book = FakeBook()
        sheet_id = f"cli-{uuid4().hex}"
        self.addCleanup(snd_cloud_store._SHARED.pop, sheet_id, None)
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=0"
        common = ["cutover", "--sqlite", str(live), "--sqlite", str(local),
                  "--sheet-id", url, "--artifacts-dir", str(artifacts)]
        with patch.object(snd_cloud_store, "_open_spreadsheet", return_value=book):
            dry = self.run_cli(*common)
            self.assertEqual((dry["source_partners"], dry["missing_partners"]), (2, 2))
            self.assertFalse(dry["ready_for_production_switch"])
            self.assertEqual(book.writes(), 0)
            snd_cloud_store._SHARED.pop(sheet_id, None)
            applied = self.run_cli(*common, "--apply")
        self.assertTrue(applied["ready_for_production_switch"])
        self.assertEqual(len(applied["source_backup_sha256s"]), 2)
        self.assertEqual((local.read_bytes(), live.read_bytes()), (local_bytes, live_bytes))
        view = GoogleSheetsSNDStore("x", spreadsheet=book, expected_cutover_id=applied["migration_id"])
        self.assertEqual(view.requirement_counts(), {"old partner": 1, "today partner": 1})
        names = {path.name.split("-2")[0] for path in artifacts.iterdir()}
        self.assertTrue({"snd-source", "snd-merge-report", "snd-destination-before",
                         "snd-cutover-receipt"} <= names)


class LegacyRescueTests(TempFiles, unittest.TestCase):
    def test_reader_is_read_only_and_summarizes_the_file(self):
        path = self.temp_path()
        SNDStore(path).replace_requirements("Demo", "ELDs", [req(), req("r2", "Goodie Bag")])
        before, modified = path.read_bytes(), path.stat().st_mtime_ns
        legacy = legacy_sqlite_backup(path)
        self.assertEqual((legacy["requirements"], legacy["partners"]), (2, 1))
        self.assertEqual(legacy["raw"], before)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (before, modified))

    def test_missing_file_is_never_created(self):
        path = self.temp_path()
        self.assertIsNone(legacy_sqlite_backup(path))
        self.assertFalse(path.exists())

    def test_unreadable_file_still_offers_the_raw_bytes(self):
        path = self.temp_path()
        path.write_bytes(b"not a database")
        legacy = legacy_sqlite_backup(path)
        self.assertEqual(legacy["raw"], b"not a database")
        self.assertIn("raw file", legacy["error"])

    def test_explicit_sqlite_backend_is_not_treated_as_legacy(self):
        with patch("src.google_drive_service.optional_setting", return_value="sqlite"):
            self.assertIsNone(legacy_sqlite_backup())


class ConfigurationTests(unittest.TestCase):
    def settings(self, values):
        return patch("src.google_drive_service.optional_setting", side_effect=values.get)

    def test_full_sheet_url_or_id_is_accepted(self):
        for value in ("abc_123-XYZ", "https://docs.google.com/spreadsheets/d/abc_123-XYZ/edit#gid=0"):
            with self.subTest(value), self.settings({"SND_CONFIG_SHEET_ID": value, "SND_CUTOVER_ID": "cut"}):
                self.assertEqual(get_store().sheet_id, "abc_123-XYZ")
        self.assertIsNone(parse_sheet_id("not a sheet id!"))
        with self.settings({"SND_CONFIG_SHEET_ID": "not a sheet id!", "SND_CUTOVER_ID": "cut"}):
            with self.assertRaisesRegex(SNDStoreError, "spreadsheet ID"):
                get_store()

    def test_missing_cutover_id_explains_the_next_step(self):
        with self.settings({"SND_STORAGE_BACKEND": "google_sheets", "SND_CONFIG_SHEET_ID": "abc"}):
            with self.assertRaisesRegex(SNDStoreError, "SND_CUTOVER_ID"):
                get_store()


if __name__ == "__main__":
    unittest.main()
