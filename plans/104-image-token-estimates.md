# Image token estimates (issue #104)

Status: **implemented 2026-11-25, uncommitted, not released**. See *Implementation status* at the end.

## Problem

Observed on request `802de6e1-8c92-4833-9b1e-852ae4bf958c` (Claude Code, `claude-sonnet-5-5`, one
pasted 2000×1224 PNG):

- The image part becomes a `BlockType.OTHER` block with content `[image]`, 3 tokens (the marker text),
  category `conversation_history` although it is part of the current user message.
- Every image has the same content, so the same `content_hash`. Occurrences, first-seen, context diff
  and hot spots cannot tell two images apart.
- Images inside tool results (screenshots, reading an image file) are also reduced to the marker.
- Ollama's `messages[].images` array is not read at all.

By the Anthropic formula below this image is 72 × 44 = **3,168 tokens**.

## Provider rules (docs read 2026-11-25)

Anthropic ([vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)):
`ceil(w/28) × ceil(h/28)` after downscaling to fit both limits.

| Tier | Models | Max long edge | Max tokens |
|---|---|---|---|
| High resolution | Claude 4.7 and later | 2576 px | 4784 |
| Standard | all others | 1568 px | 1568 |

OpenAI ([images and vision](https://developers.openai.com/api/docs/guides/images-vision)), two methods:

- Patch (newer models): `ceil(ceil(w/32) × ceil(h/32) × multiplier)` after fitting the dimension limit
  and patch budget of the model and `detail` level. Multiplier 1.2 for GPT-5.2 and the 5.4, 5.5, 5.6
  and 6 families; limits differ per family.
- Tile (gpt-4o, gpt-4.1, gpt-5, gpt-5.1, o-series): fit in 2048², shortest side down to 768, count
  512 px tiles; `base + tiles × per_tile` (85 + 170 for gpt-4o, 70 + 140 for gpt-5.1). `low` is base only.

GitHub Copilot: bills tokens as AI credits. No separate rule for image *input* was found (web search
only, not confirmed on a GitHub page). Assumption: apply the rule of the underlying model. Whether
Copilot resizes images itself shows up in the captured bytes only if the client does it.

### Codex

Read from `openai/codex` main (`utils/image/src/lib.rs`, `protocol/src/models.rs`,
`core/src/context_manager/history.rs`; the last two only partly verbatim):

- Resizes before upload (high: 2048 px, 2500 patches of 32 px; original: 6000 px, 10000), so the
  captured bytes are already the resized image. The model's own limits still apply.
- Images arrive as `input_image` in messages, `function_call_output` and `custom_tool_call_output`,
  and as `image_generation_call.result`. `McpToolCallOutput` and `ToolSearchOutput` variants also
  exist; their image handling was not checked.
- Its own context accounting is a byte heuristic (flat figure unless `detail: original`), not the
  billing formula. We do not copy it.
- Not verified: which `detail` each input path sends (we read it from the wire), and whether ChatGPT
  subscription credits follow the API image formula.

**Existing bug:** `image_generation_call` falls into the catch-all branch of
`openai_responses.py`, which tokenizes the base64 `result` as text, on input and output. A synthetic
100,000-character result counts as 50,026 tokens. Fix it in this change.

### Self-hosted models (vLLM, llama.cpp, Ollama)

There is no per-server rule: the count depends on the model's vision processor and server settings,
neither of which is in the request.

- Gemma 3: fixed 256 tokens per image (model card).
- Qwen-VL: depends on resolution, min/max pixels and patch size (28 px for 2.5, 32 px for 3 is an
  inference from the config defaults, not confirmed).
- llama.cpp: `--image-min-tokens` / `--image-max-tokens` change the count. Its `prompt_tokens`
  includes image tokens (`tools/server`, local checkout `ae6a7ad2c`), and it has `input_tokens` /
  `count_tokens` endpoints (image handling there not checked).
- vLLM and Ollama: the docs read do not say whether reported prompt tokens include images.

The proxy will not call count endpoints: it only observes traffic.

## Design

1. New `analysis/media.py`:
   - Read width/height from PNG, JPEG, GIF and WebP headers in pure Python (no new dependency).
     Unreadable or truncated data never fails capture; it counts as `unavailable`.
   - `estimate_image_tokens(model, width, height, detail)`, rule chosen by **model name**, not wire
     format. Must accept gateway spellings (`claude-sonnet-4.5`, `anthropic.claude-…`). Unknown Claude
     models use high resolution. Models with no published rule: see decisions below.
2. One block shape for all adapters. `attrs.media` is a **list** of
   `{mime, width, height, bytes, detail, digest, tokens, method}`; a block may hold several images.
   - Image part in a user or assistant message: that role's `block_type`, content
     `[image png 2000×1224 sha256:1a2b3c4d]` (unique per image, no base64).
   - Tool results: stay one `tool_result` block (call linking assumes one result per call). Same marker
     per image inside the content. `token_count` = tokens of the text **without** the markers + image
     estimates. A helper returning (text, media) replaces `flatten_content` at these sites;
     `Block.make` receives the explicit count.
   - Ollama: read `messages[].images`, and do not skip a message that has images but no text.
   - `image_generation_call`: output stays `other`, never tokenizing `result`; count 0, marked
     unavailable (output-side image billing is not covered here). Resent as input, estimate it like
     any input image.
   - Remote URL or `file_id`: `method: unavailable`, shown as unknown.
3. Classification: no new rule in `classifier.py`, but two effects to test:
   - An image-only user message becomes the current user message, so earlier text moves to history.
   - Agents that attach a tool's screenshot as a following user message (possible with Chat
     Completions) would have it read as user input and `purpose = user_turn`. Text injected this way
     already behaves so. Accepted; noted in docs.
4. UI:
   - User colour plus image badge and dimensions; the badge also on tool results holding images.
   - The inspector's token view re-tokenizes block content (`/tokenize/window`), which would show the
     marker's tokens beside a count of thousands. Hide the token highlighting for media-only blocks
     and label mixed blocks "image tokens are estimated and not highlighted".
   - All numbers (text part, image part) come from the API.
5. Backfill (`SCHEMA_VERSION` 11, no new column), only where the canonical body is retained:
   - Match stored and parsed blocks by `(direction, json_path)`. `_v9_match_json_paths` cannot be
     used: it requires equal block type and content hash, and both change. Rows without `json_path`
     are matched by position; if the block count differs, skip the request and count it.
   - Update matched media blocks in place: type, content, hash, count, attrs, `source_key`.
   - Then **reclassify every input block of the request** (moving the last user message changes other
     blocks' categories) and recompute category totals, `tool_stats`, `purpose`. Output totals too
     for `image_generation_call`.
   - Find candidates with both `;base64,` and `"type":"base64"`, plus Ollama `"images"`.
   - Idempotent; report requests updated, skipped and without a body.
6. `CLASSIFIER_VERSION`: bump only if `sources.py` / `purpose.py` code changes.
7. Tests: dimension readers (incl. truncated data), documented worked examples for each rule including
   downscaled ones, each adapter, tool results with text + several images, identity (two images differ,
   the same image matches), migration on a fixture and on a scratch copy of a real database. Existing
   tests asserting the `[image]` / `[input_image]` markers will change. Update `SPEC.md`, `docs/*`,
   changelog.

## Files, PDFs, audio

Adapter run on synthetic input, 2026-11-25:

| Input | Counted today |
|---|---|
| Anthropic PDF, Chat `file`, Responses `input_file`, audio | 2–3 tokens (marker) |
| Anthropic `document` with `source.type: text` | 3 tokens; the real text is dropped |
| Unrecognised item type carrying ~137,000 base64 characters | 92,424 tokens (base64 counted as text) |

No retained request body in the measured database holds a file, document or audio part, so nothing
here is checked against real traffic. Server-tool results with `encrypted_content` were not checked.

Provider docs ([Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support),
[OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs)):

- Anthropic PDF: per page, extracted text (typically 1,500–3,000 tokens) plus a page image (at most
  1,568).
- OpenAI PDF: extracted text plus page images (`detail` sets image resolution); no per-page figures.
  Other documents: text only; spreadsheets: first 1,000 rows per sheet plus a generated summary.
- Audio: no token rule in the pages read.

In scope for this change:

- Count text documents (`source.type: text` or `content`) as text, in messages and tool results.
- Catch-all branches never tokenize long base64 strings.
- Every file/audio/video part gets the same identity marker as images (type, size, digest, file name
  if sent), `method: unavailable`, shown as "not estimated" rather than 3 tokens.

Not in this change: PDF estimates. They need a page count and ideally text extraction (likely a new
dependency such as `pypdf`), cached by digest because history is resent on every request.

## Accepted limits

- The same image can have different counts under different models. Occurrence and hot-spot views
  already allow differing counts for one hash.
- Block identity changes for backfilled rows but not for purged ones, so one image can appear as two
  across that boundary.
- PDF token estimates, audio, video and office files are not estimated (see below). Ollama `/api/generate` is not parsed
  today (neither prompt nor images) and stays so.

## Measurement

Anthropic, `claude-sonnet-5-5` only: 17 consecutive-request pairs where images first appear. Estimate =
provider input delta minus visible text delta, compared with the formula.

- Pairs with little new text (under 200 visible tokens): provider is 68–88 tokens per image above the
  formula. This probably includes message framing and text tokenizer error.
- Pairs with 600–900 tokens of new text differ by up to about 1,000, consistent with the local
  tokenizer undercounting Claude text, but not separated from image error.
- Not covered: images large enough to be downscaled, any OpenAI or self-hosted model.

No overhead correction is added.

## Decisions (user accepted the recommendations, 2026-11-25)

- **Models with no published rule?** Recommended: Gemma 3 gets 256; others get a rough
  `ceil(w/32) × ceil(h/32)`, `method: rough`, shown differently. It can be far off. Alternative:
  unknown, 0 tokens.
- **PDF estimates?** Recommended: a follow-up change.
- **Thumbnail in the inspector?** Recommended: not in this change. The bytes are in the retained
  request body, so it can be added later; it means rendering captured screenshots in the UI.

## Implementation status

Implemented: `analysis/media.py`; all four adapters (`content_block` in `adapters/base.py`); migration
v11; UI (tile badge, inspector list, token highlighting off for media-only blocks); tests
(`tests/test_media.py`, `tests/test_media_migration.py`, `BlockMedia.test.tsx`); docs. Decisions: rough
estimate for models without a rule, Gemma 3 = 256, no thumbnail, PDF estimates deferred.

Differences from the design above:

- OpenAI `gpt-5.3*` is not in the docs table; sized like `gpt-5.2`. Deprecated mini/nano/o4 patch
  models are sized like the `gpt-5.2` row.
- Dimensions of a block's media are in `attrs.media`, with `media_tokens` and `media_only` added.
- The UI shows dimensions in the inspector, not on the tile (too small).
- Migration v11 does not add rows: a request with an Ollama image that was never stored is skipped.
- No test or note specific to the Chat Completions follow-up-screenshot case beyond `docs/development.md`.
- `CLASSIFIER_VERSION` unchanged.

Verification (2026-11-25):

- `pytest`: 827 passed. `npm test`: 231 passed in 43 files, including
  `BlockMedia.test.tsx`; `npm run build` succeeded.
- `db-upgrade` logic run on a scratch copy (online backup API) of a 6.1 GB database: 2,286 candidates,
  177 requests updated, 0 unmatched, 0 failed, 158 s; block count unchanged; input totals equal block
  sums plus message overhead; second run changed nothing; `quick_check` ok. All 957 media blocks were
  Claude images with a published estimate. Request `802de6e1…` now has a 3,168-token image in the
  current user message.
- The 633 requests reported without a body count only blocks the old adapters flagged as media;
  Anthropic tool results with images were never flagged, so the real number is higher. The earlier
  "2,628 of 2,803 media blocks" figure has the same gap.

Not verified: no browser check; `contextspy db-upgrade` not run on the live database; no real OpenAI,
Ollama or self-hosted image traffic; no real file, PDF or audio traffic; downscaled images not compared
with provider usage.
