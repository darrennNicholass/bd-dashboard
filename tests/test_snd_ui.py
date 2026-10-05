"""Exercise the actual S&D widgets in isolation from login and live Sheets."""
from pathlib import Path
import unittest
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from src.snd_store import SNDStore

HARNESS_PATH = Path(__file__).with_name("snd_ui_harness.py")


class SNDViewTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_snd_ui_{uuid4().hex}.sqlite3")

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def app(self, function=None, access="member"):
        app = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
        app.session_state.test_db_path = str(self.path)
        app.session_state.test_function = function
        app.session_state.access_mode = access
        app.session_state.snd_selected_partner = "demo partner"
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_known_essm_function_has_no_selector_and_editor_has_two_visible_columns(self):
        app = self.app("ELD's")
        self.assertFalse(any(widget.label == "Function / category" for widget in app.selectbox))
        self.assertFalse(app.button(key="snd_save_demo partner").disabled)
        self.assertTrue(app.button(key="snd_check_demo partner").disabled)
        self.assertTrue(any("Auto-detected from ESSM" in caption.value for caption in app.caption))
        self.assertEqual(len(app.dataframe), 2)
        for editor in app.dataframe:
            self.assertEqual(list(editor.value.columns), ["ID", "Deliverable", "Quantity"])
            self.assertEqual(list(editor.proto.column_order), ["Deliverable", "Quantity"])

    def test_manual_fallback_save_updates_header_and_enables_check(self):
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
        self.assertIn("EwAs", app.expander[0].label)
        self.assertIn("S&D Setup: Configured", app.expander[0].label)
        self.assertFalse(app.button(key="snd_check_demo partner").disabled)
        self.assertFalse(any(widget.label == "Function / category" for widget in app.selectbox))
        saved = SNDStore(self.path).list_requirements("Demo Partner")
        self.assertEqual([(row["kind"], row["name"], row["quantity"]) for row in saved], [("demand", "Goodie Bag", 20)])
        self.assertNotIn("notes", saved[0])
        app = self.app("SS")
        self.assertIn("EwAs", app.expander[0].label)

    def test_anonymous_does_not_read_configuration(self):
        app = self.app("ELDs", access="anonymous")
        self.assertFalse(self.path.exists())
        self.assertFalse(app.dataframe)
        self.assertFalse(app.button)
        self.assertTrue(any("members only" in info.value for info in app.info))
