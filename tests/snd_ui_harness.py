"""Isolated Streamlit harness for the production S&D view; no external I/O."""
import ast
import copy
import html
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from src import periods, preparation, snd_backup as _production_backup, snd_tracker
from src.google_drive_service import DriveServiceError
from src.snd_cloud_store import GoogleSheetsSNDStore
from src.snd_matching import requirement_signature
from src.snd_requirements import requirements_revision
from src.snd_security import may_access_real_snd
from src.snd_store import SNDStore, SNDStoreError, partner_key

app_path = Path(__file__).resolve().parents[1] / "app.py"
tree = ast.parse(app_path.read_text(encoding="utf-8"))
names = {"section_title", "empty_state", "_snd_editor_key", "_snd_cache_partner_draft",
         "_snd_apply_editor_state", "_snd_capture_editor_draft", "_snd_checked_at_label", "_snd_editor",
         "_snd_editor_rows", "_snd_check_results", "_snd_legacy_rescue_panel",
         "_snd_release_partner_pin", "_snd_reload_saved_requirements", "tab_snd_tracker"}
nodes = [node for node in tree.body if
         (isinstance(node, ast.FunctionDef) and node.name in names) or
         (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
          target.id == "SND_STATUS_MARK" for target in node.targets))]
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(app_path), "exec"))


def get_store():
    if st.session_state.get("test_store_error"):
        raise SNDStoreError(st.session_state.test_store_error)
    if st.session_state.get("test_backend") == "google":
        from tests.fake_sheets import BOOKS
        return GoogleSheetsSNDStore(
            "ui-test", spreadsheet=BOOKS[st.session_state.test_book_id],
            expected_cutover_id=st.session_state.get("test_cutover_id"),
        )
    return SNDStore(st.session_state.test_db_path)


def _legacy_sqlite_backup():
    path = st.session_state.get("test_legacy_path")
    return _production_backup.legacy_sqlite_backup(Path(path)) if path else None


snd_backup = SimpleNamespace(legacy_sqlite_backup=_legacy_sqlite_backup)


_production_tracker = snd_tracker


def _check_partner_fulfillment(partner_name, *, store=None, drive=None, root_id=None):
    fresh_result = st.session_state.get("test_fresh_check")
    if fresh_result is None:
        return _production_tracker.check_partner_fulfillment(
            partner_name, store=store, drive=drive, root_id=root_id,
        )
    store = store or get_store()
    assignment = store.get_partner_function_assignments().get(partner_key(partner_name))
    function = _production_tracker.resolve_partner_function(assignment).value
    requirements = store.list_requirements(partner_name)
    signature = requirement_signature(requirements, function)
    st.session_state.test_check_calls = st.session_state.get("test_check_calls", 0) + 1
    return store.save_check(partner_name, signature, copy.deepcopy(fresh_result))


snd_tracker = SimpleNamespace(
    active_partner_functions=_production_tracker.active_partner_functions,
    partner_fulfillment_status=_production_tracker.partner_fulfillment_status,
    can_check_fulfillment=_production_tracker.can_check_fulfillment,
    save_partner_requirements=_production_tracker.save_partner_requirements,
    check_partner_fulfillment=_check_partner_fulfillment,
)


today = pd.Timestamp("2026-10-07")
specs = st.session_state.get("test_partner_specs") or [
    {"name": name, "end": "2027-01"}
    for name in st.session_state.get("test_partner_names", ["Demo Partner"])
]
source = pd.DataFrame([
    {
        "partner_name": item["name"],
        "function": st.session_state.test_function,
        "has_loa": True,
        "end_month": pd.Period(item.get("end", "2027-01"), freq="M"),
        "end_date": pd.Period(item.get("end", "2027-01"), freq="M").end_time.normalize(),
    }
    for item in specs
])
months = tuple(st.session_state.get("test_months", ("august", "september", "october")))
frame = SimpleNamespace(
    df_partner=source,
    df_partner_active=periods.active_partner_scope(source, months, today=today),
    months=months,
    reference_date=today,
)
tab_snd_tracker({"frame": frame})
