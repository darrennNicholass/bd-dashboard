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


def resolve_partner_function(essm_function, persisted: dict | None = None) -> FunctionResolution:
    persisted = persisted or {}
    manual = normalize_partner_function(persisted.get("function"))
    if manual and persisted.get("source", "manual") == "manual":
        return FunctionResolution(manual, "manual")
    detected = normalize_partner_function(essm_function)
    return FunctionResolution(detected, "essm" if detected else None)


def active_partner_functions(active: pd.DataFrame, assignments: dict) -> dict[str, FunctionResolution]:
    candidates: dict[str, set[str]] = {}
    for row in active.to_dict("records"):
        values = candidates.setdefault(partner_key(row["partner_name"]), set())
        if function := normalize_partner_function(row.get("function")):
            values.add(function)
    return {
        key: resolve_partner_function(next(iter(values)) if len(values) == 1 else None, assignments.get(key))
        for key, values in candidates.items()
    }


def save_partner_requirements(
    partner_name: str, active: pd.DataFrame, requirements: list[dict], *, store,
    manual_function: str | None = None,
) -> list[dict]:
    resolutions = active_partner_functions(active, store.get_partner_function_assignments())
    resolution = resolutions.get(partner_key(partner_name))
    if resolution is None:
        raise ValueError("Partner tidak ditemukan dalam daftar partner aktif ESSM.")
    if resolution.value is None:
        resolution = FunctionResolution(normalize_partner_function(manual_function), "manual")
    if resolution.value is None:
        raise ValueError("Select the partner function before saving S&D requirements.")
    return store.replace_requirements(
        partner_name, resolution.value, requirements, function_source=resolution.source,
    )


def can_check_fulfillment(requirements: list[dict], function) -> bool:
    return bool(requirements) and normalize_partner_function(function) is not None


def check_partner_fulfillment(
    partner_name: str,
    function: str,
    *,
    store: SNDStore | None = None,
    drive: GoogleDriveService | None = None,
    root_id: str | None = None,
) -> dict:
    """Make Drive requests only when the member presses Check Fulfillment."""
    function = normalize_partner_function(function)
    if function is None:
        raise ValueError("Select the partner function before checking fulfillment.")
    store = store or get_store()
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
    store.save_check(partner_name, signature, result)
    return result
