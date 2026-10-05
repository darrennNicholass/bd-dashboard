"""Credential-free tests for manually configured S&D fulfillment."""
from pathlib import Path
import unittest
from unittest.mock import patch
import gspread

from src.google_drive_service import GoogleDriveService, parse_env_setting
from src.snd_cloud_store import GoogleSheetsSNDStore
from src.snd_matching import filename_score, match_evidence, normalize_filename, select_partner_folder
from src.snd_security import may_access_real_snd
from src.snd_store import SNDStore, get_store
from src.snd_tracker import check_partner_fulfillment


def req(name="Instagram Story", quantity=4, kind="supply", uid="r1"):
    return {"id": uid, "kind": kind, "name": name, "quantity": quantity}


def proof(name, kind="supply", uid=None):
    return {"id": uid or name, "name": name, "kind": kind, "mime_type": "image/png"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(".tmp_snd_tracker.sqlite3")
        self.path.unlink(missing_ok=True)
        self.store = SNDStore(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_add_supply_demand_and_reload(self):
        self.store.replace_requirements("Mandaya", "ELDs", [req(), req("Goodie Bag", 1, "demand", "r2")])
        saved = SNDStore(self.path).list_requirements("Mandaya")
        self.assertEqual({row["kind"] for row in saved}, {"supply", "demand"})
        self.assertEqual(SNDStore(self.path).get_partner_functions()["mandaya"], "ELDs")

    def test_edit_preserves_created_at(self):
        original = self.store.replace_requirements("Mandaya", "ELDs", [req()])[0]
        updated = self.store.replace_requirements("Mandaya", "ELDs", [req(quantity=5)])[0]
        self.assertEqual(updated["quantity"], 5)
        self.assertEqual(original["created_at"], updated["created_at"])

    def test_delete_requirement(self):
        self.store.replace_requirements("Mandaya", "ELDs", [req(), req("Broadcast", 1, "demand", "r2")])
        self.store.replace_requirements("Mandaya", "ELDs", [req()])
        self.assertEqual([row["id"] for row in self.store.list_requirements("Mandaya")], ["r1"])

    def test_invoice_exception_persists_and_clears(self):
        for reason in ("In-Kind", "No Membership"):
            self.store.set_invoice_exception("Mandaya", reason)
            self.assertEqual(SNDStore(self.path).get_invoice_exceptions()["mandaya"], reason)
        self.store.set_invoice_exception("Mandaya", None)
        self.assertNotIn("mandaya", SNDStore(self.path).get_invoice_exceptions())

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            self.store.replace_requirements("Mandaya", "ELDs", [req(quantity=0)])
        with self.assertRaises(ValueError):
            self.store.set_invoice_exception("Mandaya", "Other")

    def test_configured_local_path_is_used(self):
        with patch.dict("os.environ", {"SND_DB_PATH": str(self.path)}):
            self.assertEqual(SNDStore().path, self.path)


class CloudStoreTests(unittest.TestCase):
    def test_configured_sheet_selects_durable_backend(self):
        with patch.dict("os.environ", {"SND_CONFIG_SHEET_ID": "demo-sheet"}):
            self.assertIsInstance(get_store(), GoogleSheetsSNDStore)

    def test_sheet_round_trip_and_isolated_partner_rows(self):
        class FakeWorksheet:
            def __init__(self):
                self.values = []
            def get_all_values(self):
                return [list(row) for row in self.values]
            def update(self, range_name, values, value_input_option):
                row = int(range_name.split(":")[0][1:]) - 1
                while len(self.values) <= row:
                    self.values.append([])
                self.values[row] = list(values[0])
            def append_row(self, values, value_input_option):
                self.values.append(list(values))

        class FakeSpreadsheet:
            tab = None
            def worksheet(self, title):
                if self.tab is None:
                    raise gspread.exceptions.WorksheetNotFound(title)
                return self.tab
            def add_worksheet(self, title, rows, cols):
                self.tab = FakeWorksheet()
                return self.tab

        book = FakeSpreadsheet()
        store = GoogleSheetsSNDStore("config", spreadsheet=book)
        self.assertEqual(store.list_requirements("Mandaya"), [])
        store.replace_requirements("Mandaya", "ELDs", [req()])
        store.set_invoice_exception("Mandaya", "In-Kind")
        store.replace_requirements("Another", "SS", [req("Voucher", 1, "demand", "r2")])
        reopened = GoogleSheetsSNDStore("config", spreadsheet=book)
        self.assertEqual(reopened.list_requirements("Mandaya")[0]["name"], "Instagram Story")
        self.assertEqual(reopened.get_invoice_exceptions()["mandaya"], "In-Kind")
        self.assertEqual(reopened.get_partner_functions()["another"], "SS")
        self.assertEqual(len(book.tab.values), 3)
        with self.assertRaises(ValueError):
            reopened.replace_requirements("Mandaya", "ELDs", [])
        self.assertEqual(GoogleSheetsSNDStore("config", spreadsheet=book).requirement_counts()["mandaya"], 1)


class MatchingTests(unittest.TestCase):
    def test_filename_normalization(self):
        self.assertEqual(normalize_filename("Goodie_Bag.jpg"), "goodie bag")
        self.assertEqual(normalize_filename("Goodie-Bag.png"), "goodie bag")
        self.assertGreaterEqual(filename_score("Goodie Bag", "goodiebag.jpeg")[0], 0.98)

    def test_quantity_partial(self):
        files = [proof(f"Instagram Story {index}.png") for index in range(1, 4)]
        result = match_evidence([req()], files)
        self.assertEqual(result["requirements"][0]["fulfilled_quantity"], 3)
        self.assertEqual(result["requirements"][0]["status"], "PARTIAL")
        self.assertEqual(result["overall_percent"], 75.0)

    def test_completed_and_missing(self):
        completed = match_evidence([req("Goodie Bag", 1)], [proof("Goodie Bag.jpg")])
        self.assertEqual(completed["status"], "COMPLETED")
        missing = match_evidence([req("Goodie Bag", 1)], [])
        self.assertEqual(missing["status"], "MISSING")

    def test_supply_and_demand_never_cross(self):
        supply = match_evidence([req("Voucher", 1, "supply")], [proof("Voucher.png", "demand")])
        demand = match_evidence([req("Voucher", 1, "demand")], [proof("Voucher.png", "supply")])
        self.assertEqual(supply["requirements"][0]["fulfilled_quantity"], 0)
        self.assertEqual(demand["requirements"][0]["fulfilled_quantity"], 0)

    def test_generic_filename_unmatched(self):
        result = match_evidence([req("Goodie Bag", 1)], [proof("IMG_28382.jpg")])
        self.assertEqual(result["overall_percent"], 0.0)
        self.assertEqual(len(result["unmatched_evidence"]), 1)

    def test_ambiguous_requirement_needs_review(self):
        result = match_evidence([req("Instagram Story", 1)], [proof("Instagram.png")])
        self.assertEqual(result["requirements"][0]["status"], "NEEDS_REVIEW")

    def test_duplicate_proofs_not_counted_twice(self):
        result = match_evidence([req("Instagram Story", 2)], [
            proof("Instagram Story.png"), proof("Instagram Story copy.png"),
        ])
        self.assertEqual(result["requirements"][0]["fulfilled_quantity"], 1)
        self.assertEqual(result["status"], "NEEDS_REVIEW")

    def test_ambiguous_partner_folder_is_not_selected(self):
        folders = [{"name": "Mandaya Clinic", "id": "a"}, {"name": "Mandaya Hospital", "id": "b"}]
        selected, status = select_partner_folder("Mandaya", folders)
        self.assertIsNone(selected)
        self.assertEqual(status, "needs_review")

    def test_missing_drive_folder_graceful(self):
        class EmptyDrive(GoogleDriveService):
            def __init__(self):
                pass
            def list_children(self, folder_id):
                return []
        result = EmptyDrive().scan_partner_evidence("Mandaya", "ELDs", "root")
        self.assertEqual(result["partner_match"], "unmatched")
        self.assertEqual(result["evidence"], [])

    def test_check_reads_drive_only_when_invoked(self):
        class FakeDrive:
            calls = 0
            def scan_partner_evidence(self, partner_name, function, root):
                self.calls += 1
                return {"partner_match": "matched", "function": function,
                        "warnings": [], "evidence": [proof("Goodie Bag.jpg", "demand")]}
        path = Path(__file__).with_name(".tmp_snd_check.sqlite3")
        path.unlink(missing_ok=True)
        try:
            store = SNDStore(path)
            store.replace_requirements("Mandaya", "ELDs", [req("Goodie Bag", 1, "demand")])
            drive = FakeDrive()
            self.assertEqual(drive.calls, 0)
            result = check_partner_fulfillment("Mandaya", "ELDs", store=store, drive=drive, root_id="root")
            self.assertEqual(drive.calls, 1)
            self.assertEqual(result["status"], "COMPLETED")
        finally:
            path.unlink(missing_ok=True)

    def test_multiline_credential_parser_and_anonymous_policy(self):
        raw = 'GOOGLE_SERVICE_ACCOUNT={\n"private_key":"line1\\nline2"\n}\nSND_FOLDER_ID=root\n'
        self.assertTrue(parse_env_setting(raw, "GOOGLE_SERVICE_ACCOUNT").startswith("{"))
        self.assertEqual(parse_env_setting(raw, "SND_FOLDER_ID"), "root")
        self.assertTrue(may_access_real_snd("member"))
        self.assertFalse(may_access_real_snd("anonymous"))


if __name__ == "__main__":
    unittest.main()
