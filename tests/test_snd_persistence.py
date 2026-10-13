"""Persistence incident regressions: lost updates, restarts, history, and configuration."""
import copy
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import gspread

from src.snd_cloud_store import GoogleSheetsSNDStore, HEADER
from src.snd_backup import (
    read_sqlite_snapshot, migrate_missing, verified_cutover, write_json_exclusive,
)
from src.snd_requirements import requirements_revision
from src.snd_store import SNDStore, SNDStoreError, SNDConflictError, get_store


def req(uid="r1", name="Broadcast", quantity=1):
    return {"id": uid, "name": name, "quantity": quantity, "kind": "supply"}


def revision(store, name="Demo"):
    return requirements_revision(store.list_requirements(name),
                                 store.get_partner_function_assignments().get(name.lower()))


class Worksheet:
    def __init__(self, values=None):
        self.values = copy.deepcopy(values if values is not None else [HEADER])
        self.before_append = None
        self.fail_after_append = False
        self.lock = threading.Lock()

    def get_all_values(self, **kwargs):
        with self.lock:
            return copy.deepcopy(self.values)

    def append_row(self, values, value_input_option, insert_data_option):
        assert value_input_option == "RAW" and insert_data_option == "INSERT_ROWS"
        if self.before_append:
            self.before_append()
        with self.lock:
            self.values.append(copy.deepcopy(values))
        if self.fail_after_append:
            raise TimeoutError("response lost after server committed")

    def append_rows(self, values, value_input_option, insert_data_option):
        assert value_input_option == "RAW" and insert_data_option == "INSERT_ROWS"
        with self.lock:
            self.values.extend(copy.deepcopy(values))
        if self.fail_after_append:
            raise TimeoutError("response lost after server committed")


class Book:
    def __init__(self, values=None):
        self.tab = Worksheet(values)

    def worksheet(self, name):
        return self.tab


class InitWorksheet:
    def __init__(self, title, values=None, *, fail_after_header=False):
        self.title = title
        self.values = copy.deepcopy(values or [])
        self.update_calls = 0
        self.append_calls = 0
        self.fail_after_header = fail_after_header
        self.lock = threading.Lock()

    def get_all_values(self, **kwargs):
        with self.lock:
            return copy.deepcopy(self.values)

    def update(self, range_name, values, value_input_option):
        with self.lock:
            self.update_calls += 1
            while len(self.values) < 1:
                self.values.append([])
            self.values[0] = copy.deepcopy(values[0])
        if self.fail_after_header:
            raise TimeoutError("response lost after header commit")

    def append_row(self, values, value_input_option, insert_data_option):
        self.append_rows([values], value_input_option, insert_data_option)

    def append_rows(self, values, value_input_option, insert_data_option):
        assert value_input_option == "RAW" and insert_data_option == "INSERT_ROWS"
        with self.lock:
            self.append_calls += 1
            header = not self.values and values[0] == HEADER
            self.values.extend(copy.deepcopy(values))
        if header and self.fail_after_header:
            raise TimeoutError("response lost after header commit")


class InitBook:
    def __init__(self, config_values=None, *, include_config=False, config_title="SND_Config",
                 fail_after_header=False):
        self.lock = threading.Lock()
        self.tabs = {"Sheet1": InitWorksheet("Sheet1", [["keep me"]])}
        if include_config:
            self.tabs[config_title] = InitWorksheet(
                config_title, config_values, fail_after_header=fail_after_header,
            )

    def worksheet(self, title):
        with self.lock:
            if title not in self.tabs:
                raise gspread.exceptions.WorksheetNotFound(title)
            return self.tabs[title]

    def worksheets(self):
        with self.lock:
            return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        with self.lock:
            if title in self.tabs:
                raise RuntimeError("duplicate title")
            tab = InitWorksheet(title)
            self.tabs[title] = tab
            return tab


class InitializationTests(unittest.TestCase):
    def test_missing_tab_is_initialized_on_read_and_other_tabs_are_untouched(self):
        book = InitBook()
        store = GoogleSheetsSNDStore("test", spreadsheet=book)
        self.assertEqual(store.requirement_counts(), {})
        self.assertEqual(book.tabs["SND_Config"].values, [HEADER])
        self.assertEqual(book.tabs["Sheet1"].values, [["keep me"]])

    def test_existing_blank_tab_is_initialized_idempotently(self):
        book = InitBook(include_config=True)
        store = GoogleSheetsSNDStore("test", spreadsheet=book)
        store.initialize()
        store.initialize()
        self.assertEqual(book.tabs["SND_Config"].values, [HEADER])
        self.assertEqual(book.tabs["SND_Config"].append_calls, 1)
        self.assertEqual(book.tabs["SND_Config"].update_calls, 0)  # Cells are never overwritten.

    def test_populated_valid_tab_is_never_rewritten(self):
        payload = {"partner_name": "Demo", "requirements": [req()]}
        values = [HEADER, ["demo", json.dumps(payload), "old"]]
        book = InitBook(values, include_config=True)
        self.assertEqual(GoogleSheetsSNDStore("test", spreadsheet=book).requirement_counts(), {"demo": 1})
        self.assertEqual(book.tabs["SND_Config"].values, values)
        self.assertEqual(book.tabs["SND_Config"].update_calls, 0)

    def test_nonempty_or_formula_tab_is_rejected_without_mutation(self):
        cases = [
            [["wrong", "header", "here"], ["demo", "{}", "old"]],
            [["=IF(TRUE,\"\",\"x\")"]],
            [HEADER, ["", "{}", "timestamp"]],
        ]
        for values in cases:
            with self.subTest(values=values):
                book = InitBook(values, include_config=True)
                before = copy.deepcopy(book.tabs["SND_Config"].values)
                with self.assertRaises(SNDStoreError):
                    GoogleSheetsSNDStore("test", spreadsheet=book).requirement_counts()
                self.assertEqual(book.tabs["SND_Config"].values, before)
                self.assertEqual(book.tabs["SND_Config"].update_calls, 0)

    def test_ambiguous_header_response_is_accepted_only_after_readback(self):
        book = InitBook(include_config=True, fail_after_header=True)
        store = GoogleSheetsSNDStore("test", spreadsheet=book)
        self.assertEqual(store.requirement_counts(), {})
        self.assertEqual(book.tabs["SND_Config"].values, [HEADER])
        self.assertEqual(book.tabs["SND_Config"].append_calls, 1)  # Lost response is not retried.

    def test_near_match_tab_is_preserved_and_blocks_duplicate_creation(self):
        book = InitBook([["existing"]], include_config=True, config_title="snd_config ")
        before = copy.deepcopy(book.tabs["snd_config "].values)
        with self.assertRaisesRegex(SNDStoreError, "similar"):
            GoogleSheetsSNDStore("test", spreadsheet=book).requirement_counts()
        self.assertNotIn("SND_Config", book.tabs)
        self.assertEqual(book.tabs["snd_config "].values, before)

    def test_concurrent_initializers_keep_one_header_and_both_records(self):
        book = InitBook()
        errors = []

        def save(name, uid):
            try:
                GoogleSheetsSNDStore("test", spreadsheet=book).replace_requirements(
                    name, "ELDs", [req(uid=uid)],
                )
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=save, args=("One", "r1")),
                   threading.Thread(target=save, args=("Two", "r2"))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(book.tabs["SND_Config"].values[0], HEADER)
        self.assertEqual(GoogleSheetsSNDStore("test", spreadsheet=book).requirement_counts(),
                         {"one": 1, "two": 1})


class CloudPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.book = Book()

    def store(self):
        return GoogleSheetsSNDStore("test-sheet", spreadsheet=self.book)

    def test_new_process_keeps_yesterday_and_today_and_other_partners(self):
        first = self.store()
        first.replace_requirements("Yesterday", "ELDs", [req()])
        second = self.store()
        second.replace_requirements("Today", "SS", [req("r2")])
        self.assertEqual(self.store().requirement_counts(), {"yesterday": 1, "today": 1})

    def test_stale_invoice_write_does_not_replace_new_requirements(self):
        self.store().replace_requirements("Demo", "ELDs", [req()])
        stale = self.store()
        stale.list_requirements("Demo")
        self.store().replace_requirements("Demo", "ELDs", [req(quantity=9)])
        stale.set_invoice_exception("Demo", "In-Kind")
        self.assertEqual(self.store().list_requirements("Demo")[0]["quantity"], 9)
        self.assertEqual(self.store().get_invoice_exceptions()["demo"], "In-Kind")

    def test_stale_configuration_is_rejected_before_append(self):
        self.store().replace_requirements("Demo", "ELDs", [req()])
        stale = self.store()
        old_revision = revision(stale)
        self.store().replace_requirements("Demo", "SS", [req(quantity=7)])
        count = len(self.book.tab.values)
        with self.assertRaises(SNDConflictError):
            stale.replace_requirements("Demo", "ELDs", [req(quantity=2)], expected_revision=old_revision)
        self.assertEqual(len(self.book.tab.values), count)
        self.assertEqual(self.store().list_requirements("Demo")[0]["quantity"], 7)

    def test_simultaneous_edits_keep_one_winner_and_archive_both_attempts(self):
        self.store().replace_requirements("Demo", "ELDs", [req()])
        baseline = revision(self.store())
        barrier = threading.Barrier(2)
        self.book.tab.before_append = lambda: barrier.wait(timeout=5)
        outcomes = []

        def save(quantity):
            try:
                self.store().replace_requirements("Demo", "ELDs", [req(quantity=quantity)],
                                                  expected_revision=baseline)
                outcomes.append("saved")
            except SNDConflictError:
                outcomes.append("conflict")
            except Exception as exc:
                outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=save, args=(quantity,)) for quantity in (2, 3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["saved", "conflict"])
        snapshot = self.store().export_snapshot()
        self.assertEqual(len(snapshot["rows"]), 4)
        self.assertIn(self.store().list_requirements("Demo")[0]["quantity"], (2, 3))

    def test_superseded_and_removed_rows_survive_in_export(self):
        store = self.store()
        store.replace_requirements("Demo", "ELDs", [req(), req("r2", "Goodie Bag")])
        initial_row = copy.deepcopy(self.book.tab.values[1])
        store.replace_requirements("Demo", "ELDs", [req(quantity=4)])
        self.assertEqual(self.book.tab.values[1], initial_row)
        self.assertIn("Goodie Bag", json.dumps(store.export_snapshot()))
        self.assertEqual(len(self.store().all_requirements()), 1)

    def test_ambiguous_network_failure_never_reports_success_or_loses_data(self):
        self.book.tab.fail_after_append = True
        with self.assertRaisesRegex(SNDStoreError, "could not be confirmed"):
            self.store().replace_requirements("Demo", "ELDs", [req()])
        self.assertEqual(len(self.store().list_requirements("Demo")), 1)

    def test_bad_payload_or_duplicate_legacy_key_fails_without_overwrite(self):
        for values in ([HEADER, ["demo", "[]", "old"]],
                       [HEADER, ["demo", "{}", "old"], ["demo", "{}", "old"]],
                       [["unexpected header"]]):
            self.book = Book(values)
            with self.assertRaises(SNDStoreError):
                self.store().replace_requirements("Demo", "ELDs", [req()])
            self.assertEqual(self.book.tab.values, values)


class SQLitePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_persistence_{uuid4().hex}.sqlite3")
        self.store = SNDStore(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_stale_edit_rejected_and_previous_version_retained(self):
        self.store.replace_requirements("Demo", "ELDs", [req(), req("r2", "Goodie Bag")])
        old_revision = revision(self.store)
        SNDStore(self.path).replace_requirements("Demo", "ELDs", [req(quantity=5)])
        with self.assertRaises(SNDConflictError):
            self.store.replace_requirements("Demo", "SS", [req()], expected_revision=old_revision)
        self.assertEqual(self.store.list_requirements("Demo")[0]["quantity"], 5)
        archive = self.store.export_snapshot()["tables"]["requirement_history"]
        self.assertIn("Goodie Bag", archive[-1]["payload_json"])

    def test_failed_insert_rolls_back_deletion_and_history(self):
        self.store.replace_requirements("Other", "SS", [req("taken")])
        self.store.replace_requirements("Demo", "ELDs", [req()])
        before = self.store.export_snapshot()["tables"]
        with self.assertRaises(SNDStoreError):
            self.store.replace_requirements("Demo", "ELDs", [req("taken")])
        self.assertEqual(self.store.export_snapshot()["tables"], before)

    def test_recovery_export_is_read_only_and_migration_preserves_timestamps(self):
        original = self.store.replace_requirements("Demo", "ELDs", [req()])
        self.store.set_invoice_exception("Demo", "In-Kind")
        before = self.path.read_bytes()
        snapshot = read_sqlite_snapshot(self.path)
        self.assertEqual(before, self.path.read_bytes())
        book = Book()
        cloud = GoogleSheetsSNDStore("test", spreadsheet=book)
        plan = migrate_missing(snapshot, cloud)
        self.assertEqual(plan["missing_partners"], 1)
        self.assertEqual(book.tab.values, [HEADER])
        applied = migrate_missing(snapshot, cloud, apply=True)
        self.assertEqual(applied["imported_partners"], 1)
        reopened = GoogleSheetsSNDStore("test", spreadsheet=book)
        self.assertEqual(reopened.list_requirements("Demo"), original)
        self.assertEqual(reopened.get_invoice_exceptions()["demo"], "In-Kind")
        self.assertEqual(reopened.get_partner_functions()["demo"], "ELDs")
        # A destination edit is a hard conflict and is never overwritten or skipped.
        reopened.replace_requirements("Demo", "ELDs", [req(quantity=8)])
        with self.assertRaisesRegex(SNDStoreError, "differs"):
            migrate_missing(snapshot, reopened, apply=True)
        self.assertEqual(reopened.list_requirements("Demo")[0]["quantity"], 8)

    def test_recovery_missing_source_does_not_create_a_database(self):
        with self.assertRaises(sqlite3.OperationalError):
            read_sqlite_snapshot(self.path)
        self.assertFalse(self.path.exists())

    def test_verified_cutover_is_readback_checked_and_gates_production(self):
        self.store.replace_requirements("Demo", "ELDs", [req()])
        self.store.set_invoice_exception("Metadata Only", "In-Kind")
        snapshot = read_sqlite_snapshot(self.path)
        book = Book()
        cloud = GoogleSheetsSNDStore("destination", spreadsheet=book)
        dry_run = verified_cutover(
            snapshot, cloud, apply=False, migration_id="migration-1",
            source_backup_sha256="a" * 64,
        )
        self.assertFalse(dry_run["ready_for_production_switch"])
        self.assertEqual(len(book.tab.values), 1)
        applied = verified_cutover(
            snapshot, cloud, apply=True, migration_id="migration-1",
            source_backup_sha256="a" * 64,
        )
        self.assertTrue(applied["ready_for_production_switch"])
        self.assertEqual(applied["source_partners"], 2)
        verified = GoogleSheetsSNDStore(
            "destination", spreadsheet=book, expected_cutover_id="migration-1",
        )
        self.assertEqual(verified.requirement_counts(), {"demo": 1, "metadata only": 0})
        wrong = GoogleSheetsSNDStore(
            "destination", spreadsheet=book, expected_cutover_id="wrong",
        )
        for _ in range(2):
            with self.assertRaisesRegex(SNDStoreError, "verified migration"):
                wrong.requirement_counts()

    def test_cutover_rerun_accepts_exact_rows_but_rejects_divergence(self):
        self.store.replace_requirements("Demo", "ELDs", [req()])
        snapshot = read_sqlite_snapshot(self.path)
        book = Book()
        cloud = GoogleSheetsSNDStore("destination", spreadsheet=book)
        verified_cutover(snapshot, cloud, apply=True, migration_id="first",
                         source_backup_sha256="a" * 64)
        rerun = verified_cutover(snapshot, cloud, apply=False, migration_id="second",
                                 source_backup_sha256="b" * 64)
        self.assertEqual(rerun["already_verified_partners"], 1)
        self.assertEqual(rerun["missing_partners"], 0)
        cloud.replace_requirements("Demo", "ELDs", [req(quantity=9)])
        with self.assertRaisesRegex(SNDStoreError, "divergent"):
            verified_cutover(snapshot, cloud, apply=False, migration_id="third",
                             source_backup_sha256="c" * 64)

    def test_backup_publication_is_exclusive_and_has_stable_hash(self):
        target = self.path.with_suffix(".json")
        try:
            digest = write_json_exclusive(target, {"value": 1})
            self.assertEqual(len(digest), 64)
            with self.assertRaisesRegex(SNDStoreError, "already exists"):
                write_json_exclusive(target, {"value": 2})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"value": 1})
        finally:
            target.unlink(missing_ok=True)


class StorageConfigurationTests(unittest.TestCase):
    def settings(self, values):
        return patch("src.google_drive_service.optional_setting", side_effect=values.get)

    def test_missing_durable_configuration_never_opens_sqlite(self):
        with self.settings({}), patch("src.snd_store.SNDStore") as local:
            with self.assertRaisesRegex(SNDStoreError, "durable storage"):
                get_store()
            local.assert_not_called()

    def test_google_backend_requires_verified_cutover_id(self):
        with self.settings({"SND_CONFIG_SHEET_ID": "destination"}), \
                self.assertRaisesRegex(SNDStoreError, "cutover"):
            get_store()

    def test_sqlite_requires_explicit_backend_and_absolute_path(self):
        for values in ({"SND_DB_PATH": "relative.sqlite3"},
                       {"SND_STORAGE_BACKEND": "sqlite"},
                       {"SND_STORAGE_BACKEND": "sqlite", "SND_DB_PATH": "relative.sqlite3"},
                       {"SND_STORAGE_BACKEND": "invalid"}):
            with self.settings(values), self.assertRaises(SNDStoreError):
                get_store()
        path = str(Path(__file__).resolve().with_name(".tmp_explicit.sqlite3"))
        with self.settings({"SND_STORAGE_BACKEND": "sqlite", "SND_DB_PATH": path}):
            self.assertEqual(get_store().path, Path(path))
        self.assertFalse(Path(path).exists())

    def test_cloud_read_failure_never_falls_back_to_local(self):
        with self.settings({"SND_CONFIG_SHEET_ID": "test", "SND_CUTOVER_ID": "verified"}), \
                patch("src.snd_cloud_store.service_account_info", side_effect=RuntimeError), \
                patch("src.snd_store.SNDStore") as local:
            with self.assertRaises(SNDStoreError):
                get_store().requirement_counts()
            local.assert_not_called()

    def test_readonly_source_cannot_be_used_as_writable_config(self):
        with self.settings({"SND_CONFIG_SHEET_ID": "source",
                            "ACTIVE_ESSM_URL": "https://docs.google.com/spreadsheets/d/source/edit"}):
            with self.assertRaisesRegex(SNDStoreError, "separate Sheet"):
                get_store()
