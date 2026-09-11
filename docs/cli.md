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

Show the forward-proxy state, reported proxy port, CA status, and active session. This command
requires the dashboard/API to be running.

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

Sessions group requests captured during a named time window (e.g. one task or feature).

```
contextspy session start <name>   Start a named capture session
contextspy session end            End the currently active session
contextspy session list           List session names, IDs, timestamps, and active state
```

These commands require the dashboard/API to be running.

---

## Database / reporting commands

These commands work offline — no proxy or dashboard needs to be running.

```
contextspy db-stats        Print database row counts
contextspy db-upgrade      Back up the DB and apply pending data migrations
contextspy report          Print aggregate token stats and category breakdown table
contextspy reset-db        Delete ALL requests and sessions (prompts for confirmation)
contextspy reset-db --yes  Skip confirmation (`-y` is also accepted)
```
