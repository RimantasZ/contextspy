# CLI Reference

```
contextspy help
```
List all available commands with a short description.

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

## Certificate commands

```
contextspy install-cert
```
Install the mitmproxy CA certificate into the OS trust store (cloud mode only).
Requires sudo on macOS/Linux, or an elevated prompt on Windows.

---

## Setup helpers

Print proxy configuration instructions for a specific tool. These are reminders only —
they don't modify any config files.

```
contextspy setup-copilot       VS Code / GitHub Copilot proxy settings
contextspy setup-claude        Claude CLI / Claude Code env vars
contextspy setup-opencode      opencode env vars
contextspy setup-codex         Codex CLI env vars (terminal tool only, not the ChatGPT desktop app)
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
contextspy session list           List all sessions with request counts
```

---

## Database / reporting commands

These commands work offline — no proxy or dashboard needs to be running.

```
contextspy status          Show proxy running state, active session, DB path, and port bindings
contextspy db-stats        Print database row counts
contextspy report          Print aggregate token stats and category breakdown table
contextspy reset-db        Delete ALL requests and sessions (prompts for confirmation)
contextspy reset-db --yes  Skip confirmation prompt
```
