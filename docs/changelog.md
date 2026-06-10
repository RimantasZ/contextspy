# What's New

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
