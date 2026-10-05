"""Regression coverage for partner ownership and Notes-free requirements."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import unittest
from uuid import uuid4

import pandas as pd

from src import preparation
from src.google_drive_service import GoogleDriveService, DriveServiceError, FOLDER_MIME, normalize_function_folder
from src.snd_cloud_store import GoogleSheetsSNDStore, HEADER
from src.snd_matching import match_evidence, select_partner_folder
from src.snd_requirements import validate_requirements
from src.snd_store import SNDStore
from src.snd_tracker import (
    active_partner_functions, can_check_fulfillment, check_partner_fulfillment,
    resolve_partner_function, save_partner_requirements,
)


def requirement(**changes):
    return {"id": "r1", "kind": "supply", "name": "Instagram Story", "quantity": 4, **changes}


class FunctionTests(unittest.TestCase):
    def test_only_recognized_labels_normalize(self):
        for label, expected in [
            ("ELD", "ELDs"), ("elds", "ELDs"), (" ELD's ", "ELDs"),
            ("EwA", "EwAs"), ("EWA_s", "EwAs"), ("EwA’s", "EwAs"),
            (" ss ", "SS"), ("S.S.", "SS"),
            ("Health Care", None), ("Membership", None), ("ELD12", None),
            ("ELDs / SS", None), (None, None), (pd.NA, None),
        ]:
            with self.subTest(label=label):
                self.assertEqual(preparation.normalize_partner_function(label), expected)

    def test_manual_override_precedes_essm_but_auto_save_does_not(self):
        self.assertEqual(resolve_partner_function("SS", {"function": "ELD", "source": "manual"}).value, "ELDs")
        self.assertEqual(resolve_partner_function("SS", {"function": "ELDs", "source": "essm"}).value, "SS")
        self.assertIsNone(resolve_partner_function(None, {"function": "ELDs", "source": "essm"}).value)
        self.assertEqual(resolve_partner_function("EWA", {"function": "unknown"}).value, "EwAs")

    def test_essm_provenance_is_unambiguous_and_not_notes_or_industry(self):
        partners = pd.DataFrame([
            {"partner_name": "Demo Partner", "function": None},
            {"partner_name": "Conflicting", "function": None},
            {"partner_name": "Direct", "function": "EWA"},
            {"partner_name": "Unknown", "function": None, "notes": "ELDs", "stakeholder": "SS"},
        ])
        mr = pd.DataFrame([
            {"partner_name": " demo_partner! ", "source_tab": "[ELDs] Market Research"},
            {"partner_name": "Conflicting", "source_tab": "[ELDs] Market Research"},
            {"partner_name": "Conflicting", "source_tab": "[SS] Market Research"},
            {"partner_name": "Direct", "source_tab": "[SS] Market Research"},
        ])
        resolved = preparation.attach_partner_functions(partners, mr).set_index("partner_name")["function"]
        self.assertEqual(resolved["Demo Partner"], "ELDs")
        self.assertEqual(resolved["Direct"], "EwAs")
        self.assertTrue(pd.isna(resolved["Conflicting"]))
        self.assertTrue(pd.isna(resolved["Unknown"]))
        self.assertTrue(pd.isna(partners.iloc[0]["function"]))

    def test_conflicting_direct_values_do_not_fall_back_to_one_mr_team(self):
        partners = pd.DataFrame([
            {"partner_name": "Demo", "function": "ELDs"},
            {"partner_name": "DEMO", "function": "SS"},
        ])
        mr = pd.DataFrame([{"partner_name": "Demo", "source_tab": "[ELDs] Market Research"}])
        self.assertTrue(preparation.attach_partner_functions(partners, mr)["function"].isna().all())

    def test_drive_names_normalize_and_ambiguity_is_rejected(self):
        folder = {"id": "a", "name": "  DEMO__Partner-Clinic "}
        self.assertEqual(select_partner_folder("Demo Partner Clinic", [folder])[1], "matched")
        duplicate = {"id": "b", "name": "demo partner clinic"}
        self.assertEqual(select_partner_folder("Demo Partner Clinic", [folder, duplicate])[1], "needs_review")
        exact = {"id": "c", "name": "Demo Partner Clinic"}
        self.assertEqual(select_partner_folder("Demo Partner Clinic", [folder, exact])[0]["id"], "c")

    def test_check_requires_saved_requirements_and_resolved_function(self):
        self.assertFalse(can_check_fulfillment([], "ELDs"))
        self.assertFalse(can_check_fulfillment([requirement()], None))
        self.assertTrue(can_check_fulfillment([requirement()], "ELD"))
        with self.assertRaises(ValueError):
            check_partner_fulfillment("Demo", None)


class ConfigurationStoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_snd_{uuid4().hex}.sqlite3")
        self.store = SNDStore(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_manual_selection_persists_at_partner_level(self):
        active = pd.DataFrame([{"partner_name": "Demo", "function": None}])
        saved = save_partner_requirements("Demo", active, [requirement()], store=self.store, manual_function="ELD")
        self.assertNotIn("function", saved[0])
        self.assertNotIn("notes", saved[0])
        reopened = SNDStore(self.path)
        assignment = reopened.get_partner_function_assignments()["demo"]
        self.assertEqual(assignment, {"function": "ELDs", "source": "manual"})
        active["function"] = "SS"
        self.assertEqual(active_partner_functions(active, reopened.get_partner_function_assignments())["demo"].value, "ELDs")
        save_partner_requirements("Demo", active, [requirement(quantity=2)], store=reopened)
        self.assertEqual(reopened.get_partner_functions()["demo"], "ELDs")

    def test_auto_function_saves_without_dropdown_and_tracks_essm_updates(self):
        active = pd.DataFrame([{"partner_name": "Demo", "function": "ELD's"}])
        save_partner_requirements("Demo", active, [requirement()], store=self.store)
        self.assertEqual(self.store.get_partner_function_assignments()["demo"]["source"], "essm")
        active["function"] = "EwA"
        save_partner_requirements("Demo", active, [requirement()], store=self.store)
        self.assertEqual(self.store.get_partner_functions()["demo"], "EwAs")
        with closing(sqlite3.connect(self.path)) as connection:
            fields = {row[1] for row in connection.execute("PRAGMA table_info(requirements)")}
        self.assertFalse({"notes", "function"} & fields)

    def test_unknown_partner_is_rejected(self):
        active = pd.DataFrame([{"partner_name": "Demo", "function": "ELDs"}])
        with self.assertRaisesRegex(ValueError, "Partner tidak ditemukan"):
            save_partner_requirements("Missing", active, [requirement()], store=self.store)

    def test_empty_side_allowed_but_empty_configuration_and_invalid_rows_are_not(self):
        for kind in ("supply", "demand"):
            saved = self.store.replace_requirements("Demo", "SS", [requirement(kind=kind)])
            self.assertEqual(saved[0]["kind"], kind)
        for invalid in ([], [requirement(name="")], [requirement(name=pd.NA)],
                        [requirement(quantity=0)], [requirement(quantity=-1)],
                        [requirement(quantity=1.5)], [requirement(quantity=True)]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.replace_requirements("Demo", "SS", invalid)
        self.assertEqual(self.store.list_requirements("Demo")[0]["kind"], "demand")
        self.assertEqual(len(validate_requirements([requirement(), {"name": pd.NA, "quantity": pd.NA}])), 1)

    def test_legacy_sqlite_rows_migrate_automatically_without_losing_config(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                CREATE TABLE requirements (
                    id TEXT PRIMARY KEY, partner_key TEXT NOT NULL, partner_name TEXT NOT NULL,
                    function TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
                    quantity INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE partner_functions (
                    partner_key TEXT PRIMARY KEY, function TEXT NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO requirements VALUES ('legacy','demo','Demo','ELDs','demand','Goodie Bag',20,'SS','created','updated');
                INSERT INTO partner_functions VALUES ('demo','EwAs','updated');
            """)
        rows = self.store.list_requirements("Demo")
        self.assertEqual(rows[0]["id"], "legacy")
        self.assertEqual(rows[0]["quantity"], 20)
        self.assertNotIn("notes", rows[0])
        self.assertNotIn("function", rows[0])
        self.assertEqual(self.store.get_partner_functions()["demo"], "EwAs")
        saved = self.store.replace_requirements("Demo", "EwAs", rows)
        self.assertEqual(saved[0]["created_at"], "created")
        self.assertEqual(SNDStore(self.path).list_requirements("Demo")[0]["name"], "Goodie Bag")

    def test_old_notes_are_ignored_when_saving_or_matching(self):
        old = requirement(name="Goodie Bag", quantity=1, kind="demand", notes="SS", function="ELDs")
        saved = self.store.replace_requirements("Demo", "EwAs", [old])
        result = match_evidence([old], [{"id": "p1", "name": "Goodie_Bag.jpg", "kind": "demand"}])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertNotIn("notes", result["requirements"][0])
        self.assertNotIn("function", saved[0])
        self.assertEqual(self.store.get_partner_functions()["demo"], "EwAs")


class LegacyCloudTests(unittest.TestCase):
    def test_legacy_cloud_payload_loads_and_rewrites_without_notes(self):
        class Worksheet:
            def __init__(self, payload):
                self.values = [HEADER, ["demo", json.dumps(payload), "old"]]
            def get_all_values(self):
                return self.values
            def update(self, range_name, values, value_input_option):
                self.values[1] = values[0]
        class Book:
            def worksheet(self, name):
                return tab
        tab = Worksheet({"function": "ELD's", "requirements": [requirement(notes="SS", function="ELDs")]})
        store = GoogleSheetsSNDStore("test", spreadsheet=Book())
        self.assertEqual(store.get_partner_function_assignments()["demo"], {"function": "ELDs", "source": "manual"})
        saved = store.list_requirements("Demo")
        self.assertNotIn("notes", saved[0])
        self.assertNotIn("function", saved[0])
        store.replace_requirements("Demo", "ELDs", saved)
        payload = json.loads(tab.values[1][1])
        self.assertEqual(payload["function"], "ELDs")
        self.assertNotIn("function", payload["requirements"][0])
        self.assertNotIn("notes", payload["requirements"][0])
        self.assertEqual(GoogleSheetsSNDStore("test", spreadsheet=Book()).list_requirements("Demo")[0]["quantity"], 4)


class DriveHierarchyTests(unittest.TestCase):
    @staticmethod
    def folder(uid, name):
        return {"id": uid, "name": name, "mime_type": FOLDER_MIME}

    def test_numbered_function_folders_normalize_strictly(self):
        cases = {
            "1. ELDs": "ELDs", "1 ELDs": "ELDs", "ELDs": "ELDs",
            "2. EwAs": "EwAs", "2 EwAs": "EwAs", "EwAs": "EwAs",
            "3. SS": "SS", "3 SS": "SS", "SS": "SS",
            "1. ELDs Backup": None, "1ELDs": None, "4. Membership": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(normalize_function_folder(name), expected)

    def test_bi_international_traversal_and_partial_quantity(self):
        listing = {
            "root": [self.folder("elds", "1. ELDs"), self.folder("ewas", "2. EwAs"),
                     self.folder("ss", "3. SS")],
            "elds": [self.folder("bi", "BI International"), self.folder("other", "Other Partner")],
            "ewas": [self.folder("wrong", "BI International")],
            "bi": [self.folder("supply", "sUpPlY"), self.folder("demand", "DEMAND")],
            "supply": [{"id": "p2", "name": "broadcast", "mime_type": "image/png"}],
            "demand": [{"id": "p1", "name": "broadcast", "mime_type": "image/png"}],
        }
        class FakeDrive(GoogleDriveService):
            def __init__(self):
                self.visited = []
            def list_children(self, folder_id):
                self.visited.append(folder_id)
                return listing.get(folder_id, [])
        drive = FakeDrive()
        scan = drive.scan_partner_evidence("BI International", "ELDs", "root")
        self.assertEqual(scan["partner_match"], "matched")
        self.assertEqual(scan["function"], "ELDs")
        self.assertEqual(drive.visited, ["root", "elds", "bi", "supply", "demand"])
        self.assertFalse(scan["warnings"])
        result = match_evidence(
            [{"id": "r1", "kind": "demand", "name": "broadcast", "quantity": 3}],
            scan["evidence"],
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["requirements"][0]["fulfilled_quantity"], 1)
        self.assertEqual(result["requirements"][0]["quantity"], 3)
        self.assertEqual(len(result["unmatched_evidence"]), 1)  # Supply proof cannot fulfill Demand.
        supply = match_evidence(
            [{"id": "r2", "kind": "supply", "name": "broadcast", "quantity": 3}],
            [scan["evidence"][1]],
        )
        self.assertEqual(supply["status"], "MISSING")  # Demand proof cannot fulfill Supply.

    def test_duplicate_function_folder_is_not_selected(self):
        class FakeDrive(GoogleDriveService):
            def __init__(self):
                pass
            def list_children(self, folder_id):
                return [DriveHierarchyTests.folder("one", "1. ELDs"),
                        DriveHierarchyTests.folder("two", "ELDs")]
        with self.assertRaises(DriveServiceError):
            FakeDrive().function_folders("root")
