# Backend Block Analysis: Persisted Blocks + Provider Adapter Layer

## Context

Today the analysis pipeline (`providers.py` → `classifier.py`) produces only 8 aggregate token
counts per request; the block structure is discarded. The frontend (`ParsedViewer.tsx
extractBlocks()`) re-parses `raw_request_body` in TypeScript to build the block view — duplicated
provider logic that breaks once raw bodies are purged. The goal:

1. All analysis in Python; frontend renders server-provided blocks.
2. Persist blocks **including contents** (content-addressed) to enable future request-diffing.
3. Extensible data model (`attrs`/`usage_extra` JSON bags) and a provider **adapter abstraction**
   so new providers/wire formats are one new module.
4. Track thinking/reasoning tokens separately from output text.
5. Configurable retention; schema versioning with an explicit `db-upgrade` command.

Decisions confirmed with user: content-addressed content table; per-content-part granularity;
response persisted as blocks too (incl. thinking); retention configurable in config.toml with
7-day default for both raws and block contents; keep `tool_stats` as materialized summary;
purge at startup only (+ README/FAQ note); schema version stored in DB, warning on
`start`/`start-local` when data migrations pending, explicit `contextspy db-upgrade` command
(backfill happens there), or user can `reset-db`.

Cross-request evolution tracking: **no first-seen denormalization** — content-hash identity is
brittle for semantically-same-but-changed blocks (embedded timestamps etc.), so matching
strategy is deferred. Instead, requests get a `session_seq` ordinal (request # within its
session) assigned at insert; blocks inherit it via their request FK. Future diff/"show only new"
features will combine `session_seq` ordering with hash or fuzzy matching.

## 1. Domain model — `contextspy/analysis/blocks.py` (new)

```python
class BlockType(StrEnum):   # structural — fact from wire format
    SYSTEM_PROMPT, TOOL_DEFINITION, USER_MESSAGE, ASSISTANT_MESSAGE,
    TOOL_CALL, TOOL_RESULT, ASSISTANT_PREFILL, THINKING, OTHER

class Direction(StrEnum):
    INPUT, OUTPUT

@dataclass
class Block:
    position: int                 # order within direction
    message_index: int | None     # wire-format message it came from
    direction: str                # input | output
    block_type: str               # BlockType
    category: str | None          # semantic 8-category label, set by classifier (input only)
    content: str                  # normalized text ("" when provider hides it)
    content_hash: str | None      # sha256; None for hidden-content blocks (OpenAI reasoning)
    token_count: int              # tiktoken estimate; for hidden reasoning, provider-reported
    tool_name: str | None
    tool_call_id: str | None
    attrs: dict                   # extensible: cache_control, redacted flags, image refs...

@dataclass
class AnalyzedRequest:            # replaces ParsedRequest as pipeline currency
    model: str | None
    input_blocks: list[Block]
    output_blocks: list[Block]
    usage: Usage                  # below
    tool_call_map: dict[str, str]

@dataclass
class Usage:
    input_tokens: int | None      # provider-reported (Anthropic: billed+cached, as today)
    output_tokens: int | None
    reasoning_tokens: int | None  # provider-reported where available (OpenAI details)
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    extra: dict                   # any other provider usage fields, stored as JSON
```

Granularity: **one block per content part** — each `tool_result`, `text`, `tool_use`,
`thinking` part inside a message is its own block; `message_index` links siblings.
`thinking` appears in both directions (agents replay Anthropic thinking blocks in later requests).
OpenAI hidden reasoning → synthetic THINKING output block, `content=""`, `content_hash=None`,
`token_count` from `usage.*_tokens_details.reasoning_tokens`.

## 2. Adapter layer — `contextspy/analysis/adapters/` (new package)

```python
class WireFormatAdapter(ABC):
    format_id: str                       # "anthropic" | "openai_chat" | "openai_responses" | "ollama"
    endpoint_patterns: tuple[str, ...]   # e.g. ("/messages",)
    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]  # blocks + tool_call_map
    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]
    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]

REGISTRY: list[WireFormatAdapter]        # ordered; first endpoint_pattern match wins
def get_adapter(endpoint: str) -> WireFormatAdapter | None
```

- `base.py` (ABC + registry + shared `_content_to_str`-style helpers), `anthropic.py`,
  `openai_chat.py`, `openai_responses.py`, `ollama.py`. Port logic from existing
  `parse_*`/`_sse_to_*` functions — dispatch stays endpoint-based (Copilot→Claude keeps working).
- Anthropic adapter additionally parses `thinking`/`redacted_thinking` content blocks (request
  and response, incl. `thinking_delta` in SSE) and captures `cache_control` markers into `attrs`.
- `providers.py` is deleted; `tests/test_providers.py` rewritten against adapters
  (same fixtures, assertions on blocks instead of ParsedMessage).

## 3. Classifier — `analysis/classifier.py` (rework, same heuristics)

- `classify_blocks(blocks) -> None`: assigns `category` per input block using the existing
  priority waterfall + `_is_file_content` regexes (unchanged). "Last user message" logic keys
  on the last USER_MESSAGE block's `message_index`.
- `CategoryBreakdown` becomes an aggregation over blocks (sum `token_count` by category).
  ChatML overhead stays `4 × distinct message_index count + 3`.
- New output aggregates: `tokens_output_text`, `tokens_output_thinking` (sum over output blocks
  by type); `total_output` = their sum.
- `per_tool_tokens` reimplemented over blocks (definition blocks per tool; result blocks
  attributed via `tool_call_id` → `tool_call_map`, even-split fallback as today).

## 4. DB schema — `db/models.py`

New tables:

```
blocks                                   block_contents            schema_meta
├─ id INTEGER PK autoincr               ├─ hash TEXT PK           ├─ key TEXT PK ("schema_version",
├─ request_id FK requests CASCADE       ├─ content TEXT           │   "pending_data_migrations")
├─ direction TEXT                       └─ created_at DATETIME    └─ value TEXT
├─ position INTEGER
├─ message_index INTEGER NULL
├─ block_type TEXT
├─ category TEXT NULL
├─ content_hash TEXT NULL → block_contents.hash (no FK enforcement; content may be purged)
├─ token_count INTEGER
├─ tool_name TEXT NULL
├─ tool_call_id TEXT NULL
└─ attrs TEXT NULL (JSON)
Indexes: (request_id), (content_hash), (block_type)
```

`requests` gains: `tokens_output_text`, `tokens_output_thinking`, `provider_reasoning_tokens`,
`usage_extra TEXT` (JSON), and `session_seq INTEGER NULL` — the request's ordinal within its
session (1, 2, 3, …), assigned in `crud.create_request` as `MAX(session_seq)+1` for the session
(NULL when session_id is NULL; backfill migration computes it from timestamps for existing
rows). Existing `tokens_*` aggregate columns **stay** (dashboard perf) and are computed from
blocks at write time. `tool_stats` **stays**, now populated from block-derived
`per_tool_tokens`.

`crud.py` additions: `insert_blocks(db, request_id, blocks)` (content via
`INSERT OR IGNORE INTO block_contents`), `get_blocks(db, request_id)` (LEFT JOIN contents —
purged content returns hash + counts with `content=None`).

## 5. Schema versioning & migrations — `db/migrations.py` (new)

- `SCHEMA_VERSION = 2` constant; stored in `schema_meta`.
- **Structural** migrations stay automatic on startup (`create_all` + additive ALTERs, as today) —
  the app always runs. Version bumped after applying.
- **Data** migrations are explicit: registry `{2: backfill_blocks_from_raw_bodies}`. When a data
  migration is pending, `schema_meta.pending_data_migrations` is set; `start`/`start-local`
  print a prominent warning: run `contextspy db-upgrade` (re-parses every request that still has
  `raw_request_body` through the adapter pipeline and writes its blocks) or `contextspy reset-db`.
- New CLI command `db-upgrade` in `cli.py` (progress output, idempotent — skips requests that
  already have blocks). Pre-purge rows simply have no blocks.

## 6. Retention — `config.py` + `db/database.py`

- New `[retention]` section: `raw_body_days = 7`, `block_content_days = 7` (0 = keep forever).
  Added to `Settings.load` + `write_defaults` template.
- `startup_vacuum(settings)` rewritten: (a) NULL raw bodies past `raw_body_days`;
  (b) GC `block_contents` rows whose **newest** referencing request (`MAX(requests.timestamp)`
  over `blocks`) is past `block_content_days` — shared content still referenced by recent
  requests is never purged. Block rows (hashes/types/categories/counts) are never purged.
- Delete dead `crud.purge_raw_bodies`. README/FAQ note: purge runs at startup only.

## 7. Pipeline wiring — `proxy/addon.py`

`_handle_response` / `_handle_sse_response` → `get_adapter(endpoint)` →
`adapter.parse_request` + `parse_response`/`parse_sse` → `classify_blocks` → aggregates →
`crud.create_request` + `crud.insert_blocks` + `upsert_tool_stats` → WS broadcast (payload
unchanged plus new token fields).

## 8. API — `api/routers/requests.py`

- `GET /requests/{id}/blocks` → `{"session_seq": N, "blocks": [{direction, position,
  message_index, block_type, category, content, content_purged, token_count, tool_name,
  tool_call_id, attrs}, ...]}`.
- `to_dict` includes the new token columns and `session_seq`.

## 9. Frontend — `ui/src/`

- `api/client.ts` + `api/hooks.ts`: add `useRequestBlocks(id)`.
- `ParsedViewer.tsx`: **delete** `extractBlocks`, `contentToStr`; render blocks from the API
  (Overview packing + Parsed list unchanged visually; block label/category colors map from
  `block_type`). Token highlight keeps calling `/api/tokenize` with block contents on demand;
  `token_count` now comes from the server. Show "content purged" state per block.
- `RawViewer.tsx` response Text tab: use output blocks (text vs thinking distinguishable) via
  the same endpoint; `extractResponseText` removed. Raw tabs unchanged.
- `RequestDetail.tsx`: unchanged except passing request id instead of raw body into ParsedViewer;
  optionally show output text/thinking split next to Generated tokens.
- Run `make ui` after changes (built `_web/` is what `contextspy start` serves).

## Files touched

**New:** `analysis/blocks.py`, `analysis/adapters/{__init__,base,anthropic,openai_chat,openai_responses,ollama}.py`, `db/migrations.py`
**Modified:** `analysis/classifier.py`, `proxy/addon.py`, `db/{models,crud,database}.py`, `config.py`, `cli.py`, `api/routers/requests.py`, `api/websocket` payload untouched, `ui/src/components/{ParsedViewer,RawViewer}.tsx`, `ui/src/pages/RequestDetail.tsx`, `ui/src/api/{client,hooks}.ts`, `tests/test_providers.py` → adapter tests, README/FAQ retention note
**Deleted:** `analysis/providers.py` (logic moves into adapters)

## Verification

1. `pytest` — rewritten adapter tests (same wire-format fixtures: OpenAI chat, Anthropic incl.
   thinking + cache_control, Responses API incl. reasoning usage, Ollama; SSE variants) + new
   tests: per-part block splitting, classifier category assignment per block, content-addressed
   dedup (two requests sharing blocks → single `block_contents` row), retention GC keeps shared
   content, hidden-reasoning synthetic block.
2. Fresh DB path: `contextspy start` against a live agent (or replay a captured raw body through
   the addon in a test) → verify `blocks` rows, aggregates on `requests` match block sums.
3. Upgrade path: run against an existing `~/.contextspy/contextspy.db` copy → structural
   migration applies, warning printed, `contextspy db-upgrade` backfills, request detail block
   view shows pre-upgrade requests.
4. UI: `make dev-backend` + `make dev-ui` → RequestDetail Overview/Parsed tabs render from API,
   including for a request whose raw body was NULLed manually (block view still works, Raw tab
   shows purged). `make ui && contextspy start` for the packaged check.
