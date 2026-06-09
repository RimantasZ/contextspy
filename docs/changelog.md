# What's New

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
