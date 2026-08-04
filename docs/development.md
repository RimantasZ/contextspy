# Development

## Backend

```bash
git clone https://github.com/RimantasZ/contextspy.git
cd contextspy
uv venv
uv pip install -e ".[dev]"
uvicorn contextspy.api.main:create_app --factory --reload --port 5173
```

## Tests

Tests live in `tests/test_providers.py` and cover provider request-parsing. Run them with:

```bash
pytest
# or a single test:
pytest tests/test_providers.py::test_name
```

When you modify `analysis/providers.py` or `analysis/classifier.py`, always run pytest before committing.

## Frontend

```bash
cd ui
npm install
npm run dev   # Vite dev server on :5174, proxies /api and /ws to :5173
```

The built UI is embedded in the Python package at `contextspy/_web/`. Rebuild after
changing anything under `ui/src/`:

```bash
cd ui && npm run build   # outputs to contextspy/_web/
```

---

## Architecture

### Cloud mode

```
coding agent → HTTPS_PROXY → mitmproxy (port 8888)
                                  │
                            ContextSpyAddon (intercepts here)
                              → parse request body
                              → classify tokens into 8 categories
                              → write to SQLite
                              → broadcast via WebSocket
                                  │ TLS terminate + forward
                              cloud LLM API
```

### Local mode

```
client (base_url=:8889) → mitmproxy reverse proxy (port 8889)
                                  │ plain HTTP forward
                            llama-server / Ollama / vLLM (port 8080…)
                                  │
                            ContextSpyAddon (provider_override="openai")
                              → parse, classify, count tokens
                              → write to SQLite
                              → broadcast via WebSocket
```

Both modes share the same FastAPI web server (port 5173), SQLite database, and dashboard.

---

## Data storage

All data is stored in `~/.contextspy/`:

| Path | Description |
|------|-------------|
| `~/.contextspy/contextspy.db` | SQLite database — all requests and sessions |
| `~/.contextspy/config.toml` | Configuration file (auto-created on first run) |

Raw request/response bodies, plus the content-addressed `block_contents` table (see below), are
purged automatically 7 days after capture by default to limit disk usage — configurable via
`[retention]` in `config.toml` (`raw_body_days`, `block_content_days`; `0` disables purging).
Purging only runs once, at server startup — there is no background timer, so a `contextspy`
process left running for many days won't purge again until restarted.

### Blocks

Every request/response is also decomposed into `blocks` — one row per content part (system
prompt, tool definition, a single tool call or tool result, a text or thinking segment, ...).
Each block's semantic `category` (one of the 8 breakdown categories) and structural `block_type`
are kept forever; only the block's `content` (in `block_contents`, deduplicated by content hash
across requests) is subject to the retention window above.

---

## Token estimation accuracy

Token counts are **estimates** using tiktoken `cl100k_base` encoding.

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-4, GPT-4o) | ~2–5% |
| Anthropic (Claude) | ~5–15% |
| Ollama / llama.cpp / vLLM | ~10–20% |

When the provider reports exact token counts in the API response, those are stored
alongside the estimate and shown on the request detail page for comparison.

### Thinking / reasoning tokens

Reasoning is billed by every provider but disclosed by only some, so
`analysis/adapters/base.py: reconcile_thinking()` normalises all of them onto the same
carriers — `thinking` blocks for the text, `provider_reasoning_tokens` for the provider's
own figure — and tags each block with `attrs["token_source"]` recording how it was arrived at:

| `token_source` | When | Accuracy |
|----------------|------|----------|
| `provider` | The API reports a reasoning count (OpenAI `reasoning_tokens`) | Exact — it is what was billed |
| `estimated` | No count, but the text came back (Anthropic `display: "summarized"`, Ollama `thinking`, DeepSeek/vLLM `reasoning_content`) | Same band as the table above |
| `derived` | Neither count nor text (Anthropic `display: "omitted"` — the default on current Claude models — and `redacted_thinking`) | Residual of `output_tokens` minus the estimated visible output |
| `unknown` | Nothing to go on (no count, no text, no `output_tokens`) | Reported as 0 |

The `derived` case matters most in practice: Anthropic's Messages API bills thinking inside
`output_tokens` and never breaks it out under any `thinking.display` setting, so subtraction is
the only signal available. Because the visible side of that subtraction is a tiktoken estimate
against a different tokenizer, **all of its error lands on the thinking figure** — and since
tiktoken tends to undercount Claude's tokenizer, `derived` thinking skews high.

Getting the reasoning *text* captured moves a request off `derived` and onto the more accurate
`estimated` path. For Claude Code that means `"showThinkingSummaries": true` in
`~/.claude/settings.json`; other agents expose it as a `thinking.display: "summarized"` request
parameter. Either way ContextSpy only records what the provider chose to send.

---

## Contributing

1. Fork the repo and create a branch.
2. For backend changes to `analysis/providers.py` or `analysis/classifier.py`, add or update tests in `tests/test_providers.py` and confirm `pytest` passes.
3. For frontend changes, rebuild the UI (`make ui`) and verify in the browser with `contextspy start`.
4. Open a pull request against `main` with a description of what changed and why.

Bug reports and feature requests are tracked in [GitHub Issues](https://github.com/RimantasZ/contextspy/issues).
