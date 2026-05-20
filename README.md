# ContextSpy

A local proxy that intercepts traffic between coding agents (GitHub Copilot, Claude,
opencode, OpenAI SDK scripts, etc.) and LLM APIs — either cloud provider APIs or local
LLM servers — then analyses context composition and token usage.

## Features

- **Two proxy modes**: forward proxy (cloud APIs) and reverse proxy (local LLM servers)
- **Provider support**: OpenAI, Anthropic (Claude), Ollama, llama.cpp, vLLM
- **Agent detection**: Copilot, Claude Desktop, opencode, Cursor, and generic clients
- **Context analysis**: breaks input tokens into 8 categories:
  - System prompt, Tool definitions, Tool results, File contents,
    Conversation history, Current user message, Assistant prefill, Uncategorised
- **Token estimation** via tiktoken (`cl100k_base`)
- **Live dashboard** — real-time WebSocket updates, charts, session grouping
- **Session tracking** — manually start/end named sessions to group requests
- **SQLite storage** — all data stored locally in `~/.contextspy/`

---

## Mode 1: Cloud API Mode (forward proxy)

Use this mode when you want to intercept requests going to **cloud LLM APIs** such as
OpenAI, Anthropic, GitHub Copilot, or Azure OpenAI.

ContextSpy acts as an HTTPS man-in-the-middle proxy. It terminates TLS, inspects the
request, and re-encrypts it before forwarding to the provider. This requires installing
a local CA certificate once so your OS and tools trust ContextSpy's dynamically-signed
server certificates.

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`) — or plain `pip`
- Administrator / sudo access (for CA cert installation)
- Node.js 18+ and npm — only needed if you want to modify the frontend

### Install

**From PyPI (recommended):**

```bash
pip install contextspy
# or with uv:
uv tool install contextspy
```

**From source:**

```bash
git clone https://github.com/RimantasZ/contextspy.git
cd contextspy
uv venv
uv pip install -e .
```

**Binary releases (macOS / Linux / Windows):**

Pre-built single-file executables are attached to each [GitHub release](https://github.com/RimantasZ/contextspy/releases).
Download and extract the archive for your platform, then run `./contextspy`.

> **macOS Gatekeeper warning** — binaries downloaded from the internet are quarantined
> by macOS and may show a warning that the file "cannot be opened because it was not
> scanned for malware". Remove the quarantine flag after extracting:
>
> ```bash
> xattr -d com.apple.quarantine ./contextspy
> ```
>
> Alternatively, right-click the binary in Finder and choose **Open**, then click
> **Open** in the dialog. This is a one-time step per downloaded binary.
>
> To avoid this entirely, install via **Homebrew** (see below) — Homebrew removes the
> quarantine attribute automatically during install.

**Via Homebrew (macOS / Linux — recommended for direct downloads):**

```bash
brew install rimantas/contextspy/contextspy
```

### Build the UI (optional — only needed if you change the frontend)

The built UI is bundled with the package. Only rebuild if you modify `ui/src/`:

```bash
cd ui
npm install
npm run build   # outputs to contextspy/_web/
cd ..
```

### Step 1 — Start ContextSpy

```bash
# Windows (PowerShell)
uv run contextspy start

# macOS / Linux
uv run contextspy start
```

This starts:
- mitmproxy HTTPS forward proxy on **port 8888**
- FastAPI web dashboard on **port 5173**
- Opens http://127.0.0.1:5173 in your browser automatically

### Step 2 — Install the CA certificate

mitmproxy generates a local CA certificate on first run. You must install it into your
OS trust store so HTTPS connections through the proxy are trusted.

**Automatic install (run once):**

```bash
uv run contextspy install-cert
```

- **Windows**: runs `certutil -addstore Root ~/.mitmproxy/mitmproxy-ca.pem` (requires elevated prompt or UAC prompt)
- **macOS**: runs `security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ...` (requires sudo)
- **Linux**: copies cert to `/usr/local/share/ca-certificates/` and runs `update-ca-certificates`

You can also install from the dashboard → **Settings** → **CA Certificate** tab.

> **One-time operation.** The certificate persists in your trust store across restarts.
> You only need to re-run this if you delete `~/.mitmproxy/` or reinstall the OS.

### Step 3 — Configure your agent to use the proxy

Choose the agent you want to intercept and follow the corresponding instructions.
You can also run `uv run contextspy setup-<agent>` for a printed reminder.

#### GitHub Copilot (VS Code)

**Option A — VS Code `settings.json`** (`Ctrl+Shift+P` → "Open User Settings JSON"):

```json
{
  "http.proxy": "http://127.0.0.1:8888",
  "http.proxyStrictSSL": false
}
```

**Option B — environment variables** (set before launching VS Code):

```bash
# PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:8888"
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"

# Bash / zsh
export HTTPS_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

Run for the exact snippet:
```bash
uv run contextspy setup-copilot
```

#### Claude CLI / Claude Code

```bash
# PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:8888"
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"

# Bash / zsh
export HTTPS_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

> `NODE_EXTRA_CA_CERTS` is required because Claude CLI is an Electron/Node app and has
> its own bundled certificate store that ignores the OS trust store.
> Use `mitmproxy-ca-cert.pem` (cert-only), not `mitmproxy-ca.pem` (key+cert bundle).

Run for the exact snippet:
```bash
uv run contextspy setup-claude
```

#### opencode

```bash
# PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:8888"
$env:SSL_CERT_FILE = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"

# Bash / zsh
export HTTPS_PROXY=http://127.0.0.1:8888
export SSL_CERT_FILE=~/.mitmproxy/mitmproxy-ca-cert.pem
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

> opencode uses both the Go TLS stack (`SSL_CERT_FILE`) and Node.js components
> (`NODE_EXTRA_CA_CERTS`), so both variables are needed.

Run for the exact snippet:
```bash
uv run contextspy setup-opencode
```

#### Python / OpenAI SDK / httpx scripts

```python
import os
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8888"
```

Or set env vars before running your script:

```bash
# PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:8888"
python your_script.py

# Bash
HTTPS_PROXY=http://127.0.0.1:8888 python your_script.py
```

#### Generic (curl, httpx CLI, etc.)

```bash
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
```

### Step 4 — Use the dashboard

Open http://127.0.0.1:5173. Requests appear in real-time as your agent makes LLM calls.

- **Dashboard** — token usage totals, category breakdown chart, model distribution
- **Requests** — table of all captured requests with token counts and category bars
- **Sessions** — group requests by task; click **Start Session** and give it a name

---

## Mode 2: Local LLM Mode (reverse proxy)

Use this mode when you want to intercept requests going to **local LLM servers** such as
[llama.cpp / llama-server](https://github.com/ggerganov/llama.cpp),
[Ollama](https://ollama.com), or [vLLM](https://github.com/vllm-project/vllm).

**Why a different mode?** When client and server are both on `127.0.0.1`, operating
systems route loopback traffic directly — they bypass `HTTPS_PROXY` entirely. A forward
proxy cannot intercept this traffic. Instead, ContextSpy acts as a **reverse proxy**:
the client connects to ContextSpy's listen port; ContextSpy forwards to the real server.
No TLS, no certificate installation needed.

```
Client (opencode / script)
  base_url = http://127.0.0.1:8889/v1   ← ContextSpy listen port
      │
      ▼
ContextSpy reverse proxy (port 8889)
      │
      ▼
llama-server                            ← actual server
  127.0.0.1:8080
```

### Prerequisites

- Python 3.11+ and uv (same as cloud mode)
- A running local LLM server (llama-server, Ollama, or vLLM) — see setup sections below
- No CA certificate needed

### Step 1 — Configure `~/.contextspy/config.toml`

Add one `[[reverse_targets]]` block per local server you want to intercept. The file is
auto-created at `~/.contextspy/config.toml` on first run (or after running any
`contextspy` command).

```toml
# Example: intercept llama-server running on port 8080
[[reverse_targets]]
name        = "llama-server"
listen_port = 8889
target_url  = "http://127.0.0.1:8080"
provider    = "openai"

# Example: intercept Ollama /v1 endpoint on port 11434
[[reverse_targets]]
name        = "ollama"
listen_port = 8890
target_url  = "http://127.0.0.1:11434"
provider    = "openai"

# Example: intercept vLLM on port 8000
[[reverse_targets]]
name        = "vllm"
listen_port = 8891
target_url  = "http://127.0.0.1:8000"
provider    = "openai"
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `name` | yes | Human-readable label shown in the CLI and logs |
| `listen_port` | yes | Port ContextSpy binds on `127.0.0.1` |
| `target_url` | yes | Full base URL of the local LLM server |
| `provider` | yes | Response parser: `"openai"` for all three servers above |

All three servers implement the OpenAI-compatible `/v1/chat/completions` API, so
`provider = "openai"` is correct in all cases.

You can run `uv run contextspy setup-llamaserver` (or `-ollama`, `-vllm`) for a
ready-to-paste config snippet and client configuration instructions.

### Step 2 — Start ContextSpy in local mode

```bash
uv run contextspy start-local
```

This starts:
- One mitmproxy reverse-proxy listener per `[[reverse_targets]]` entry
- FastAPI web dashboard on **port 5173**
- Opens http://127.0.0.1:5173 in your browser automatically

If `[[reverse_targets]]` is empty or missing, the command prints a config example and
exits without starting anything.

### Step 3 — Point your client at ContextSpy

Instead of connecting to the LLM server directly, configure your client to use the
ContextSpy listen port.

#### llama-server (llama.cpp)

Default setup: llama-server on port 8080, ContextSpy on port 8889.

```bash
# Print the full setup reminder
uv run contextspy setup-llamaserver

# In your Python / openai SDK client:
from openai import OpenAI
client = OpenAI(
    base_url="http://127.0.0.1:8889/v1",   # ContextSpy port
    api_key="not-needed",
)
```

```bash
# For opencode: set the model's base URL to ContextSpy
# In your opencode config (providers section):
# base_url: http://127.0.0.1:8889/v1
```

#### Ollama

Default setup: Ollama on port 11434, ContextSpy on port 8890.

> Ollama ≥ 0.1.24 is required for the `/v1/chat/completions` OpenAI-compatible
> endpoint. Older versions only have `/api/chat`.

```bash
# Print the full setup reminder
uv run contextspy setup-ollama

# In your Python / openai SDK client:
from openai import OpenAI
client = OpenAI(
    base_url="http://127.0.0.1:8890/v1",   # ContextSpy port
    api_key="ollama",                        # Ollama ignores the key
)
```

> **Alternative for cloud mode:** If your Ollama client respects `HTTPS_PROXY`, you can
> use `contextspy start` (forward proxy mode) instead — Ollama is in the built-in
> hostname filter for `localhost:11434`. Run `uv run contextspy setup-ollama` for both
> options.

#### vLLM

Default setup: vLLM on port 8000, ContextSpy on port 8891.

```bash
# Print the full setup reminder
uv run contextspy setup-vllm

# In your Python / openai SDK client:
from openai import OpenAI
client = OpenAI(
    base_url="http://127.0.0.1:8891/v1",   # ContextSpy port
    api_key="not-needed",
)
```

### Step 4 — Use the dashboard

Same as cloud mode — open http://127.0.0.1:5173. All captured requests appear in
real-time regardless of which local server they came from.

---

## CLI reference

```
contextspy start [--proxy-port 8888] [--web-port 5173] [--no-browser]
    Start in cloud/forward-proxy mode. Opens browser on startup.

contextspy start-local [--web-port 5173] [--no-browser]
    Start in local/reverse-proxy mode. Reads [[reverse_targets]] from config.toml.

contextspy install-cert
    Install mitmproxy CA certificate into OS trust store (cloud mode only).

contextspy status
    Show proxy running state, active session, DB path.

contextspy reset-db [--yes]
    Delete ALL requests and sessions (prompts for confirmation).

contextspy db-stats
    Print database row counts (works offline).

contextspy report
    Print aggregate token stats and category breakdown table (works offline).

contextspy setup-claude        Print proxy env-var commands for Claude CLI
contextspy setup-copilot       Print proxy settings for VS Code / GitHub Copilot
contextspy setup-opencode      Print proxy env-var commands for opencode
contextspy setup-llamaserver   Print config.toml snippet and client URL for llama-server
contextspy setup-ollama        Print config.toml snippet and client URL for Ollama
contextspy setup-vllm          Print config.toml snippet and client URL for vLLM

contextspy session start <name>   Start a named capture session
contextspy session end            End the active session
contextspy session list           List all sessions
```

---

## Architecture

### Cloud mode

```
coding agent → HTTPS_PROXY → mitmproxy (port 8888)
                                  │ TLS terminate + forward
                              cloud LLM API
                                  │
                            ContextSpyAddon
                              → parse, classify, count tokens
                              → write to SQLite
                              → broadcast WebSocket
```

### Local mode

```
client (base_url=:8889) → mitmproxy reverse proxy (port 8889)
                                  │ plain HTTP forward
                            llama-server (port 8080)
                                  │
                            ContextSpyAddon (provider_override="openai")
                              → parse, classify, count tokens
                              → write to SQLite
                              → broadcast WebSocket
```

Both modes share the same FastAPI web server (port 5173), SQLite database, and dashboard.

---

## Data storage

All data is stored in `~/.contextspy/`:

| Path | Description |
|------|-------------|
| `~/.contextspy/contextspy.db` | SQLite database |
| `~/.contextspy/config.toml` | Configuration (auto-created) |

Raw request/response bodies are stored per-request and purged automatically
7 days after capture to save disk space (on next server startup).

---

## Token estimation accuracy

Token counts are **estimates** using tiktoken `cl100k_base` encoding.
Accuracy varies by provider:

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-4, GPT-4o) | ~2–5% |
| Anthropic (Claude) | ~5–15% |
| Ollama / llama.cpp / vLLM | ~10–20% |

When the provider reports exact token counts in the API response, those are
stored alongside the estimate for comparison on the request detail page.

---

## Development

### Backend

```bash
uv venv
uv pip install -e ".[dev]"
uvicorn contextspy.api.main:create_app --factory --reload --port 5173
```

### Frontend

```bash
cd ui
npm install
npm run dev   # Vite on :5174, proxies /api to :5173
```

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
