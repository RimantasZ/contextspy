# CLI Reference

```
contextspy help
```
List all available commands with a short description.

`contextspy --version` prints the installed package version and exits.

---

## Proxy commands

```
contextspy start [OPTIONS]
```
Start in cloud/forward-proxy mode. Intercepts HTTPS traffic to cloud LLM APIs.

| Option | Default | Description |
|--------|---------|-------------|
| `--proxy-port PORT` | 8888 | Proxy listen port |
| `--web-port PORT` | 5173 | Dashboard listen port |
| `--no-browser` | — | Don't open browser on startup |

---

```
contextspy start-local [OPTIONS]
```
Start in local/reverse-proxy mode. Reads `[[reverse_targets]]` from `config.toml`.

| Option | Default | Description |
|--------|---------|-------------|
| `--web-port PORT` | 5173 | Dashboard listen port |
| `--no-browser` | — | Don't open browser on startup |

---

```
contextspy status
```

Show the forward-proxy state, reported proxy port, CA status, whether capture is active or paused,
and the active session. This command requires the dashboard/API to be running.

---

```
contextspy pause
contextspy resume
```

Pause or resume capture. While paused, requests to known LLM providers (including WebSocket
traffic) are forwarded untouched but not recorded; each ignored request is logged to the console
as `Ignored request …: capture is paused`. The pause is independent of sessions, is held in memory
only (capture is always active after a restart), and is shared with the **Pause capture** button at
the bottom of the dashboard sidebar. Requests already in flight when you pause are still recorded.
These commands require the dashboard/API to be running.

---

## Certificate commands

```
contextspy install-cert
```
Install the mitmproxy CA certificate into the OS trust store (cloud mode only).
Requires sudo on macOS/Linux, or an elevated prompt on Windows.

`contextspy inject-cert` appends that CA to the active Python `certifi` bundle for httpx/OpenAI
SDK environments that do not honor the OS trust store. It changes the selected bundle in place
and may need to be repeated after a `certifi` upgrade.

---

## Run a tool through the proxy

```
contextspy run [--proxy-port PORT] <tool> [ARGS...]
```

Launch a command with HTTP/HTTPS proxy variables set. Known tools receive the relevant CA
environment variables; unknown commands receive the base proxy variables. ContextSpy options must
come before the tool name. The dashboard must already be running, and tools with certificate
injectors require the mitmproxy CA file.

---

## Setup helpers

Print proxy configuration instructions for a specific tool. These are reminders only —
they don't modify any config files.

```
contextspy setup-copilot       VS Code / GitHub Copilot proxy settings
contextspy setup-claude        Claude CLI / Claude Code env vars
contextspy setup-opencode      opencode env vars
contextspy setup-codex         Codex CLI env vars (terminal tool only, not the ChatGPT desktop app)
contextspy setup-python        Python/OpenAI SDK/httpx certificate and proxy options
contextspy setup-llamaserver   config.toml snippet + client URL for llama-server
contextspy setup-ollama        config.toml snippet + client URL for Ollama
contextspy setup-vllm          config.toml snippet + client URL for vLLM
```

---

## Session commands

Sessions group requests observed during a named time window (e.g. one task or feature). A session
can contain several independent or forked conversations. Session request numbers are stable
recording labels, not proof that requests form one conversation.

```
contextspy session start <name>   Start a named session
contextspy session end            End the currently active session
contextspy session list           List session names, IDs, timestamps, and status (active / ended / archived)
contextspy session archive <id>   Remove an ended session's raw payloads and block text (one-way)
```

These commands require the dashboard/API to be running.

**Archiving.** `contextspy session archive <id-or-unique-prefix>` (add `--yes` to skip the prompt) works on an *ended*
session; end it first (`contextspy session end`). It removes the raw request/response payloads and the stored text of the
blocks that no other session needs, and **cannot be undone**: only a database backup made earlier (`contextspy db-backup`)
still contains them. Kept: token counts, block structure and categories, tool/source labels, JSON locations and the
conversation and lineage analysis. It prints how much was removed and, when the database is in incremental auto-vacuum
mode, how much disk space was returned; otherwise it tells you to run `contextspy db-compact`. Large sessions can take
several seconds (the command waits up to five minutes). Archiving again repeats the cleanup for anything captured since.
If you may continue one of the session's conversations later, do not archive it: a continuation whose earlier request was
archived is recorded with partial context.

---

## Database / reporting commands

These commands work offline — no proxy or dashboard needs to be running.

```
contextspy db-stats        Print database row counts
contextspy db-backup       Create an on-demand SQLite backup
contextspy db-compact      Shrink the database file (offline); add --backup / --yes
contextspy db-restore FILE Restore FILE, preserving the current DB for rollback
contextspy db-upgrade      Back up the DB and apply pending data migrations
contextspy report          Print aggregate token stats and category breakdown table
contextspy reset-db        Delete ALL requests and sessions (prompts for confirmation)
contextspy reset-db --yes  Skip confirmation (`-y` is also accepted)
```

`db-backup` can run while ContextSpy is capturing; it uses SQLite's online backup API and
publishes the file only after copying and validating it. The default name is
`contextspy_backup_v7_2026-09-30-181504Z.back` (the version and UTC timestamp vary).
Large databases need at least one database-sized copy of free disk space, and a live backup
may temporarily increase disk I/O. `contextspy status` lists manual, migration, and
pre-restore backups even when the web server is offline.

Stop the ContextSpy backend before `db-upgrade`. It creates a consistent SQLite backup,
including committed WAL content, before changing derived data. Migration names retain the
`contextspy_backup_v6_to_v7_2026-09-30-1445.back` form. Leave at least the current database
size available for that backup, plus room for newly materialized canonical requests and blocks.
The Anthropic thread backfill reports retained rows reanalyzed and partial/opaque results;
payloads already removed (archived or purged) cannot be recovered.

To restore, stop all ContextSpy processes and other programs using the database. Preview with
`contextspy db-restore BACKUP.back --dry-run`, then run `contextspy db-restore BACKUP.back` and
confirm (or pass `--yes` for noninteractive use). A bare filename is resolved in the database
directory; an explicit path also works. Restore validates the backup, stages a complete copy,
and retains the former database as `contextspy_backup_v7_pre_restore_...back` before switching
files. The source backup is not consumed. Restore replaces the database; it does not merge in
newer requests. If the restored backup has pending data migrations, run `contextspy db-upgrade`
before starting ContextSpy. A pre-WAL backup can be restored; startup will enable WAL again.

### Shrinking the database file (`db-compact`)

Deleting request bodies or block contents (session archive, or the opt-in time-based purge) does not make
the `.db` file smaller: SQLite keeps the freed pages inside the file for reuse. A database that has been
purged for a while can therefore be mostly empty space (`contextspy db-stats` shows the file size, the free
space inside it and the auto-vacuum mode). Stop ContextSpy and run:

```
contextspy db-compact            # asks for confirmation
contextspy db-compact --yes      # no prompt
contextspy db-compact --backup   # first writes contextspy_backup_vN_pre_compact_<UTC>.back
```

It rebuilds the file without the free pages (`VACUUM`) and switches the database to *incremental*
auto-vacuum, so space freed later can be returned without another full rebuild. New databases start in that mode.
It refuses to run while ContextSpy or another maintenance command is using the database, and checks beforehand that
the disk has room: the rebuild needs about the live data size again (in SQLite's temporary directory, `SQLITE_TMPDIR`
if you set it, and on the database volume), plus a full copy of the file when `--backup` is used. `VACUUM` is atomic:
if it is interrupted the original database is unchanged. As a rough guide, a 6.6 GB file with 65% free space took
about 12 seconds to compact on an SSD.

Existing backups are not affected, and a pre-compaction backup is listed and restorable like any other. A restored backup
comes back exactly as it was backed up (not compacted, with its old auto-vacuum mode); run `db-compact` again to shrink it.
Backups copy every page, so compacting first also makes later backups smaller.

ContextSpy enables SQLite WAL mode on startup for file-backed databases. Stop all ContextSpy
processes before an offline copy or restore; a live `.db` file alone may omit committed data in
its `-wal` sidecar. Do not delete `-wal` or `-shm` files while the database is in use. For a live
backup, use SQLite's online backup API instead of copying the main file.
