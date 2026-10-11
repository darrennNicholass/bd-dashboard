"""Exercise the actual S&D widgets in isolation from login and live Sheets."""
from pathlib import Path
import unittest
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from src.snd_matching import requirement_signature
from src.snd_store import SNDStore

HARNESS_PATH = Path(__file__).with_name("snd_ui_harness.py")


class SNDViewTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_snd_ui_{uuid4().hex}.sqlite3")

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def app(self, function=None, access="member", partners=None, specs=None, months=None,
            fresh_check=None):
        app = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
        app.session_state.test_db_path = str(self.path)
        app.session_state.test_function = function
        app.session_state.test_partner_names = partners or ["Demo Partner"]
        app.session_state.test_partner_specs = specs
        app.session_state.test_months = months or ("august", "september", "october")
        app.session_state.test_fresh_check = fresh_check
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
