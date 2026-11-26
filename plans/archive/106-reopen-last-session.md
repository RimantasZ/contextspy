# Reopen the last closed session (issue #106)

Status: **draft 2026-11-26, product questions decided, not reviewed, not implemented.**

## Problem

A session ended by mistake cannot be continued. `crud.end_session` sets `ended_at` and `is_active = 0`
and nothing reverses it. Starting a session with the same name creates a separate one.

The issue has a title only ("add ability to reopen last closed session"), so the scope below is
derived from that title and the code.

## Scope

Only the **last closed session** can be reopened: the inactive session with the latest `ended_at`.
It must not be archived. If it is archived, nothing is reopenable; there is no fallback to an older one.
Deleting the last closed session makes the one closed before it reopenable.

If another session is active, reopening **ends it first**, as starting a session does, and returns a
warning naming it. So after starting a new session by mistake, one action goes back to the old one.
The ended session is kept, with its requests; deleting it stays a separate user action.

Not in scope: choosing any other session to reopen, merging sessions, moving requests between
sessions, tracking several active intervals per session.

## Behaviour

- Reopening sets `ended_at = NULL`, `is_active = 1`. `started_at`, name and id do not change.
- New requests get the session's id. `next_request_seq` is kept, so numbering continues.
- Requests captured while it was closed stay without a session. So does a request that started while
  it was closed and finishes after the reopen (`proxy/addon.py` takes the session at request start).
- Duration is measured from the original start, so it includes the closed time.
- Session time spans can now **overlap**: reopening A after B was started leaves B inside A's span.
  Requests stay with the session they were captured in. Lists ordered by start time show the
  reopened A below B although A is the active one.
- Because ending B makes it the last closed session, reopening again switches back to B.
- No schema change, no migration.

## Two problems to fix along with it

### 1. Dashboard timeline would hide requests

`crud.get_sessions_summary` builds "no session" rows from windows between one session's `ended_at`
and the next one's `started_at`, and adds the trailing window only if the last session is not active.
After a reopen the last session is active again, so requests captured while it was closed would
**disappear from the timeline** (still stored, but not listed there).

Fix: bound the windows by session **starts** only: before the first start, between consecutive
starts, and after the last start. The query already selects only requests without a session, so
every one of them falls in exactly one window, also when sessions overlap. A row's displayed times still come from its first and
last request.

Side effect, intended: an unassigned request that finished while a session was running (started just
before it) is currently listed nowhere and would now appear.

### 2. Race with archive

`session_archive.archive_session_data` checks `session.is_active` *before* it registers the id in
`_in_progress`, then purges in several transactions. A reopen in between would let the purge run on
an active session.

Fix: reopen checks `_in_progress` and commits while holding `_in_progress_lock`; archive re-reads
`is_active` after registering the id. Reopen during an archive returns 409. This relies on the single
server process, as the existing guard does.

## Work items

**Backend**
- `db/crud.py`: `get_reopenable_session(db)` (the only place the rules live) and
  `reopen_session(db, session_id)`, which ends any active session and reopens the target in one
  transaction. Take the write lock before reading, so a concurrent session start cannot leave two
  active sessions. A refused reopen must not end the active session.
- `api/routers/sessions.py`: `POST /api/sessions/{id}/reopen`, returning `{ session, warning }` like
  session creation. 404 unknown id; 409 with a reason when it is not the last closed session, is
  archived or being archived, or is already active. Broadcast `session_ended` for the session it
  ended, then `session_reopened`.
- `GET /api/sessions` and `GET /api/sessions/{id}`: add `reopenable_session_id` (nullable), so the
  UI does not re-derive the rules.
- `get_sessions_summary` and archive fixes above.

**CLI**
- `contextspy session reopen`, no argument: reopens the last closed session, prints its name and the
  warning if an active session was ended, or says why there is nothing to reopen.

**UI**
- "Reopen session" button on Session Detail and on the matching row of the Sessions list, shown only
  for `reopenable_session_id`. No confirmation dialog, matching session start. When a session is
  active, the button's tooltip says it will be ended.
- `useReopenSession` hook; handle `session_reopened` in `useWebSocket.ts` like `session_started`.

**Docs**
- `SPEC.md` (API table, CLI list, session lifecycle rules), `docs/cli.md`, `docs/changelog.md`.

## Tests

- Reopen succeeds on the last closed session; a later capture gets its id and the next sequence number.
- With an active session: it is ended, the warning names it, its requests stay with it, and exactly
  one session is active. Reopening again switches back.
- Each 409 case (the active session stays active) and 404.
- Last closed session deleted, then the one closed before it is reopenable.
- Timeline: unassigned requests captured between end and reopen are still listed; existing gap rows
  for non-reopened histories are unchanged; overlapping sessions list each unassigned request once.
- Archive/reopen race, both orders.
- CLI command; button visibility and WebSocket refresh in Vitest.
- `pytest`, `cd ui && npm test`, `make ui`.

## Decisions (user, 2026-11-26)

| # | Decision |
|---|----------|
| Q1 | Reopen ends the active session automatically. |
| Q2 | Duration includes the closed time; no intervals table. |
| Q3 | Requests captured while closed are not added to the reopened session. |
