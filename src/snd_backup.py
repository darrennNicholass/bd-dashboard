"""Read-only SQLite recovery and non-overwriting migration to durable Sheets.

Run from the project root: python -m src.snd_backup --help
No credentials or partner names are printed. Source databases are opened read-only.
A dry run is strictly read-only: it never creates a worksheet, header, partner
row, or cutover receipt in the destination spreadsheet.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from src.preparation import normalize_partner_function
from src.snd_cloud_store import GoogleSheetsSNDStore, PATCH_FIELDS
from src.snd_requirements import requirement_record
from src.snd_tracker import compact_check_result
from src.snd_store import (
    DEFAULT_DB, FUNCTIONS, INVOICE_REASONS, SNDStoreError, parse_sheet_id, partner_key,
    reject_source_sheet,
)

TABLES = ("requirements", "partner_functions", "invoice_exceptions",
          "fulfillment_checks", "requirement_history")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def snapshot_sha256(value: object) -> str:
    return sha256(_json_bytes(value)).hexdigest()


def write_json_exclusive(path: Path, value: object) -> str:
    """Durably publish a complete JSON artifact without replacing an old one."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SNDStoreError(f"Backup artifact already exists: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        with temporary.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        # A hard link is an atomic, non-overwriting publication on the same disk.
        os.link(temporary, path)
    except FileExistsError as exc:
        raise SNDStoreError(f"Backup artifact already exists: {path.name}") from exc
    except OSError as exc:
        raise SNDStoreError("Backup artifact could not be written safely.") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(raw).hexdigest()


def read_sqlite_snapshot(path: Path) -> dict:
    """mode=ro is essential: a typo must not create a new empty database."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SNDStoreError("Source SQLite integrity check failed. Preserve the original for recovery.")
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "requirements" not in present:
            raise SNDStoreError("The source database has no S&D requirements table.")
        tables = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                  for table in TABLES if table in present}
    return {"format": "snd-sqlite-snapshot-v1", "exported_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables}


def snapshot_partners(snapshot: dict) -> dict[str, dict]:
    if snapshot.get("format") != "snd-sqlite-snapshot-v1":
        raise SNDStoreError("Import requires a snd-sqlite-snapshot-v1 backup.")
    tables = snapshot["tables"]
    partners = {}
    for item in tables.get("requirements", []):
        key = item["partner_key"]
        data = partners.setdefault(key, {})
        data["partner_name"] = item["partner_name"]
        data.setdefault("requirements", []).append(requirement_record(item))
    for item in tables.get("partner_functions", []):
        data = partners.setdefault(item["partner_key"], {})
        data.update(
            function=item["function"], function_source=item.get("source", "legacy"),
        )
        if item.get("updated_at"):
            data.setdefault("migration_metadata", {})["function_updated_at"] = item["updated_at"]
    for item in tables.get("invoice_exceptions", []):
        data = partners.setdefault(item["partner_key"], {})
        data["invoice_exception"] = item["reason"]
        if item.get("updated_at"):
            data.setdefault("migration_metadata", {})["invoice_updated_at"] = item["updated_at"]
    for item in tables.get("fulfillment_checks", []):
        # Older versions stored every unmatched Drive file in full. Keep the same
        # compact form the app now saves so one large check cannot exceed a cell.
        partners.setdefault(item["partner_key"], {})["check"] = {
            "signature": item["requirements_signature"],
            "result": compact_check_result(json.loads(item["result_json"])),
            "checked_at": item["checked_at"],
        }
    for key, data in partners.items():
        if partner_key(data.get("partner_name", key)) != key:
            raise SNDStoreError("Source partner name/key mismatch. Review the backup before importing.")
    return partners


def _canonical_partner(data: dict) -> dict:
    result = {key: data[key] for key in sorted(PATCH_FIELDS) if key in data}
    if "requirements" in result:
        result["requirements"] = sorted(result["requirements"], key=lambda row: str(row.get("id", "")))
    return result


def _canonical_projection(partners: dict[str, dict]) -> dict[str, dict]:
    return {key: _canonical_partner(partners[key]) for key in sorted(partners)}


def _validate_partner_payloads(partners: dict[str, dict]) -> None:
    """Validate every row and cell size before the first remote partner write."""
    from src.snd_cloud_store import EVENT_MARKER
    from src.snd_requirements import validate_requirements

    for key, data in partners.items():
        if not key or partner_key(data.get("partner_name", key)) != key:
            raise SNDStoreError("Source partner identity is invalid; migration was not started.")
        requirements = data.get("requirements", [])
        if requirements:
            validate_requirements(requirements)
        function = normalize_partner_function(data.get("function"))
        if data.get("function") is not None and function not in FUNCTIONS:
            raise SNDStoreError("Source partner Function is invalid; migration was not started.")
        if data.get("function_source") not in {None, "manual", "essm", "legacy"}:
            raise SNDStoreError("Source Function provenance is invalid; migration was not started.")
        if data.get("invoice_exception") not in {None, *INVOICE_REASONS}:
            raise SNDStoreError("Source invoice exception is invalid; migration was not started.")
        check = data.get("check")
        if check is not None and (not isinstance(check, dict)
                                  or not isinstance(check.get("signature"), str)
                                  or not isinstance(check.get("result"), dict)
                                  or not isinstance(check.get("checked_at"), str)):
            raise SNDStoreError("Source fulfillment check is invalid; migration was not started.")
        event = {EVENT_MARKER: 1, "id": "preflight", "expected_revision": "preflight",
                 "expected_absent": True, "changes": _canonical_partner(data)}
        if len(json.dumps(event, ensure_ascii=False)) > 45000:
            raise SNDStoreError("A source partner record exceeds the Google Sheets cell limit.")


def verified_cutover(snapshot: dict, store: GoogleSheetsSNDStore, *, apply: bool,
                     migration_id: str, source_backup_sha256: str) -> dict:
    """Single-backup convenience wrapper around verified_cutover_partners."""
    return verified_cutover_partners(
        snapshot_partners(snapshot), store, apply=apply, migration_id=migration_id,
        source_backup_sha256=source_backup_sha256,
    )


def verified_cutover_partners(source: dict[str, dict], store: GoogleSheetsSNDStore, *, apply: bool,
                              migration_id: str, source_backup_sha256: str,
                              source_backups: list[str] | None = None) -> dict:
    """Resume exact rows, reject divergence, verify readback, then issue a receipt."""
    _validate_partner_payloads(source)
    source_projection = _canonical_projection(source)
    source_state_sha = snapshot_sha256(source_projection)
    existing = store.partner_snapshots(refresh=True)
    existing_projection = _canonical_projection(existing)
    extra = set(existing_projection) - set(source_projection)
    divergent = {key for key in existing_projection.keys() & source_projection.keys()
                 if existing_projection[key] != source_projection[key]}
    if extra or divergent:
        raise SNDStoreError(
            f"Destination already contains {len(extra)} partner(s) not in the backups and "
            f"{len(divergent)} divergent partner(s) with different values. Nothing was overwritten; "
            "review the destination backup artifact before retrying."
        )
    missing = sorted(set(source_projection) - set(existing_projection))
    result = {
        "migration_id": migration_id,
        "source_partners": len(source_projection),
        "source_requirements": sum(len(data.get("requirements", [])) for data in source_projection.values()),
        "already_verified_partners": len(existing_projection),
        "missing_partners": len(missing),
        "imported_partners": 0,
        "source_backup_sha256": source_backup_sha256,
        "source_state_sha256": source_state_sha,
        "ready_for_production_switch": False,
    }
    if source_backups:
        result["source_backup_sha256s"] = list(source_backups)
    if not apply:
        return result
    if store.read_only:
        raise SNDStoreError("Cutover --apply requires a writable destination connection.")
    result["imported_partners"] = store.import_partner_snapshots({key: source[key] for key in missing})
    destination_projection = _canonical_projection(store.partner_snapshots(refresh=True))
    if destination_projection != source_projection:
        raise SNDStoreError(
            "Migration readback did not exactly match the source. No cutover receipt was written."
        )
    receipt = {
        **result,
        "destination_state_sha256": snapshot_sha256(destination_projection),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_production_switch": True,
    }
    store.record_cutover(receipt)
    # A fresh reader must see both the exact projection and the receipt.
    verifier = GoogleSheetsSNDStore(
        store.sheet_id, spreadsheet=store._spreadsheet, expected_cutover_id=migration_id,
    )
    if _canonical_projection(verifier.partner_snapshots(refresh=True)) != source_projection:
        raise SNDStoreError("Post-receipt verification failed; production remains disabled.")
    receipt["ready_for_production_switch"] = True
    return receipt


def migrate_missing(snapshot: dict, store: GoogleSheetsSNDStore, *, apply: bool = False) -> dict:
    partners = snapshot_partners(snapshot)
    existing = store.partner_snapshots(refresh=True)
    divergent = {key for key in existing.keys() & partners.keys()
                 if _canonical_partner(existing[key]) != _canonical_partner(partners[key])}
    if divergent:
        raise SNDStoreError("Destination partner data differs from the backup; nothing was overwritten.")
    missing = {key: data for key, data in partners.items() if key not in existing}
    # Validate the entire import before any remote write.
    from src.snd_requirements import validate_requirements
    for data in missing.values():
        if data.get("requirements"):
            validate_requirements(data["requirements"])
    result = {"source_partners": len(partners), "missing_partners": len(missing),
              "existing_partners_verified": len(partners) - len(missing), "imported_partners": 0}
    if apply:
        result["imported_partners"] = store.import_partner_snapshots(missing)
        final = store.partner_snapshots(refresh=True)
        for key, data in partners.items():
            if key not in final or _canonical_partner(final[key]) != _canonical_partner(data):
                raise SNDStoreError("Migration readback verification failed.")
    return result


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_CONFIG_FIELDS = ("partner_name", "requirements", "function", "function_source")


def _parse_time(value) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _group_view(data: dict, group: str) -> tuple[dict, datetime | None, dict]:
    """Return (values, newest saved timestamp, migration metadata) for one field group."""
    metadata = data.get("migration_metadata", {})
    if group == "config":
        content = {field: data[field] for field in _CONFIG_FIELDS if field in data}
        if not ({"requirements", "function"} & content.keys()):
            content = {}
        stamps = [row.get("updated_at") for row in data.get("requirements", [])]
        stamps.append(metadata.get("function_updated_at"))
        meta_key = "function_updated_at"
    elif group == "invoice":
        content = {"invoice_exception": data["invoice_exception"]} if "invoice_exception" in data else {}
        stamps = [metadata.get("invoice_updated_at")]
        meta_key = "invoice_updated_at"
    else:
        content = {"check": data["check"]} if "check" in data else {}
        stamps = [data.get("check", {}).get("checked_at")]
        meta_key = None
    parsed = [stamp for stamp in map(_parse_time, filter(None, stamps)) if stamp]
    meta = {meta_key: metadata[meta_key]} if meta_key and meta_key in metadata else {}
    return content, (max(parsed) if parsed else None), meta


def _same(left: dict, right: dict) -> bool:
    return snapshot_sha256(_canonical_partner(left)) == snapshot_sha256(_canonical_partner(right))


def merge_partner_sources(sources: list[dict[str, dict]]) -> tuple[dict[str, dict], dict]:
    """Combine recovered backups without letting an older value replace a newer one.

    Per partner, the configuration (requirements + Function) and the invoice
    exception are chosen independently by their own saved timestamps. A proof
    check is kept only when it belongs to the chosen configuration. Different
    values without a strictly newer timestamp stop the migration for manual
    review. Superseded values stay in the immutable per-source backups.
    """
    merged: dict[str, dict] = {}
    report = {"format": "snd-merge-report-v1", "sources": len(sources),
              "superseded_values": 0, "partners": {}}
    for key in sorted(set().union(*sources)):
        result: dict = {}
        metadata: dict = {}
        choices: dict = {}
        chosen_config = None
        for group in ("config", "invoice", "check"):
            candidates = []
            for index, source in enumerate(sources):
                if key not in source:
                    continue
                content, stamp, meta = _group_view(source[key], group)
                if not content:
                    continue
                if group == "check" and chosen_config is not None and not _same(
                        _group_view(source[key], "config")[0], chosen_config):
                    continue  # A proof check for another configuration is stale.
                candidates.append((index, content, stamp, meta))
            if not candidates:
                continue
            ranked = sorted(candidates, key=lambda item: item[2] or _EPOCH, reverse=True)
            winner = ranked[0]
            rivals = [item for item in ranked[1:] if not _same(item[1], winner[1])]
            if rivals and (winner[2] is None or any(item[2] == winner[2] for item in rivals)):
                raise SNDStoreError(
                    "Recovered backups contain different S&D values for the same partner without a "
                    "newer timestamp. Nothing was migrated; review the source backups manually."
                )
            result.update(copy.deepcopy(winner[1]))
            metadata.update(winner[3])
            if group == "config":
                chosen_config = winner[1]
            report["superseded_values"] += len(rivals)
            choices[group] = {
                "source": winner[0] + 1,
                "saved_at": winner[2].isoformat() if winner[2] else None,
                "superseded_sources": [item[0] + 1 for item in rivals],
            }
        if metadata:
            result["migration_metadata"] = metadata
        merged[key] = result
        report["partners"][key] = choices
    return merged, report


def legacy_sqlite_backup(path: Path | None = None) -> dict | None:
    """Read-only view of the pre-cutover SQLite file left on this server, if any.

    The previous version wrote S&D data to DEFAULT_DB on the Cloud container.
    That file is lost on reboot/hibernation, so members can download it once
    for migration. It is opened with mode=ro and never created or modified.
    """
    if path is None:
        from src.google_drive_service import optional_setting

        if (optional_setting("SND_STORAGE_BACKEND") or "").lower() == "sqlite":
            return None  # The file is the active store, not a legacy leftover.
        path = DEFAULT_DB
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return {"error": "A legacy S&D database exists on this server but could not be read."}
    result = {"raw": raw, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
    try:
        snapshot = read_sqlite_snapshot(path)
        partners = snapshot_partners(snapshot)
    except (SNDStoreError, sqlite3.Error, ValueError, KeyError, TypeError):
        result["error"] = ("The legacy database could not be read as an S&D snapshot. "
                           "Download the raw file and keep it for manual recovery.")
        return result
    tables = snapshot["tables"]
    stamps = sorted(str(row[field]) for rows in tables.values() for row in rows
                    for field in ("created_at", "updated_at", "checked_at") if row.get(field))
    result.update(
        snapshot=snapshot, partners=len(partners),
        requirements=len(tables.get("requirements", [])),
        invoice_exceptions=len(tables.get("invoice_exceptions", [])),
        first_change=stamps[0] if stamps else None, latest_change=stamps[-1] if stamps else None,
    )
    return result


def _sheet_id_argument(value: str) -> str:
    sheet_id = parse_sheet_id(value)
    if not sheet_id:
        raise argparse.ArgumentTypeError("must be a spreadsheet ID or a Google Sheets URL")
    return sheet_id


def _write_source_backups(paths: list[Path], artifacts_dir: Path, timestamp: str,
                          migration_id: str) -> tuple[list[dict], list[str]]:
    """Checksum every source before any destination access."""
    partner_sources, hashes = [], []
    for position, path in enumerate(paths, start=1):
        snapshot = read_sqlite_snapshot(path)
        partners = snapshot_partners(snapshot)
        source_path = artifacts_dir / f"snd-source-{timestamp}-{migration_id}-{position}.json"
        source_sha = write_json_exclusive(source_path, snapshot)
        write_json_exclusive(source_path.with_name(source_path.name + ".manifest.json"), {
            "format": "snd-backup-manifest-v1", "backup_file": source_path.name,
            "backup_sha256": source_sha, "migration_id": migration_id, "source_position": position,
            "source_file": path.name,
            "requirements": len(snapshot["tables"].get("requirements", [])),
            "partners": len(partners),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        partner_sources.append(partners)
        hashes.append(source_sha)
    return partner_sources, hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Export an existing SQLite database without modifying it")
    export.add_argument("--sqlite", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    migrate = sub.add_parser("migrate", help="Import missing partners into an explicit staging Sheet; defaults to dry-run")
    migrate.add_argument("--snapshot", type=Path, required=True)
    migrate.add_argument("--sheet-id", required=True, type=_sheet_id_argument,
                         help="Explicit staging destination; does not use production backend settings")
    migrate.add_argument("--apply", action="store_true")
    cutover = sub.add_parser(
        "cutover", help="Backup, stage, verify, and issue a production cutover receipt",
    )
    cutover.add_argument("--sqlite", type=Path, required=True, action="append",
                         help="Repeat for every recovered database; the newest saved value wins")
    cutover.add_argument("--sheet-id", required=True, type=_sheet_id_argument,
                         help="Destination spreadsheet ID or URL; production settings are not read")
    cutover.add_argument("--artifacts-dir", type=Path, required=True)
    cutover.add_argument("--apply", action="store_true",
                         help="Write to the destination. Without it the run is strictly read-only.")
    args = parser.parse_args()
    try:
        if args.command == "export":
            snapshot = read_sqlite_snapshot(args.sqlite)
            backup_sha = write_json_exclusive(args.output, snapshot)
            manifest = {
                "format": "snd-backup-manifest-v1", "backup_file": args.output.name,
                "backup_sha256": backup_sha,
                "requirements": len(snapshot["tables"]["requirements"]),
                "partners": len(snapshot_partners(snapshot)),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            manifest_path = args.output.with_name(args.output.name + ".manifest.json")
            write_json_exclusive(manifest_path, manifest)
            print(json.dumps({"exported_requirements": manifest["requirements"],
                              "exported_partners": manifest["partners"],
                              "backup_sha256": backup_sha}))
        elif args.command == "migrate":
            reject_source_sheet(args.sheet_id)
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            store = GoogleSheetsSNDStore(args.sheet_id, read_only=not args.apply)
            print(json.dumps(migrate_missing(snapshot, store, apply=args.apply)))
        else:
            reject_source_sheet(args.sheet_id)
            migration_id = uuid4().hex
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            sources, hashes = _write_source_backups(args.sqlite, args.artifacts_dir, timestamp, migration_id)
            if len(sources) == 1:
                partners, merge_report, combined_sha = sources[0], None, hashes[0]
            else:
                partners, merge_report = merge_partner_sources(sources)
                combined_sha = snapshot_sha256(hashes)
                merge_report["source_backup_sha256s"] = hashes
                write_json_exclusive(
                    args.artifacts_dir / f"snd-merge-report-{timestamp}-{migration_id}.json", merge_report,
                )
            store = GoogleSheetsSNDStore(args.sheet_id, read_only=not args.apply)
            destination_before = store.export_snapshot(refresh=True)
            destination_path = args.artifacts_dir / f"snd-destination-before-{timestamp}-{migration_id}.json"
            write_json_exclusive(destination_path, destination_before)
            result = verified_cutover_partners(
                partners, store, apply=args.apply, migration_id=migration_id,
                source_backup_sha256=combined_sha, source_backups=hashes if len(hashes) > 1 else None,
            )
            if merge_report is not None:
                result["superseded_values"] = merge_report["superseded_values"]
            if args.apply:
                receipt_path = args.artifacts_dir / f"snd-cutover-receipt-{timestamp}-{migration_id}.json"
                write_json_exclusive(receipt_path, result)
            else:
                result["dry_run"] = "No destination changes were made. Re-run with --apply to migrate."
            print(json.dumps(result))
    except SNDStoreError as exc:
        parser.exit(1, f"{exc}\n")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        parser.exit(1, "Backup operation failed. Check the source format, paths, and file permissions.\n")


if __name__ == "__main__":
    main()
