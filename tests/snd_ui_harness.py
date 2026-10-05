"""Isolated Streamlit harness for the production S&D view; no external I/O."""
import ast
import html
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from src import preparation, snd_tracker
from src.google_drive_service import DriveServiceError
from src.snd_matching import requirement_signature
from src.snd_security import may_access_real_snd
from src.snd_store import SNDStore, SNDStoreError, partner_key

app_path = Path(__file__).resolve().parents[1] / "app.py"
tree = ast.parse(app_path.read_text(encoding="utf-8"))
names = {"section_title", "empty_state", "_snd_editor", "_snd_editor_rows",
         "_snd_check_results", "tab_snd_tracker"}
nodes = [node for node in tree.body if
         (isinstance(node, ast.FunctionDef) and node.name in names) or
         (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
          target.id == "SND_STATUS_MARK" for target in node.targets))]
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(app_path), "exec"))


def get_store():
    return SNDStore(st.session_state.test_db_path)


active = pd.DataFrame([{"partner_name": "Demo Partner", "function": st.session_state.test_function}])
tab_snd_tracker({"frame": SimpleNamespace(df_partner_active=active)})
