"""Exercise the actual S&D widgets in isolation from login and live Sheets."""
import json
from pathlib import Path
import unittest
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from src.snd_backup import legacy_sqlite_backup, verified_cutover_partners
from src.snd_cloud_store import GoogleSheetsSNDStore
from src.snd_matching import requirement_signature
from src.snd_store import SNDStore
from tests.fake_sheets import BOOKS, FakeBook

HARNESS_PATH = Path(__file__).with_name("snd_ui_harness.py")


class SNDHarnessCase(unittest.TestCase):
    """Shared S&D harness plumbing; holds no tests of its own."""

    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_snd_ui_{uuid4().hex}.sqlite3")

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def app(self, function=None, access="member", partners=None, specs=None, months=None,
            fresh_check=None, backend=None, book_id=None, store_error=None, legacy_path=None):
        app = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
        app.session_state.test_db_path = str(self.path)
        app.session_state.test_function = function
        app.session_state.test_partner_names = partners or ["Demo Partner"]
        app.session_state.test_partner_specs = specs
        app.session_state.test_months = months or ("august", "september", "october")
        app.session_state.test_fresh_check = fresh_check
        app.session_state.test_backend = backend
        app.session_state.test_book_id = book_id
        app.session_state.test_cutover_id = "ui-cutover"
        app.session_state.test_store_error = store_error
        app.session_state.test_legacy_path = legacy_path
        app.session_state.access_mode = access
        app.run()
        self.assertFalse(app.exception)
        return app

    def save_partner(self, name, function, kind="supply", deliverable="Broadcast", status=None,
                     partner_match="matched", warnings=None):
        store = SNDStore(self.path)
        requirements = store.replace_requirements(name, function, [
            {"id": f"{name}-r1", "kind": kind, "name": deliverable, "quantity": 1},
        ])
        if status:
            percent = 100.0 if status == "COMPLETED" else 50.0 if status == "PARTIAL" else 0.0
            store.save_check(name, requirement_signature(requirements, function), {
                "status": status, "function": function, "partner_match": partner_match,
                "warnings": warnings or [], "requirements": [], "unmatched_evidence": [],
                "supply_percent": percent, "demand_percent": percent,
                "overall_percent": percent,
            })


class SNDViewTests(SNDHarnessCase):
    def test_partner_selector_renders_only_one_workspace(self):
        app = self.app("ELDs", partners=["Demo Partner", "Second Partner"])
        self.assertIsNone(app.selectbox(key="snd_function_demo partner").value)
        partner_headers = [item.value for item in app.markdown if "snd-partner-name" in item.value]
        self.assertTrue(any("Demo Partner" in header for header in partner_headers))
        self.assertFalse(any("Second Partner" in header for header in partner_headers))
        self.assertEqual(len(app.dataframe), 2)

    def test_month_status_search_and_partner_selector_work_together(self):
        specs = [
            {"name": "Alpha", "end": "2026-08"},
            {"name": "Beta", "end": "2026-10"},
            {"name": "BI International", "end": "2026-10"},
        ]
        self.save_partner("Alpha", "ELDs", status="COMPLETED")
        self.save_partner("Beta", "EwAs", status="MISSING")
        self.save_partner("BI International", "SS", status="PARTIAL")
        app = self.app(specs=specs)

        month = app.selectbox(key="snd_month_filter")
        self.assertIn("September", month.options)
        month.select("September").run()
        self.assertEqual(set(app.selectbox(key="snd_selected_partner").options),
                         {"Beta", "BI International"})

        app.selectbox(key="snd_status_filter").select("Missing Proof").run()
        app.text_input(key="snd_partner_search").set_value("bi").run()
        selector = app.selectbox(key="snd_selected_partner")
        self.assertEqual(selector.options, ["BI International"])
        self.assertEqual(selector.value, "BI International")

    def test_status_filter_uses_saved_fulfillment_result(self):
        self.save_partner("Demo Partner", "ELDs", status="COMPLETED")
        app = self.app(partners=["Demo Partner", "Second Partner"])
        app.selectbox(key="snd_status_filter").select("Fulfilled").run()
        self.assertEqual(app.selectbox(key="snd_selected_partner").options, ["Demo Partner"])
        self.assertEqual(app.selectbox(key="snd_function_demo partner").value, "ELDs")

    def test_fresh_check_replaces_stale_ui_result_during_same_session(self):
        self.save_partner(
            "Demo Partner", "ELDs", status="NEEDS_REVIEW",
            partner_match="needs_review", warnings=["Folder partner perlu diperiksa secara manual."],
        )
        fresh = {
            "status": "COMPLETED", "function": "ELDs", "partner_match": "matched",
            "warnings": [], "requirements": [], "unmatched_evidence": [],
            "supply_percent": 100.0, "demand_percent": 100.0,
            "overall_percent": 100.0,
        }
        app = self.app(fresh_check=fresh)
        self.assertTrue(any("Folder partner" in item.value for item in app.warning))
        self.assertTrue(any("Last checked:" in item.value for item in app.markdown))

        app.button(key="snd_check_demo partner").click().run()

        self.assertFalse(app.exception)
        self.assertFalse(any("Folder partner" in item.value for item in app.warning))
        self.assertFalse(any("could not be matched" in item.value for item in app.warning))
        self.assertEqual(app.session_state.test_check_calls, 1)
        latest_session = app.session_state["snd_latest_check_demo partner"]["result"]
        self.assertEqual(latest_session["partner_match"], "matched")
        self.assertEqual(latest_session["warnings"], [])
        requirements = SNDStore(self.path).list_requirements("Demo Partner")
        persisted = SNDStore(self.path).get_check(
            "Demo Partner", requirement_signature(requirements, "ELDs"),
        )
        self.assertEqual(persisted["partner_match"], "matched")
        self.assertEqual(persisted["warnings"], [])
        self.assertEqual(persisted["status"], "COMPLETED")

    def test_empty_filters_show_clear_empty_state(self):
        app = self.app()
        app.text_input(key="snd_partner_search").set_value("does not exist").run()
        self.assertFalse(any(widget.label == "Select Partner" for widget in app.selectbox))
        self.assertTrue(any("No partners match the current filters" in item.value for item in app.markdown))

    def test_essm_function_is_ignored_and_manual_selector_is_always_visible(self):
        app = self.app("ELD's")
        selector = app.selectbox(key="snd_function_demo partner")
        self.assertEqual(selector.label, "Function")
        self.assertIsNone(selector.value)
        self.assertTrue(app.button(key="snd_save_demo partner").disabled)
        self.assertTrue(app.button(key="snd_check_demo partner").disabled)
        self.assertTrue(any("Select the partner Function" in warning.value for warning in app.warning))
        self.assertEqual(len(app.dataframe), 2)
        for editor in app.dataframe:
            self.assertEqual(list(editor.value.columns), ["ID", "Deliverable", "Quantity"])
            self.assertEqual(list(editor.proto.column_order), ["Deliverable", "Quantity"])

    def test_manual_save_persists_function_and_enables_check(self):
        app = self.app()
        self.assertTrue(app.button(key="snd_save_demo partner").disabled)
        self.assertTrue(app.button(key="snd_check_demo partner").disabled)
        self.assertEqual(len(app.warning), 1)
        app.selectbox(key="snd_function_demo partner").select("EwAs").run()
        self.assertFalse(app.warning)
        self.assertFalse(app.button(key="snd_save_demo partner").disabled)
        self.assertTrue(app.button(key="snd_check_demo partner").disabled)
        app.session_state["snd_demand_demo partner_0"] = {
            "edited_rows": {}, "added_rows": [{"Deliverable": "Goodie Bag", "Quantity": 20}],
            "deleted_rows": [],
        }
        app.button(key="snd_save_demo partner").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.selectbox(key="snd_function_demo partner").value, "EwAs")
        self.assertTrue(any("S&amp;D Setup: <strong>Configured" in item.value for item in app.markdown))
        self.assertFalse(app.button(key="snd_check_demo partner").disabled)
        saved = SNDStore(self.path).list_requirements("Demo Partner")
        self.assertEqual([(row["kind"], row["name"], row["quantity"]) for row in saved], [("demand", "Goodie Bag", 20)])
        self.assertNotIn("notes", saved[0])
        app = self.app("SS")
        self.assertEqual(app.selectbox(key="snd_function_demo partner").value, "EwAs")

    def test_function_rerun_preserves_unsaved_editor_input(self):
        app = self.app()
        app.session_state["snd_supply_demo partner_0"] = {
            "edited_rows": {}, "added_rows": [{"Deliverable": "Long campaign deliverable", "Quantity": 3}],
            "deleted_rows": [],
        }
        app.session_state["snd_function_demo partner"] = "SS"
        app.run()
        self.assertFalse(app.exception)
        editor_state = app.session_state["snd_supply_demo partner_0"]
        self.assertEqual(editor_state["added_rows"][0]["Deliverable"], "Long campaign deliverable")
        self.assertEqual(editor_state["added_rows"][0]["Quantity"], 3)
        self.assertFalse(app.button(key="snd_save_demo partner").disabled)

    def test_switching_partners_loads_isolated_saved_data(self):
        self.save_partner("Demo Partner", "ELDs", "supply", "Demo Supply")
        self.save_partner("Second Partner", "SS", "demand", "Second Demand")
        app = self.app(partners=["Demo Partner", "Second Partner"])
        self.assertEqual(app.selectbox(key="snd_function_demo partner").value, "ELDs")
        self.assertEqual(app.dataframe[0].value.iloc[0]["Deliverable"], "Demo Supply")

        app.selectbox(key="snd_selected_partner").select("Second Partner").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="snd_function_second partner").value, "SS")
        self.assertTrue(app.dataframe[0].value.empty)
        self.assertEqual(app.dataframe[1].value.iloc[0]["Deliverable"], "Second Demand")

    def test_switching_partners_preserves_unsaved_draft_without_leaking(self):
        app = self.app(partners=["Demo Partner", "Second Partner"])
        app.session_state["snd_supply_demo partner_0"] = {
            "edited_rows": {}, "added_rows": [{"Deliverable": "Unsaved Demo", "Quantity": 2}],
            "deleted_rows": [],
        }
        app.session_state["snd_function_demo partner"] = "EwAs"
        app.selectbox(key="snd_selected_partner").select("Second Partner").run()
        self.assertIsNone(app.selectbox(key="snd_function_second partner").value)
        self.assertTrue(app.dataframe[0].value.empty)

        app.selectbox(key="snd_selected_partner").select("Demo Partner").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="snd_function_demo partner").value, "EwAs")
        self.assertEqual(app.dataframe[0].value.iloc[0]["Deliverable"], "Unsaved Demo")
        self.assertEqual(app.dataframe[0].value.iloc[0]["Quantity"], 2)

    def test_anonymous_does_not_read_configuration(self):
        app = self.app("ELDs", access="anonymous")
        self.assertFalse(self.path.exists())
        self.assertFalse(app.dataframe)
        self.assertFalse(app.button)
        self.assertTrue(any("members only" in info.value for info in app.info))

    def test_new_session_reads_saved_data_and_stale_editor_cannot_overwrite_it(self):
        self.save_partner("Demo Partner", "ELDs", deliverable="Original")
        first = self.app()
        self.save_partner("Demo Partner", "SS", deliverable="Updated in another session")
        first.run()
        self.assertFalse(first.exception)
        self.assertTrue(first.button(key="snd_save_demo partner").disabled)
        self.assertEqual(first.dataframe[0].value.iloc[0]["Deliverable"], "Original")
        first.button(key="snd_reload_demo partner").click().run()
        self.assertFalse(first.exception)
        self.assertFalse(first.button(key="snd_save_demo partner").disabled)
        self.assertEqual(first.dataframe[0].value.iloc[0]["Deliverable"], "Updated in another session")
        reopened = self.app()
        self.assertEqual(reopened.dataframe[0].value.iloc[0]["Deliverable"], "Updated in another session")

    def test_inactive_and_renamed_partner_records_remain_accessible(self):
        self.save_partner("Old Partner Name", "ELDs", deliverable="Retained requirement")
        app = self.app(specs=[{"name": "New Partner Name", "end": "2026-07"}], months=("october",))
        self.assertFalse(app.exception)
        self.assertEqual(app.table[0].value.iloc[0]["partner_name"], "Old Partner Name")
        self.assertEqual(app.table[0].value.iloc[0]["name"], "Retained requirement")
        self.assertFalse(app.dataframe)

    def test_operational_ui_has_no_legacy_tracker_or_backup_access(self):
        """The Legacy Tracker and Backup entry points are gone from the tracker page.

        The legacy file, its reader, and the recovery CLI stay untouched; only
        the operational buttons are withdrawn.
        """
        self.save_partner("Live Partner", "ELDs", deliverable="Entered on Cloud")
        before = self.path.read_bytes()
        app = self.app(partners=["Live Partner"], legacy_path=str(self.path))
        self.assertFalse(app.exception)

        labels = [item.label for item in app.expander]
        self.assertFalse([label for label in labels if "Legacy" in label])
        self.assertFalse([label for label in labels if "backup" in label.casefold()])
        self.assertEqual(labels, ["All saved requirements (1)"])
        self.assertFalse(app.get("download_button"))
        button_labels = [item.label for item in app.button]
        self.assertFalse([label for label in button_labels
                          if "legacy" in label.casefold() or "backup" in label.casefold()])
        self.assertEqual(self.path.read_bytes(), before)

        # The retained recovery reader still works outside the operational UI.
        self.assertEqual(legacy_sqlite_backup(self.path)["requirements"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_records_panel_survives_disabled_storage_without_backup_button(self):
        self.save_partner("Live Partner", "ELDs", deliverable="Entered on Cloud")
        before = self.path.read_bytes()
        app = self.app(store_error="S&D storage is connected but the one-time migration has not been verified yet",
                       legacy_path=str(self.path))
        self.assertTrue(any("migration has not been verified" in item.value for item in app.error))
        self.assertFalse(app.expander)
        self.assertFalse(app.get("download_button"))
        self.assertFalse(app.dataframe)  # No editor can save while storage is disabled.
        self.assertEqual(self.path.read_bytes(), before)

    def test_anonymous_never_sees_legacy_database(self):
        self.save_partner("Live Partner", "ELDs")
        app = self.app(access="anonymous", legacy_path=str(self.path))
        self.assertFalse(app.expander)
        self.assertFalse(app.get("download_button"))

    def test_google_sheets_save_survives_refresh_and_filters_never_write(self):
        book = FakeBook()
        verified_cutover_partners({}, GoogleSheetsSNDStore("ui", spreadsheet=book), apply=True,
                                  migration_id="ui-cutover", source_backup_sha256="0" * 64)
        book_id = uuid4().hex
        BOOKS[book_id] = book
        self.addCleanup(BOOKS.pop, book_id, None)
        specs = [{"name": "Demo Partner", "end": "2026-09"}, {"name": "Second Partner", "end": "2027-01"}]
        app = self.app(backend="google", book_id=book_id, specs=specs)
        app.selectbox(key="snd_function_demo partner").select("EwAs").run()
        app.session_state["snd_demand_demo partner_0"] = {
            "edited_rows": {}, "added_rows": [{"Deliverable": "Goodie Bag", "Quantity": 20}],
            "deleted_rows": [],
        }
        app.button(key="snd_save_demo partner").click().run()
        self.assertFalse(app.error)
        event = json.loads(book.config.values[-1][1])
        self.assertEqual(event["changes"]["requirements"][0]["name"], "Goodie Bag")

        refreshed = self.app(backend="google", book_id=book_id, specs=specs)  # New browser session.
        self.assertEqual(refreshed.selectbox(key="snd_function_demo partner").value, "EwAs")
        self.assertEqual(refreshed.dataframe[1].value.iloc[0]["Deliverable"], "Goodie Bag")
        journal = [list(row) for row in book.config.values]
        refreshed.selectbox(key="snd_month_filter").select("October").run()
        self.assertNotIn("Demo Partner", refreshed.selectbox(key="snd_selected_partner").options)
        self.assertEqual(refreshed.table[0].value.iloc[0]["name"], "Goodie Bag")  # Still listed.
        refreshed.selectbox(key="snd_status_filter").select("Configured").run()
        refreshed.selectbox(key="snd_month_filter").select("All").run()
        self.assertFalse(refreshed.exception)
        self.assertEqual(book.config.values, journal)  # Filtering never writes.
        self.assertEqual(GoogleSheetsSNDStore("ui", spreadsheet=book, expected_cutover_id="ui-cutover")
                         .list_requirements("Demo Partner")[0]["quantity"], 20)


FRESH_COMPLETED = {
    "status": "COMPLETED", "function": "ELDs", "partner_match": "matched",
    "warnings": [], "requirements": [], "unmatched_evidence": [],
    "supply_percent": 100.0, "demand_percent": 100.0, "overall_percent": 100.0,
}


class CheckFulfillmentSelectionTests(SNDHarnessCase):
    """Regression: 'Check Fulfillment' used to jump to the next partner.

    A fresh proof check rewrites the partner's workflow status (for example
    Configured -> Fulfilled). The Status filter then dropped that partner from
    the option list, and the out-of-range fallback reassigned
    ``snd_selected_partner`` to ``shown[0]`` -- the following partner -- so the
    requested result was never shown for the partner the member picked.
    """

    def two_configured_partners(self, **kwargs):
        self.save_partner("Alpha", "ELDs", deliverable="Alpha Broadcast")
        self.save_partner("Beta", "ELDs", deliverable="Beta Broadcast")
        return self.app(partners=["Alpha", "Beta"], fresh_check=FRESH_COMPLETED, **kwargs)

    @staticmethod
    def partner_headers(app):
        return [item.value for item in app.markdown if "snd-partner-name" in item.value]

    def test_check_keeps_partner_a_selected_under_active_status_filter(self):
        app = self.two_configured_partners()
        app.selectbox(key="snd_status_filter").select("Configured").run()
        self.assertEqual(app.selectbox(key="snd_selected_partner").value, "Alpha")

        app.button(key="snd_check_alpha").click().run()

        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        selector = app.selectbox(key="snd_selected_partner")
        self.assertEqual(selector.value, "Alpha")
        self.assertIn("Alpha", selector.options)
        self.assertEqual(app.session_state["snd_selected_partner"], "Alpha")
        self.assertEqual(app.session_state["snd_workspace_partner"], "Alpha")

    def test_partner_b_is_never_auto_selected_after_checking_partner_a(self):
        app = self.two_configured_partners()
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()

        self.assertNotEqual(app.session_state["snd_selected_partner"], "Beta")
        headers = self.partner_headers(app)
        self.assertTrue(any("Alpha" in header for header in headers))
        self.assertFalse(any("Beta" in header for header in headers))
        self.assertEqual(app.session_state.test_check_calls, 1)
        self.assertNotIn("snd_latest_check_beta", app.session_state)
        store = SNDStore(self.path)
        self.assertIsNone(store.get_check(
            "Beta", requirement_signature(store.list_requirements("Beta"), "ELDs"),
        ))

    def test_result_is_attributed_to_the_checked_partner(self):
        app = self.two_configured_partners()
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()

        self.assertEqual(app.session_state["snd_latest_check_alpha"]["result"]["status"], "COMPLETED")
        self.assertTrue(any("Fulfilled · 100%" in item.value for item in app.markdown))
        self.assertEqual([metric.value for metric in app.metric], ["100%", "100%", "100%"])
        store = SNDStore(self.path)
        stored = store.get_check(
            "Alpha", requirement_signature(store.list_requirements("Alpha"), "ELDs"),
        )
        self.assertEqual(stored["status"], "COMPLETED")
        self.assertEqual(stored["overall_percent"], 100.0)
        self.assertEqual(app.session_state["snd_latest_check_alpha"]["signature"],
                         requirement_signature(store.list_requirements("Alpha"), "ELDs"))

    def test_check_never_duplicates_or_rewrites_requirements(self):
        store = SNDStore(self.path)
        self.save_partner("Alpha", "ELDs", deliverable="Alpha Broadcast")
        self.save_partner("Beta", "ELDs", deliverable="Beta Broadcast")
        before = store.export_snapshot()["tables"]
        app = self.app(partners=["Alpha", "Beta"], fresh_check=FRESH_COMPLETED)
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()
        self.assertFalse(app.exception)

        after = store.export_snapshot()["tables"]
        for table in ("requirements", "partner_functions", "requirement_history",
                      "invoice_exceptions"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual([row["name"] for row in store.all_requirements()],
                         ["Alpha Broadcast", "Beta Broadcast"])
        self.assertEqual(len(store.list_requirements("Alpha")), 1)

    def test_filters_stay_authoritative_after_the_check_rerun(self):
        app = self.two_configured_partners()
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()
        self.assertEqual(app.session_state["snd_pinned_partner"], "Alpha")

        # Steering the Status filter releases the pin, so the new Fulfilled
        # status of Alpha is honoured again.
        app.selectbox(key="snd_status_filter").select("Fulfilled").run()
        self.assertNotIn("snd_pinned_partner", app.session_state)
        selector = app.selectbox(key="snd_selected_partner")
        self.assertEqual(selector.options, ["Alpha"])
        self.assertEqual(selector.value, "Alpha")

        app.selectbox(key="snd_status_filter").select("Configured").run()
        self.assertEqual(app.selectbox(key="snd_selected_partner").options, ["Beta"])

        app.selectbox(key="snd_status_filter").select("All").run()
        self.assertEqual(app.selectbox(key="snd_selected_partner").options, ["Alpha", "Beta"])

    def test_explicit_partner_change_still_switches_and_releases_the_pin(self):
        app = self.two_configured_partners()
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()

        app.selectbox(key="snd_selected_partner").select("Beta").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["snd_selected_partner"], "Beta")
        self.assertNotIn("snd_pinned_partner", app.session_state)
        self.assertTrue(any("Beta" in header for header in self.partner_headers(app)))

    def test_pin_does_not_survive_a_month_scope_change(self):
        self.save_partner("Alpha", "ELDs", deliverable="Alpha Broadcast")
        self.save_partner("Beta", "ELDs", deliverable="Beta Broadcast")
        app = self.app(specs=[{"name": "Alpha", "end": "2026-08"}, {"name": "Beta", "end": "2027-01"}],
                       fresh_check=FRESH_COMPLETED)
        app.button(key="snd_check_alpha").click().run()
        self.assertEqual(app.session_state["snd_pinned_partner"], "Alpha")

        app.selectbox(key="snd_month_filter").select("October").run()
        self.assertFalse(app.exception)
        self.assertNotIn("snd_pinned_partner", app.session_state)
        self.assertEqual(app.selectbox(key="snd_selected_partner").options, ["Beta"])

    def test_check_with_no_status_filter_keeps_the_same_partner(self):
        app = self.two_configured_partners()
        self.assertEqual(app.selectbox(key="snd_selected_partner").value, "Alpha")
        app.button(key="snd_check_alpha").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["snd_selected_partner"], "Alpha")
        self.assertEqual(app.selectbox(key="snd_selected_partner").options, ["Alpha", "Beta"])

    def test_google_sheets_check_writes_one_proof_event_and_keeps_requirements(self):
        book = FakeBook()
        verified_cutover_partners({}, GoogleSheetsSNDStore("ui", spreadsheet=book), apply=True,
                                  migration_id="ui-cutover", source_backup_sha256="0" * 64)
        book_id = uuid4().hex
        BOOKS[book_id] = book
        self.addCleanup(BOOKS.pop, book_id, None)
        store = GoogleSheetsSNDStore("ui", spreadsheet=book, expected_cutover_id="ui-cutover")
        store.replace_requirements("Alpha", "ELDs", [
            {"id": "a1", "kind": "supply", "name": "Alpha Broadcast", "quantity": 1}])
        store.replace_requirements("Beta", "ELDs", [
            {"id": "b1", "kind": "supply", "name": "Beta Broadcast", "quantity": 1}])
        before = len(book.config.values)

        app = self.app(backend="google", book_id=book_id, fresh_check=FRESH_COMPLETED,
                       partners=["Alpha", "Beta"])
        app.selectbox(key="snd_status_filter").select("Configured").run()
        app.button(key="snd_check_alpha").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["snd_selected_partner"], "Alpha")

        appended = [json.loads(row[1]) for row in book.config.values[before:]]
        self.assertEqual([sorted(event["changes"]) for event in appended], [["check"]])
        self.assertEqual([row[0] for row in book.config.values[before:]], ["alpha"])
        reopened = GoogleSheetsSNDStore("ui", spreadsheet=book, expected_cutover_id="ui-cutover")
        self.assertEqual([row["name"] for row in reopened.all_requirements()],
                         ["Alpha Broadcast", "Beta Broadcast"])
        self.assertEqual(reopened.get_check(
            "Alpha", requirement_signature(reopened.list_requirements("Alpha"), "ELDs"),
        )["status"], "COMPLETED")
