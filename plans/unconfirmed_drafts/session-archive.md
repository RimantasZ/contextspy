# Draft 3: Session lifecycle and explicit archive

Status: DRAFT (not approved). Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). Decisions D3, D4.

## Why

The current retention policy was a prototype decision and now causes more problems than it solves:
`db/database.py:startup_vacuum` silently nulls raw bodies (`raw_*`, `canonical_*`, `response_events`)
and deletes `block_contents` older than `[retention]` days (default 7, config in `config.py:
RetentionSettings`) **only at server startup**, per request timestamp, regardless of session
state. A user analysing a week-old session finds content vanished with no explanation, and a
long-running process never purges at all (inconsistent). Retention should be explicit and visible.

## Agreed decisions

- Session lifecycle: `active → ended → archived`. Today only `is_active` + `ended_at` exist
  (`db/models.py: Session`, `crud.end_session`, `POST /sessions/{id}/end`).
- **Archive is a specific user action on the session screen**, **one-way**, **with confirmation**.
- **No auto-archive rule in the first version.**
- Analysis views must keep working on archived sessions (block rows remain: hash, type, category,
  token_count, tool metadata). Content-less blocks show as greyed nodes with tokens.

## Proposed design (to confirm)

- Additive column `sessions.archived_at` (nullable DateTime). Derive `status` in `Session.to_dict`:
  `active` (is_active) / `ended` / `archived` (archived_at set). Avoids a status enum migration.
- `POST /api/sessions/{id}/archive` — allowed only for ended sessions (409 otherwise). Within a
  transaction (and respecting `db/maintenance_lock.py` — verify how it is used):
  1. null raw bodies / canonical bodies / response_events for that session's requests;
  2. delete `block_contents` whose hash is **not referenced by any block of a non-archived session**
     (content is a global hash-keyed store, shared across sessions);
  3. set `archived_at`.
  Return counts (bytes/rows freed) for the confirmation result.
- UI: action in `SessionDetail` header/`SessionControls` and the sessions list; confirmation dialog
  (reuse the `DeleteSessionModal` pattern) stating what will be lost (raw JSON, block text; kept:
  hashes and token counts). Archived badge; viewers show "Archived — content not retained" instead of
  generic purge text (distinguish from "never captured").
- Request detail states: raw/canonical body purged because archived vs capture failed vs paused.
- CLI parity: `contextspy session archive <id>` (check existing session commands in `cli.py`).

## Retention settings fate (open)

Options: (a) remove `[retention]` purge entirely (disk grows until user archives/deletes);
(b) keep it but default to `0` (off) and document as legacy; (c) replace with an optional
"auto-archive ended sessions older than N days" later. Recommend (b) for compatibility, then (c) if
users ask. Needs user decision.

## Migration

Additive column in `_migrate()`; no backfill required. Existing sessions whose content was already
purged by the old policy remain `ended` (not marked archived) — consider whether to show them as
"content expired" in the UI via existing `content_purged` flags.

## Tests

Archive allowed only when ended; idempotency (409 on already archived); shared content hash kept when
referenced by another non-archived session; block rows preserved; analysis endpoints (occurrences,
hot spots) still return data for archived sessions; startup purge behaviour per the chosen option;
active session cannot be archived; DB-lock/maintenance interaction.

## Open questions

1. Retention-setting fate (a/b/c above).
2. Should "keep content" be available for pinned/starred sessions? (small add-on; user not yet asked.)
3. Should archiving also be offered per conversation? (Probably no.)
