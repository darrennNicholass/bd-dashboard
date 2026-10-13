# S&D persistence incident — October 12, 2026

## Finding and confidence

The user confirmed that the loss occurred on Streamlit Community Cloud and that
no dedicated S&D configuration spreadsheet had been created. The deployed code
selected `.local/snd_tracker.sqlite3` whenever `SND_CONFIG_SHEET_ID` was absent.
This is a confirmed unsafe storage design and the leading explanation for the
reported loss: a new Cloud filesystem has no copy of the previous SQLite file,
so the first access creates an empty database. Records entered afterward appear
normally until that filesystem is replaced again.

[Streamlit documents that Community Cloud does not guarantee local-file persistence](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).
The exact restart/redeploy event has **not** been verified: production secrets,
runtime files, and deployment logs are not available from this workspace.

## Evidence collected before changing code

- Working tree was clean; HEAD was `b269f8a` (October 11, 2026, 21:51 WIB).
  S&D was introduced in `a8a5234` (October 5). Both versions used the same SQLite
  fallback and same default path. The latest commit changed function provenance,
  navigation, and proof results, but did not introduce a database reset or change
  the backend-selection logic.
- Local environment, `.env`, and Streamlit secrets had no `SND_CONFIG_SHEET_ID`
  or `SND_DB_PATH`. Drive folder and service-account settings were present in
  `.env`. Credentials were inspected only for presence and were not printed.
- The local SQLite database passed `PRAGMA quick_check`. It held three requirements
  for two partners, created October 4–5, plus two function assignments, twenty
  invoice exceptions, and two fulfillment checks. It contained no October 11–12
  requirements. It is not a copy of the affected live Cloud filesystem.
- A consistent SQLite backup and raw JSON snapshot were saved under
  `.local/incident-backups/20261012T125642990303Z/`. A versioned migration snapshot
  is at `.local/incident-backups/recovery-snapshot-v1.json`. These files are private
  and ignored by Git. The original local database was not modified.
- All 59 pre-change regression tests passed. They tested reopening the same
  SQLite file, but did not test absent production storage or concurrent Sheet
  writers. This explains why the unsafe fallback passed the existing suite.

## Complete lifecycle at the time of the incident

| Stage | Code and behavior |
| --- | --- |
| Input | `app.py::tab_snd_tracker`, `_snd_editor`, `_snd_editor_rows`: editable Supply/Demand rows. `session_state` holds drafts and UI state, not the authoritative saved requirements. Seeing an edited row is not proof of a save; the Save button must complete. |
| Authorization | `may_access_real_snd` restricts the real S&D view to logged-in members. The application uses a shared member login, not per-user audit identities. |
| Validation | `save_partner_requirements` checks active ESSM membership and manual Function; `validate_requirements` validates IDs, kinds, names, and positive integer quantities before writing. |
| Backend choice | `get_store` now requires a dedicated Sheet ID plus a verified cutover receipt. SQLite is available only through explicit opt-in with an absolute persistent-disk path. `.env` and `.streamlit/secrets.toml` are ignored by Git; local configuration does not automatically reach Cloud. Setting precedence is process environment, local `.env`, then Streamlit secrets. |
| SQLite save | `SNDStore.replace_requirements` deletes and reinserts only the selected partner's requirements, upserts its Function, and invalidates its proof check within a transaction. Other partners are not deleted. IDs and original creation timestamps are preserved. |
| Sheet save | `GoogleSheetsSNDStore._save` previously rewrote one partner's complete JSON row. Cached payloads and cached row numbers could overwrite later edits or a row that moved. Appending then inferring its row number from sheet length was also unsafe under concurrency. No whole-sheet clear was found. |
| Initialization | SQLite used `CREATE TABLE IF NOT EXISTS`; it did not truncate existing data. Missing files were silently created. Legacy migration copied IDs, values, and timestamps within a transaction. Sheet initialization only created a missing `SND_Config` tab. |
| Retrieval | `list_requirements`, `requirement_counts`, and function assignments read the selected backend. No date-based requirement expiry, nightly reset, TTL deletion, or startup purge exists. |
| Partner matching | Saved configuration uses normalized partner names (case, punctuation, spacing). Substantive renames create different keys. Fuzzy Drive matching only affects proof checks; it does not delete saved requirements. |
| Rendering | The old view listed active partners in the selected period, then applied month, status, and search filters. Inactive/renamed partners could be invisible while their records still existed. October 11's function-provenance change could label legacy/ESSM assignments Not Configured without deleting their requirements. |

## Remediation implemented

1. `get_store()` requires durable Google Sheets configuration by default. Missing
   or invalid configuration is an actionable error, never a new SQLite fallback.
   SQLite requires explicit opt-in and an absolute path. Relative paths used by
   internal callers resolve against the repository rather than the process cwd.
   ESSM/National source spreadsheets cannot be selected as the writable store.
2. Google Sheets writes append versioned field changes to `SND_Config` using
   `values.append` with `INSERT_ROWS`. Existing snapshot rows remain readable and
   unchanged. Invoice/proof changes cannot rewrite requirement fields from a stale
   cached snapshot. Old values and removed requirements remain in the journal.
3. Each requirement edit includes its saved baseline revision. Conflicting events
   are retained in the journal but excluded from the current projection. Replay
   follows physical append order, and readback must confirm the new event applied
   before reporting success. This is application-level conflict handling, not a
   claim that Sheets provides database transactions. Transport failures never
   silently fall back to local writes or report confirmed success.
4. SQLite takes a write lock before comparing the revision and replacing rows.
   It archives the prior configuration in `requirement_history` in the same
   transaction. Failed writes roll back both the changes and the archive.
5. The editor retains its original baseline across reruns. If storage or saved
   values change, saving is disabled until the member explicitly reloads the
   saved version. An unsaved draft is not silently applied to a different dataset.
6. A member-only saved-records view and backup download work even when no active
   partner matches. A backend label and non-secret storage fingerprint help detect
   configuration changes. Backups include SQLite history or the complete Sheet
   journal, including rejected concurrent attempts.
7. `python -m src.snd_backup` exports an existing SQLite file read-only and can
   stage a migration against an explicit Sheet ID before production settings are
   changed. It creates checksummed source and destination backups, rejects any
   divergent destination record, preserves current IDs/timestamps/invoice/check
   data, verifies the full projection after readback, and only then writes a
   cutover receipt. Production requires that exact receipt ID. SQLite history
   remains preserved in the immutable source backup.
8. Only `cutover --apply` initializes an empty spreadsheet: it creates `SND_Config`
   with `partner_key`, `payload_json`, `updated_at` by appending (never overwriting).
   Other tabs remain untouched. A dry run is strictly read-only. The production app
   (with `SND_CUTOVER_ID`) never creates a worksheet or header; an uninitialized or
   unverified Sheet disables S&D instead. Any formula, unexpected content, partial
   header, or similarly named worksheet causes a fail-closed error and is not rewritten.
9. October 12 review fixes: a duplicate header from two simultaneous initializers is
   tolerated; a blank row between records blocks writes (reads continue); reruns share
   one Sheets connection and a 10-second journal cache (Sheets allows 60 reads/minute
   for the service account); reads retry on 429/5xx, writes retry only on 429 so a
   lost response is never duplicated; requests time out after 60 s; errors state the
   cause (no access, not found, rate limit, unreachable). Invoice/proof saves no longer
   re-read the journal. Proof results store unmatched files as kind+name (max 100,
   with `unmatched_total`) so large Drive folders fit one cell. The cutover accepts
   several `--sqlite` files and keeps the newest saved value per partner. Equal
   timestamps with different values stop the run. `SND_CONFIG_SHEET_ID` may be an ID or a full URL.
10. Members see a read-only **Legacy local S&D database found on this server** panel
    while the previous version's Cloud SQLite file still exists, with `.sqlite3` and
    `.json` downloads. It works even while S&D storage is disabled.

## Recovery and rollout — order matters

State on October 12: the dedicated spreadsheet exists, is empty, is shared with the
service account as Editor, and `SND_STORAGE_BACKEND` / `SND_CONFIG_SHEET_ID` are set
in Cloud. `SND_CUTOVER_ID` is not set yet. The deployed (old) code switches to that
Sheet as soon as `SND_CONFIG_SHEET_ID` exists, so the live S&D tab may now look
empty. **Do not save anything in the live S&D tab until step 5.** An old-format row
in the new Sheet makes the cutover stop (nothing is overwritten).

1. **Capture what members can still see.** If the live S&D tab still shows records,
   copy them (partner, Function, Supply/Demand, deliverable, quantity) to a private
   document. Do not reboot the app, change secrets, or let it sleep (12 h without
   traffic) before step 3. Each of these can discard the Cloud SQLite file.
2. Commit and push the reviewed code (code-only change; no dependency changes). Pushes
   normally update files in place without a reboot. That is likely, but not verified
   for this app. The S&D tab will show "migration has not been verified" and saving is
   disabled. That is expected.
3. As a member, open **S&D Tracker → Legacy local S&D database found on this server**
   and download both files into `.local/incident-backups/`. If the panel is absent,
   the Cloud file is already gone, and only the local backup and step 1 copies remain.
4. From this checkout, run a **read-only dry run** (no destination changes). Include
   every recovered database; the newest saved value per partner wins. The Sheet ID may
   be the full URL:

   ```powershell
   .venv/Scripts/python.exe -m src.snd_backup cutover `
     --sqlite .local/incident-backups/snd-legacy-XXXXXXXXXXXX.sqlite3 `
     --sqlite .local/snd_tracker.sqlite3 `
     --sheet-id "PASTE_THE_SND_STORAGE_SHEET_URL_OR_ID" `
     --artifacts-dir .local/incident-backups
   ```

   Review `source_partners`, `source_requirements`, and `superseded_values`. Then
   re-run the same command with `--apply`. The apply step initializes the empty
   Sheet, appends the records, verifies an exact readback, and writes the receipt.
   It can be resumed after a transport failure. Continue only on
   `"ready_for_production_switch": true`. Its `migration_id` is the `SND_CUTOVER_ID`.
5. Add `SND_CUTOVER_ID = "<migration_id>"` as a **top-level** Cloud secret next to the
   two existing S&D secrets (before any `[section]`). Then re-enter any step 1
   records that were not in the recovered files.
6. Validate: save one requirement, open a new browser session, confirm it in the
   Sheet, download **Download S&D backup**, then reboot the app and confirm it remains.

## Operational limits

Validation (October 12 review): all **118 tests passed** (`python -m unittest
discover -s tests -t .`), and `py_compile` and `git diff --check` passed. A rehearsal
of the exact cutover CLI with the real local backup used an in-memory destination:
the dry run made 0 writes, `--apply` imported 22 partners (3 requirements, 20 invoice
exceptions) and issued a receipt, and the source file hash was unchanged.
Google tests use in-memory fakes; the real service-account write and the live
restart check have not been run.

- No code was deployed and no live Google data was changed during this investigation.
- Keep `SND_Config` as an application-owned append-only journal. Do not manually
  reorder/delete journal rows, overwrite the header, or run old writers against
  the upgraded data. Export before any future format migration.
- The append journal grows over time; every load currently reads the full tab.
  Monitor Sheet size and API quotas. Move to a transactional managed database if
  volume warrants it; this change adds no new hosting service dependency.
- External deletion of the spreadsheet, account access loss, or changing its ID
  still requires operational backups/recovery. The application does not promise
  absolute permanence against those events. Download backups to a separate,
  access-controlled location regularly; local backups alone are not Cloud backups.
- SQLite history improves recovery on an intact persistent disk. It cannot make
  Streamlit Cloud's local disk durable. Never configure SQLite there.
