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

Token counts are **estimates** using tiktoken `o200k_base` encoding
(`analysis/tokenizer.py: ENCODING_NAME`).

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-5.x, GPT-4.1, GPT-4o, o-series) | ~2% — `o200k_base` is these models' native encoder |
| OpenAI (GPT-4, GPT-3.5-turbo) | ~2–5% — these predate `o200k_base` and use `cl100k_base` natively |
| Anthropic (Claude) | ~15–30% — see below |
| Ollama / llama.cpp / vLLM | ~10–20% |

When the provider reports exact token counts in the API response, those are stored
alongside the estimate and shown on the request detail page for comparison.

### Encoder choice, and the 0.3.4 switch

ContextSpy counted with `cl100k_base` up to 0.3.3 and with `o200k_base` from 0.3.4 onwards.
`cl100k_base` is native only to GPT-4 and GPT-3.5-turbo; every OpenAI model released since
GPT-4o — the whole GPT-5.x line, GPT-4.1, the o-series — uses `o200k_base`, so the old
default was an approximation for essentially all current traffic.

The switch was made for correctness, not accuracy: it changes which models the counts are
exact for, not the counts themselves. Re-encoding the captured corpus (~200 KB of real
system prompts, tool definitions, tool results and reasoning) under both encoders gives
totals within **0.0%** of each other, with no category off by more than 3.6%:

| Content kind | `cl100k_base` | `o200k_base` | Difference |
|---|---|---|---|
| Tool results | 17,228 | 17,234 | +0.0% |
| Tool definitions | 10,706 | 10,726 | +0.2% |
| System prompt | 8,228 | 8,221 | −0.1% |
| Thinking | 7,925 | 7,860 | −0.8% |
| Conversation history | 2,541 | 2,560 | +0.7% |
| **All content** | **48,953** | **48,936** | **−0.0%** |

The efficiency gap `o200k_base` is known for shows up on natural language, especially
non-English — not on the English prose, code and JSON that dominate a coding agent's
context. It does nothing for Anthropic, whose tokenizer matches neither encoder.

Every `Request` records which encoder produced its counts in the `tokenizer` column
(`tiktoken/o200k_base`, or `tiktoken/cl100k_base` for rows captured before 0.3.4). Existing
rows are **not** recounted — there is no migration, because the raw bodies needed to redo the
work are purged on the retention schedule. Sessions spanning the upgrade therefore mix both,
which given the ~0.0% difference is immaterial in aggregate but is recorded per row should it
ever matter.

### Anthropic tokenizer drift

Anthropic's tokenizer has diverged from `cl100k_base` and now produces materially more
tokens for the same text. Recent Claude requests measured against the provider's own
`usage` show ContextSpy's estimate running **roughly 13–39% low** (small sample of
`claude-haiku-4-5` turns), well outside the ~5–15% this table used to quote. The estimate
is always the low side — tiktoken undercounts, it does not overcount.

This affects the *input* categories too, not just output: every category in the context
breakdown is understated by roughly the same proportion, so the *shares* between categories
stay meaningful even when the absolute numbers are low. Where an exact number matters, use
the provider-reported figures on the request detail page.

It compounds in the `derived` reasoning case below, where the estimate is subtracted from a
provider-reported total — there the whole error is concentrated into the thinking figure
rather than spread across the response.

### Thinking / reasoning tokens

Reasoning is billed by every provider but disclosed by only some, so
`analysis/adapters/base.py: reconcile_thinking()` normalises all of them onto the same
carriers — `thinking` blocks for the text, `provider_reasoning_tokens` for the provider's
own figure — and tags each block with `attrs["token_source"]` recording how it was arrived at:

| `token_source` | When | Accuracy |
|----------------|------|----------|
| `provider` | The API reports a reasoning count (OpenAI `reasoning_tokens`) | Usually exact — but see the Codex caveat below |
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

**Codex on a ChatGPT plan — `reasoning_tokens` can describe only the summary.** On the
`chatgpt.com/backend-api/codex/responses` endpoint, the reported `reasoning_tokens` sometimes
covers just the short reasoning summary rather than the hidden reasoning that was actually
billed. Because `provider` outranks every other source, that figure is taken at face value and
the remainder is not attributed anywhere — the request's totals then fall short of
`output_tokens` with no category to account for the difference. Most turns reconcile to within
a few tokens; the failure mode is a turn with heavy hidden reasoning behind a one-line summary
(one observed example: 29 visible + 444 reported reasoning against 2,535 billed output tokens,
leaving 2,062 unaccounted). Compare **Tokens out** with the provider figure on the request
detail page to spot it.

---

## Contributing

1. Fork the repo and create a branch.
2. For backend changes to `analysis/providers.py` or `analysis/classifier.py`, add or update tests in `tests/test_providers.py` and confirm `pytest` passes.
3. For frontend changes, rebuild the UI (`make ui`) and verify in the browser with `contextspy start`.
4. Open a pull request against `main` with a description of what changed and why.

Bug reports and feature requests are tracked in [GitHub Issues](https://github.com/RimantasZ/contextspy/issues).
