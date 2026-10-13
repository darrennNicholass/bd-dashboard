"""Manual S&D configuration and on-demand Drive proof checks."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.preparation import normalize_partner_function
from src.google_drive_service import GoogleDriveService, setting
from src.snd_matching import match_evidence, requirement_signature
from src.snd_store import SNDStore, get_store, partner_key


@dataclass(frozen=True)
class FunctionResolution:
    value: str | None
    source: str | None = None


def resolve_partner_function(persisted: dict | None = None) -> FunctionResolution:
    """Return only a Function explicitly selected for the S&D tracker."""
    persisted = persisted or {}
    manual = normalize_partner_function(persisted.get("function"))
    if manual and persisted.get("source") == "manual":
        return FunctionResolution(manual, "manual")
    return FunctionResolution(None, None)


def active_partner_functions(active: pd.DataFrame, assignments: dict) -> dict[str, FunctionResolution]:
    """Resolve S&D Functions without reading ESSM or Market Research values."""
    keys = {partner_key(name) for name in active["partner_name"].astype(str)}
    return {
        key: resolve_partner_function(assignments.get(key))
        for key in keys
    }


def save_partner_requirements(
    partner_name: str, active: pd.DataFrame, requirements: list[dict], *, store,
    manual_function: str | None = None,
    expected_revision: str | None = None,
) -> list[dict]:
    active_keys = {partner_key(name) for name in active["partner_name"].astype(str)}
    if partner_key(partner_name) not in active_keys:
        raise ValueError("Partner tidak ditemukan dalam daftar partner aktif ESSM.")
    function = normalize_partner_function(manual_function)
    if function is None:
        raise ValueError("Select the partner function before saving S&D requirements.")
    return store.replace_requirements(
        partner_name, function, requirements, function_source="manual", expected_revision=expected_revision,
    )


def can_check_fulfillment(requirements: list[dict], function) -> bool:
    return bool(requirements) and normalize_partner_function(function) is not None


UNMATCHED_EVIDENCE_LIMIT = 100


def compact_check_result(result: dict) -> dict:
    """Keep a stored proof result small enough for one Google Sheets cell.

    Unmatched files only need their kind and name in the UI. Drive IDs, MIME
    types, and timestamps are dropped and the list is capped; the full count is
    kept in unmatched_total.
    """
    unmatched = result.get("unmatched_evidence", [])
    return {
        **result,
        "unmatched_total": result.get("unmatched_total", len(unmatched)),
        "unmatched_evidence": [
            {"kind": item.get("kind"), "name": item.get("name")}
            for item in unmatched[:UNMATCHED_EVIDENCE_LIMIT]
        ],
    }


def partner_fulfillment_status(
    requirements: list[dict], function: str | None, check: dict | None,
) -> str:
    """Return one exclusive workflow status for S&D navigation filters."""
    if not requirements or normalize_partner_function(function) is None:
        return "Not Configured"
    if not check:
        return "Configured"
    status = check.get("status")
    if status == "COMPLETED":
        return "Fulfilled"
    if status == "NEEDS_REVIEW":
        return "Needs Review"
    if status in {"MISSING", "PARTIAL"}:
        return "Missing Proof"
    return "Configured"


def check_partner_fulfillment(
    partner_name: str,
    *,
    store: SNDStore | None = None,
    drive: GoogleDriveService | None = None,
    root_id: str | None = None,
) -> dict:
    """Scan Drive using the partner's persisted manual S&D Function."""
    store = store or get_store()
    assignment = store.get_partner_function_assignments().get(partner_key(partner_name))
    function = resolve_partner_function(assignment).value
    if function is None:
        raise ValueError("Select the partner function before checking fulfillment.")
    requirements = store.list_requirements(partner_name)
    if not requirements:
        raise ValueError("Konfigurasi S&D partner belum disimpan.")
    signature = requirement_signature(requirements, function)
    drive = drive or GoogleDriveService()
    scan = drive.scan_partner_evidence(
        partner_name, function, root_id or setting("SND_FOLDER_ID")
    )
    result = match_evidence(requirements, scan["evidence"])
    result["partner_match"] = scan["partner_match"]
    result["function"] = scan.get("function") or function
    result["warnings"] = scan["warnings"]
    if scan["partner_match"] != "matched":
        result["status"] = "NEEDS_REVIEW"
    # save_check replaces the complete previous result; warnings and match state
    # from an older scan are never merged into this fresh Drive response.
    return store.save_check(partner_name, signature, compact_check_result(result))
