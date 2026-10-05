# What's New

## Unreleased

### Database

- New **`contextspy db-compact`** shrinks the database file. Deleting request bodies or block contents (the
  retention purge) never made the file smaller, so a purged database could be mostly empty space (a 6.6 GB file
  with 65% free space compacted to 2.4 GB in about 12 seconds on an SSD). Stop ContextSpy and run it; `--backup` writes
  a restorable `..._pre_compact_...back` snapshot first, `--yes` skips the prompt. It checks free disk space before
  starting and leaves the original untouched if interrupted. It also switches the database to incremental
  auto-vacuum, so space freed later can be returned without another rebuild; **new databases start that way**.
  Existing backups are unaffected and still restore; a restored backup comes back as it was and can be compacted again.
- `contextspy db-stats` now shows the file size, the free space inside it and the auto-vacuum mode, and suggests
  `db-compact` when a lot of space is reclaimable.

### Analysis

- Every request now gets an inferred **purpose**: `user_turn` (the last message is the user's),
  `tool_continuation` (the last message carries tool results, with the tool names), `compaction`
  (the request ends in an explicit provider compaction trigger) or `unknown`. The request detail
  header shows it with a one-line summary such as "results from Read, Grep → calls Edit", and
  `GET /api/requests` accepts `?purpose=`. Trailing system/developer instruction messages and
  reasoning items are not treated as the last turn.
- Every block now records a **source** (`tool:Read`, `mcp:github/create_issue`, `bash:git`,
  `exec:rg`, `user`, `system`, ...) and an **activity** derived from it (read, search, edit, vcs,
  test, command, web, orchestration, mcp, other). Shell tools are reduced to the program name
  only; arguments are never stored. The block inspector shows both.
- Every block records **where in the request or response JSON it came from** (`messages[3].content[1]`),
  for all four wire formats, and the block inspector shows it. Blocks whose retained documents no
  longer match what was analysed keep no location rather than a guessed one.
- The block inspector has a **Present in** section: in how many requests of the conversation (or the whole
  session, via a toggle) the selected block's content occurs, its tokens per occurrence and in total, first
  and last request, whether it is still in the latest request, and the runs of requests it appears in
  (expand a run to list them). Clicking a request opens it with that block selected. Totals are visible-block
  tokens, not provider-billed tokens; blocks with hidden or empty content cannot be matched across requests.
  Request detail now keeps the selected block in the URL (`?block=<id>`), so a selection survives a refresh.
  On long sessions the first conversation-scope lookup can take a few seconds.
- Sessions gained an `archived_at` column. Nothing sets it yet.
- **Run `contextspy db-upgrade`** (schema v9) to fill these in for existing data. It makes a backup
  first, prints progress, and took about three minutes on a database with ~7,000 requests and
  1.4 million blocks. Source keys for tool calls whose text was already purged fall back to the
  tool name; locations are only recoverable where the canonical request/response was retained.

### Request detail

- Added a **Show** control next to **Size** in the block toolbar: **All**, **New only** (only
  blocks that were not in the previous request) and **Highlight new** (blocks already present in
  the previous request are drawn at 50% opacity). The previous request is the lineage parent, or
  the previous request in the same conversation when no parent was established. It applies to the
  Request direction only, and the choice survives Parent/Child navigation but resets when you
  leave the page.
- The page title is now the request's conversation ID, such as `Request #C1-33` or
  `Request #AUX-34`, matching the conversation request cards. Parent and child buttons show the
  same IDs.
- When no direct parent or child was established, **Previous in conversation** / **Next in
  conversation** buttons let you step through the conversation anyway. They carry a warning icon
  because a neighbour is not necessarily the request that was actually continued; when a real
  parent or child exists the neighbour is shown as an extra button when it is a different request.
- The block inspector shows a **Per-request header** row for system prompts that carried one.

### Capture

- Claude Code's `x-anthropic-billing-header: …` line, which changes on every request, no longer
  makes the system prompt look like a new block each time. The header is kept in the block's
  attributes, the block's identity and stored text use the stable remainder, and token counts
  still describe what was sent. **Run `contextspy db-upgrade`** (schema v8) to apply this to
  existing data; blocks whose content was already purged cannot be re-keyed.

### Conversations

- Compact request cards now show the top-right lineage icon tooltip on hover and focus.

## v0.5.3

### Capture

- Added support for Claude's `thread:continue` mode: short Anthropic `thread` requests now
  reconstruct the full retained history into canonical request JSON and blocks instead of showing
  a misleadingly tiny context. Missing or provider-managed history is labelled partial/opaque.
  Run `contextspy db-upgrade` to apply this to existing rows.
- Added **Pause capture** / **Resume capture** (sidebar button, `contextspy pause` / `resume`).

## v0.5.2

### Conversations

- Added a compact conversation view alongside the detailed one, with 24h time formatting and
  tooltips, and fixed block change detection to account for encrypted content.

## v0.5.1

### Conversations

- Reworked parent-request detection: exact provider links now always win, a known agent change
  raises the bar for an inferred edge, and a later exact successor can retrospectively re-score
  an earlier close call within a small bounded look-ahead.
- Unclassified or one-off requests now land in a new **Auxiliary requests** block instead of a
  catch-all conversation row, and are only promoted into a confirmed conversation once a coherent
  chain plus independent evidence (a different agent, a corroborated stream hint, or a long
  sustained chain) supports it.

## v0.5.0

### Conversations

- Added a Conversations view on Session Detail that reconstructs roots, continuations, forks, and
  overlapping invocations, with occurrence-aware parent/child context diffs linking back to the
  underlying requests.
- Session request numbers are now allocated atomically, and new HTTP/WebSocket invocations retain
  their observed start time and originating session even when completion order differs.
- The UI now calls the recording window a session and each linked path a conversation; existing
  database, API, route, and CLI names are unchanged.

## v0.4.1

### Fixes & improvements

- Dependency lockfile update only; no functional or UI changes.

## v0.4.0

### UI redesign

- Reworked the application shell around semantic light/dark themes, responsive navigation, and a
  new Request/Response workbench (Compact, Proportional, and Raw views) with block-type filters,
  content search, and a persistent inspector showing token/position/message metadata and jumpable
  relationships.
- Replaced tool-definition/result donuts with a stable-colour treemap, simplified request lists
  into responsive tables/cards, and refreshed Overview, Sessions, Session Detail, and Settings
  onto the same visual system.
- Added Vitest/React Testing Library coverage across the redesigned components.

## v0.3.5

### Fixes & improvements
- Removed the "opaque context" warning block from the request detail page.
- The Session Detail page's requests table no longer repeats a redundant Session column.
- **Complete normalized stream capture** — SSE, Ollama NDJSON, and registered WebSocket responses
  are reconstructed into canonical provider JSON before analysis. The request detail now exposes
  both that JSON and the ordered normalized event/frame log, retaining unknown fields, errors,
  and non-JSON application data instead of storing a response synthesized from parsed text.
- Reconstruction, persistence, and block analysis now have separate failure boundaries. Failed or
  incomplete upstream calls remain inspectable, with capture status metadata, and stream event
  logs are purged alongside request/response bodies by the existing retention policy.
- **Database backups before migration** (`#33`) — `contextspy db-upgrade` now backs up the
  database before applying a data migration, and `contextspy status` lists previous backups.
- **Nested tool call parsing fixed** — Anthropic requests with tool calls nested inside other
  content no longer lose or miscount those blocks.
- **GPT tool classification fixed** — namespaced tool names in OpenAI Responses API requests are
  now unwrapped before classification, so those tools are no longer miscategorized.
- Fixed a session reporting mismatch on the Requests and Session Detail pages (`#35`).
- Fixed a mitmproxy race condition when multiple local reverse proxies start at once (`#29`).
- Fixed `start-local` provider detection.
- Blocks now track when their content first appeared in a session (dedup-aware "first seen"),
  surfaced in the request detail view.
- Moved remaining request-parsing logic from the UI into the Python backend, per the
  [analysis-in-Python policy](../CLAUDE.md).

## v0.3.4

### Fixes & improvements
- **Token counts now use the `o200k_base` encoder** — `cl100k_base` is native only to GPT-4 and
  GPT-3.5-turbo, while every OpenAI model since GPT-4o (the GPT-5.x line, GPT-4.1, the o-series)
  uses `o200k_base`. The two encoders agree to within ~0.0% on agent traffic, so your numbers do
  not shift; what changes is that the OpenAI counts are now exact by construction rather than by
  coincidence. Existing rows are not recounted, and each `Request` records which encoder produced
  it in the `tokenizer` column. See
  [encoder choice](development.md#encoder-choice-and-the-034-switch).
- **Documented the Anthropic tokenizer drift** — Claude's tokenizer has diverged from tiktoken's,
  and ContextSpy's estimates now run ~15–30% low against Anthropic's reported `usage` (measured
  13–39%). The error bands in the FAQ and developer docs have been corrected, along with the note
  that it lands entirely on the thinking figure when a reasoning count has to be derived.
- **Session PDF export splits generated tokens** — the Requests table now shows *Output*,
  *Thinking* and *Total out* as separate columns when a session contains reasoning, adds a totals
  row, and states when the 500-row cap has truncated the list relative to the Summary section.

## v0.3.3

### New features
- **Thinking / reasoning token tracking** (`#18`) — reasoning tokens are now captured across
  every supported provider and reported as their own slice of generated output. The request
  detail page gains a **Thinking** tab showing the reasoning text when the provider returns it,
  and the token count when it does not. Generated-token totals now break down as
  *output · thinking* on the request detail, session detail and overview pages, and in the
  session PDF export.
- **Reasoning text from OpenAI-compatible backends** — `reasoning_content` / `reasoning` fields
  (DeepSeek, vLLM, llama-server) and Ollama's `thinking` field are now recorded as thinking
  blocks instead of being dropped.

### Fixes & improvements
- **Anthropic thinking tokens no longer read as zero** — current Claude models default to
  `thinking.display: "omitted"`, which returns a thinking block with no text, and the Messages
  API never breaks reasoning out of `output_tokens`. ContextSpy now derives the count from the
  part of `output_tokens` the visible response does not account for, so reasoning stops
  appearing free. Add `"showThinkingSummaries": true` to `~/.claude/settings.json` to capture
  the reasoning text as well — see the
  [FAQ](faq.md#a-thinking-block-shows-a-token-count-but-no-reasoning-text).
- **OpenAI Responses API** — a reasoning item with an empty summary no longer swallows the
  reported `reasoning_tokens`, which previously made hidden reasoning read as zero.
- Thinking-token reconciliation is now shared by all four wire-format adapters
  (`analysis/adapters/base.py`) rather than reimplemented in each, and every thinking block
  records how its count was obtained — reported by the provider, estimated from the returned
  text, or derived — surfaced in the Thinking tab and documented under
  [token estimation accuracy](development.md#token-estimation-accuracy).
- `contextspy setup-claude` now prints the `showThinkingSummaries` opt-in.

> Counts are computed at capture time, so requests recorded before this version keep the
> thinking totals they were stored with; the breakdown applies to traffic captured from v0.3.3
> onward. No database migration is required.

---

## v0.3.2

### New features
- **Native WebSocket capture** (`#17`) — Codex CLI's ChatGPT-plan auth uses a WebSocket transport
  for its private `chatgpt.com/backend-api/codex/responses` endpoint; ContextSpy now inspects it
  directly instead of requiring the `chatgpt_http` config.toml workaround (see
  `contextspy setup-codex`). WS turns are recorded with a new `transport` column and show a
  **WS** badge in the dashboard. Adding a new WS-speaking provider is a one-module addition
  (`proxy/ws_protocols/`), mirroring how wire-format adapters work for HTTP.
- Codex agent detection now also covers the WebSocket transport (via the `originator` header),
  alongside the existing User-Agent-based detection over HTTP.

---

## v0.3.1

### Fixes & improvements
- **Agent detection fixed** (`#20`) — Claude Code and Codex CLI are now correctly reported as
  their own agents instead of falling through to `unknown`.
- **Logging improvements** (`#21`) — more debug-level visibility into request routing and
  response handling to make provider/agent detection issues easier to diagnose.

## v0.3.0

### New features
- **Block linking** — tool calls link to their definitions, tool results link to their calls and
  definitions, and user/assistant messages link to the previous conversational turn (skipping
  tool-only turns). Shown as jump links in the request breakdown view.
- **Context analysis moved to the backend** — requests/responses are decomposed into structured
  blocks (system prompt, tool definitions, tool calls/results, text, thinking) in Python and
  persisted, replacing client-side JSON parsing for the breakdown views.
- **New provider adapter layer** (`analysis/adapters/`) replaces `analysis/providers.py` —
  Anthropic, OpenAI Chat Completions, OpenAI Responses, Ollama.


### Fixes & improvements
- `contextspy start`/`start-local` now refuse to start if a DB schema upgrade needs a data
  backfill (run `contextspy db-upgrade` or `contextspy reset-db` first).
- Breakdown view shows tokens before labels.
- Font fix in the breakdown view.
- Expanded [Troubleshooting](install.md#troubleshooting) section.
- README fixes.

---

## v0.2.1

### New features
- **`contextspy setup-python` / `contextspy inject-cert`** — new commands for routing Python
  scripts (OpenAI SDK, httpx) through the proxy, including a one-shot fix for the case where
  httpx/the OpenAI SDK ignores `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` because it verifies against
  certifi's bundled CA store directly.
- **Time to first token (TTFT)** — requests now record and display TTFT for streaming responses
  when the provider makes it measurable, alongside total duration.
- **Session duration tracking** — sessions now track active duration, surfaced in the session
  detail view and included in the PDF report.
- **Sortable tool breakdown table** — the per-tool token breakdown table can now be sorted by
  column.

---

## v0.2.0

### Documentation
- Expanded README and FAQ with clearer setup and usage guidance.
- Added a cloud-mode setup walkthrough (`docs/cloud-mode.md`) and expanded the install guide.
- Layout/formatting fixes to the README.

No functional or code changes in this release — documentation only.

---

## v0.1.11

### Fixes & improvements
- **Certificate handling — no more silent failures** — `contextspy start` now validates the CA
  key on every launch and exits with a clear error if it is missing or corrupted, rather than
  starting the proxy and silently dropping TLS connections.
- **`contextspy run` aborts early** — if the CA cert file is missing when launching a tool,
  the command now exits with an actionable error instead of a yellow warning.
- **Auto-fix sudo ownership** — when `contextspy install-cert` is run with `sudo`, the cert
  files in `~/.mitmproxy/` are automatically chowned back to the real user so that subsequent
  non-root runs can read them.
- **Install guide rewritten** — clearer step-by-step flow and a new
  [Troubleshooting](install.md#troubleshooting) section covering the most common cert and proxy
  startup errors.

---

## v0.1.10

### Fixes
- **Certificate key validation** — `generate_cert()` now reads and parses the existing private
  key on startup; a corrupted or unreadable key triggers automatic regeneration instead of being
  silently ignored.
- **Root-owned file detection** — if cert files are owned by root (from a previous `sudo` run),
  the error message now includes the exact `chown` command to fix ownership.

---

## v0.1.9

### Fixes
- **mitmproxy error logging** — internal mitmproxy log messages are now forwarded to
  ContextSpy's own logger, making TLS and connection errors visible in the terminal output
  instead of disappearing silently.

---

## v0.1.8

### Fixes
- **Silent TLS drop fixed** — `cert_exists()` now checks for both the CA certificate *and* its
  private key. Previously, if the key was missing while the cert file was present, the proxy
  would start but silently fail all HTTPS interceptions.
- **Windows Defender notice** — install guide now documents that Windows Defender or antivirus
  software may flag the release binary (because it bundles mitmproxy), with a link to the PyPI
  install as an alternative.

---

## v0.1.7

### New features
- **`contextspy run <tool>`** — wraps any command with the proxy env vars pre-set so you don't
  have to set `HTTPS_PROXY` / cert variables manually before each session.  Known tools
  (`claude`, `code`, `cursor`, `opencode`) get the right cert variable injected automatically.
  On Windows, Electron-based tools (`code`, `cursor`) are routed via a PAC file so the Node.js
  extension host picks up the proxy correctly.
  ```
  contextspy run claude .
  contextspy run code /path/to/project
  contextspy run opencode
  ```
- **`contextspy --version`** — prints the installed package version and exits.

### Fixes & improvements
- Agent setup page updated with PowerShell variants for all cloud agents, lowercase `no_proxy`
  for bash, and the opencode `config.json` proxy option.

---

## v0.1.6

### Fixes
- **opencode cloud API** — fixed request parsing for the opencode free/cloud API endpoint, which
  uses a different host than the standard Anthropic API.
- **Timestamp timezone** — request timestamps are now stored and displayed in the correct local
  timezone.

---

## v0.1.5

### UI improvements
- **Request detail panel** — added a toggle to collapse/expand the raw request panel, giving
  more room to the token breakdown view.
- **Request page layout** — improved spacing and visual hierarchy across the request list and
  detail pages.
- **Reload fix** — resolved a bug where navigating back to the requests list after viewing a
  detail could cause a stale render.

### Fixes
- Fixed an issue where `contextspy start-local` could fail silently if the reverse proxy port
  was already bound.
- Simplified the CA certificate installation flow — fewer manual steps on macOS and Linux.

---

## v0.1.4

### New features
- **Inline context composition bar** — the request list table now shows a miniature token
  category bar for each request so you can spot context patterns at a glance without opening
  the detail view.
- **Sorting improvements** — request list columns sort more reliably; default sort is newest
  first.
- **Updated token category labels** — category names in the UI are now clearer and consistent
  with the documentation.

### Fixes
- Fixed web app static-file path resolution on Linux (`#3`).
- Fixed CA certificate installation on systems that required `sudo`.
- Added initial project documentation under `docs/`.

---

## v0.1.3

### Fixes
- **Linux `.deb` packages** — added Debian package builds to the release workflow.
- Fixed broken release artifacts from v0.1.2.

---

## v0.1.2

### Fixes
- **OpenAI token counting** — corrected an off-by-one error in the token count for OpenAI
  chat completion requests.
- Release binary workflow fixes for cross-platform builds.

---

## v0.1.1

### New features
- **Homebrew tap** — ContextSpy can now be installed via `brew install`.

### Fixes
- **GitHub Copilot provider** — fixed request parsing for Copilot's API endpoint, including
  provider detection and token breakdown.  Test suite added for provider parsing.
- **tiktoken initialisation** — fixed a crash on first run when `HTTPS_PROXY` was already set
  in the environment, causing tiktoken's model download to fail.

---

## v0.1.0

Initial release.
