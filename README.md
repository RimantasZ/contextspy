<div align="center" id="contextspytop">
<img src="docs/_static/logo_black_label.png" alt="logo" width="400" margin="10px"></img>
</div>

ContextSpy is a local proxy that sits between your coding agent and the LLM API,
recording every request and breaking down exactly how your context window is being used.

Modern AI coding agents (GitHub Copilot, Claude Code, opencode, Cursor) pack a lot into
each LLM request: system prompts, tool definitions, tool results, file contents,
conversation history. It's often unclear why a session is slow, expensive, or hitting
the context limit. ContextSpy makes the invisible visible — you see a live breakdown of
every token category for every request, across sessions, over time.

## Features

- **Two proxy modes** — forward proxy for cloud APIs (OpenAI, Anthropic, Copilot),
  reverse proxy for local LLM servers (Ollama, llama.cpp, vLLM)
- **Context breakdown** — input tokens split into 8 categories:
  system prompt, tool definitions, tool results, file contents, conversation history,
  current user message, assistant prefill, uncategorised
- **Live dashboard** — real-time charts and per-request detail with a visual block map
  of the context window
- **Session tracking** — name and group requests by task to compare usage across runs
- **SQLite storage** — all data stored locally in `~/.contextspy/`; no data leaves your machine
- **Agent detection** — Copilot, Claude Desktop/Code, opencode, Cursor, and generic clients

## Quick start

```bash


# Install
pip install contextspy
# or: uv tool install contextspy
# or: brew install rimantas/contextspy/contextspy

# Install the CA certificate (cloud mode only, one-time)
contextspy start --no-browser   # generates cert, then Ctrl+C
contextspy install-cert

# Start
contextspy start
```

Then configure your agent to route through `http://127.0.0.1:8888` and open
http://127.0.0.1:5173 in your browser.

## Documentation

- [Installation](docs/install.md) — PyPI, Homebrew, .deb, binary, CA certificate setup
- [Cloud API mode](docs/cloud-mode.md) — intercept OpenAI, Anthropic, Copilot, etc.
- [Local LLM mode](docs/local-mode.md) — intercept Ollama, llama-server, vLLM
- [Usage examples](docs/examples.md) — practical recipes and common workflows
- [CLI reference](docs/cli.md) — all commands and options
- [Development](docs/development.md) — architecture, data storage, contributing

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

