"""Read-only Google Drive metadata scanner for S&D proof folders."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from src.preparation import normalize_partner_function
from src.snd_matching import select_partner_folder

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def normalize_function_folder(name: str) -> str | None:
    """Map a numbered Drive folder to the canonical partner function."""
    label = re.sub(r"^\s*\d+(?:\.\s*|\s+)", "", str(name).strip())
    return normalize_partner_function(label)


class DriveServiceError(RuntimeError):
    """Safe user-facing error with no URLs, IDs, or credential detail."""


def parse_env_setting(content: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*", content)
    if not match:
        return None
    rest = content[match.end():].lstrip()
    if rest.startswith("{"):
        try:
            _, end = json.JSONDecoder().raw_decode(rest)
            return rest[:end]
        except json.JSONDecodeError:
            return None
    value = rest.splitlines()[0].strip() if rest else ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value or None


def _secret(name: str) -> Any | None:
    try:
        import streamlit as st
        return st.secrets[name]
    except (KeyError, FileNotFoundError):
        return None


def optional_setting(name: str) -> str | None:
    value = os.getenv(name)
    if value and value.strip() and value.strip() != "{":
        return value.strip()
    try:
        value = parse_env_setting(ENV_PATH.read_text(encoding="utf-8"), name)
    except OSError:
        value = None
    if value:
        return value
    secret = _secret(name)
    if secret:
        return str(secret)
    return None


def setting(name: str) -> str:
    value = optional_setting(name)
    if value is None:
        raise DriveServiceError(f"Konfigurasi {name} belum tersedia.")
    return value


def service_account_info() -> dict:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT")
    if not raw or raw.strip() == "{":
        try:
            raw = parse_env_setting(ENV_PATH.read_text(encoding="utf-8"), "GOOGLE_SERVICE_ACCOUNT")
        except OSError:
            raw = None
    if not raw:
        raw = _secret("GOOGLE_SERVICE_ACCOUNT") or _secret("gcp_service_account")
    if not raw:
        raise DriveServiceError("Google Service Account belum dikonfigurasi.")
    try:
        info = dict(raw) if not isinstance(raw, str) else json.loads(raw)
        info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info
    except (ValueError, TypeError, KeyError) as exc:
        raise DriveServiceError("Google Service Account tidak valid.") from exc


def drive_credentials() -> Credentials:
    try:
        return Credentials.from_service_account_info(service_account_info(), scopes=[DRIVE_SCOPE])
    except (ValueError, TypeError, KeyError) as exc:
        raise DriveServiceError("Google Service Account tidak valid.") from exc


class GoogleDriveService:
    def __init__(self, session: Any | None = None):
        self.session = session if session is not None else AuthorizedSession(drive_credentials())

    def _get(self, url: str, params: dict):
        try:
            response = self.session.get(url, params=params, timeout=45)
        except Exception as exc:
            raise DriveServiceError("Google Drive tidak dapat dihubungi.") from exc
        status = response.status_code
        if status in (401, 403):
            raise DriveServiceError("Service Account tidak memiliki akses Viewer ke S&D Drive.")
        if status == 404:
            raise DriveServiceError("Folder S&D Drive tidak ditemukan.")
        if status >= 400:
            raise DriveServiceError(f"Google Drive gagal merespons (status {status}).")
        return response.json()

    def list_children(self, folder_id: str) -> list[dict]:
        children = []
        token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,modifiedTime)",
                "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            page = self._get(DRIVE_URL, params)
            children.extend({"id": item["id"], "name": item["name"],
                             "mime_type": item["mimeType"],
                             "modified_time": item.get("modifiedTime", "")}
                            for item in page.get("files", []))
            token = page.get("nextPageToken")
            if not token:
                return children

    def function_folders(self, root_id: str) -> dict[str, dict]:
        result = {}
        for item in self.list_children(root_id):
            function = normalize_function_folder(item["name"])
            if item["mime_type"] == FOLDER_MIME and function:
                if function in result:
                    raise DriveServiceError(f"Folder fungsi {function} ganda; periksa struktur S&D Drive.")
                result[function] = item
        return result

    def find_partner(self, partner_name: str, function: str | None, root_id: str) -> tuple[dict | None, str, str | None]:
        function = normalize_partner_function(function)
        if function is None:
            return None, "needs_review", None
        functions = self.function_folders(root_id)
        candidates = []
        folder = functions.get(function)
        if folder:
            candidates.extend({**item, "function": function}
                              for item in self.list_children(folder["id"])
                              if item["mime_type"] == FOLDER_MIME)
        match, status = select_partner_folder(partner_name, candidates)
        return match, status, match["function"] if match else None

    def scan_partner_evidence(self, partner_name: str, function: str | None, root_id: str) -> dict:
        partner, status, matched_function = self.find_partner(partner_name, function, root_id)
        if partner is None:
            return {"partner_match": status, "function": function,
                    "evidence": [], "warnings": ["Folder partner perlu diperiksa secara manual."]}
        evidence = []
        warnings = []
        children = self.list_children(partner["id"])
        for kind in ("supply", "demand"):
            folders = [item for item in children if item["mime_type"] == FOLDER_MIME
                       and re.sub(r"[^a-z]", "", item["name"].casefold()) == kind]
            if len(folders) != 1:
                warnings.append(f"Folder {kind.title()} tidak ditemukan atau ganda.")
                continue
            queue = [folders[0]["id"]]
            while queue:
                for item in self.list_children(queue.pop()):
                    if item["mime_type"] == FOLDER_MIME:
                        queue.append(item["id"])
                    else:
                        evidence.append({**item, "kind": kind})
        return {"partner_match": "matched", "function": matched_function,
                "folder_name": partner["name"], "evidence": evidence, "warnings": warnings}
