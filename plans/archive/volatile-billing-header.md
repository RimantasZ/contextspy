# Keep Claude Code's per-request billing header out of block identity

Status: implemented (GitHub issue #65). Verified on a copy of a real database: first-system-block hashes went from
413 distinct to 134 across 419 blocks, no stored content starts with the header any more, the two requests from the
example (#33, #34) share one hash with their original token counts (1653 / 1608), and a second run changes nothing.
Existing databases need `contextspy db-upgrade` (schema v8).

Implementation notes / deviations from the draft below:
- `split_volatile_header` and the `Block.make` change only apply to **input system-prompt** blocks (a user message that
  quotes the header is left alone).
- `_migrate_to_v8` deletes the per-request `block_contents` copies it replaced once nothing references them. Checked
  `startup_vacuum`: it only runs when `block_content_days > 0`, so with retention disabled the copies would otherwise
  stay forever.
- Tests that hard-coded schema version 7 (`tests/test_backups.py`, `tests/test_migrations.py`) now use
  `migrations.SCHEMA_VERSION` or version 8; new tests are in `tests/test_volatile_header.py`.
- UI: `BlockInspector` shows `attrs.volatile_header` as "Per-request header (not part of block identity)".

## Problem

Claude Code prepends a line to the first system block of every Anthropic request:

```
x-anthropic-billing-header: cc_version=2.1.284.606; cc_entrypoint=cli; cch=75ba0; cc_prev_req=req_…; cc_prompt_id=…; cc_turn_origin=human; cc_prompt_index=4; cc_turn_index=4;
You are Claude Code, Anthropic's official CLI for Claude.
…
```

`cch`, `cc_prev_req`, the prompt id and the turn/prompt counters change on every request. ContextSpy
hashes the whole block (`analysis/blocks.py: content_hash`), so the first system prompt is treated as a
*different block* on every request even though everything else in it is byte-identical.

Observed on two consecutive requests of one session (#33 → #34): the block was 7308 vs 7199 chars and
1653 vs 1608 tokens. With the header line removed the two blocks are identical (7075 chars each), and it is the
only differing block among the first 144 input blocks.

Consequences:

- The "Show: New only / Highlight new" view (`plans/archive/show-new-block-only.md`) flags the system prompt as new on every
  request. Lineage `replaced` counts, first-seen tracking (`first_seen_session_seq`) and `context_diff` are
  affected the same way.
- `block_contents` stores a fresh ~7 KB copy of the system block per request instead of one per session.
- It looks like the cache prefix changed on every request, although the API reports ~98.9% cache reads
  (101,451 of 102,184 tokens on #33), so Anthropic's side evidently does not let this header break the cache.
  That is an inference from the cache numbers, not documented behaviour.

## Unknown: is the header billed?

Not determined. `provider_input_tokens` is a single total, so a ~45-token difference cannot be isolated, and
ContextSpy's own total is far below the API's (64,776 vs 102,184 on #33), so the numbers are not comparable at that
precision. This only affects whether `token_count` should include the header (see *Token counting*); it does not
block the identity fix.

Optional check: call Anthropic's `count_tokens` endpoint on one captured request body with and without the header
(needs an API key) and compare.

## Design: strip at capture, keep the header in `attrs`

Identity should be computed from the stable text, while nothing that was sent is lost.

1. **Detect and split** (`analysis/blocks.py`): add `split_volatile_header(content) -> tuple[str, str | None]`
   that removes leading `x-anthropic-billing-header:` line(s) (regex anchored at the start of the content, up to and
   including the newline). Keep the pattern list small and explicit so another agent's volatile header can be added
   deliberately.
2. **Apply in one place** (`Block.make`, which already computes `content_hash` and `token_count`):
   - `content` and `content_hash` use the stable text, so `block_contents` dedups across requests;
   - the removed line is stored as `attrs["volatile_header"]` (no schema change: `attrs` is already a JSON column);
   - `token_count` is counted on the **original** text so ContextSpy keeps matching what was sent. Revisit once the
     billing question above is answered.
3. **UI**: the block inspector shows the header from `attrs` as a labelled "Per-request header (not part of block
   identity)" row, so the content viewer shows the stable text without hiding that the header exists. The Raw view is
   unaffected because raw bodies are stored separately.
4. **Diff/lineage**: no change needed. They compare `content_hash`, which is now stable.

Rejected alternatives:

- *Normalise only at diff time*: needs the content (fails for purged blocks), and leaves storage dedup, first-seen
  tracking and the lineage hash broken.
- *Delete the header outright*: loses information and makes token counts disagree with the API.

## Migration (required: changes hashes of existing rows)

No `models.py` change, so no additive-column step. Existing rows keep the old per-request hash, so a data
migration is needed: bump `SCHEMA_VERSION` (currently 7) in `db/migrations.py`, add `_migrate_to_v8`, register it in
`_DATA_MIGRATIONS`; it runs via `contextspy db-upgrade` like the others.

`_migrate_to_v8`: for each `blocks` row of type `system_prompt` whose joined `block_contents.content` starts with the
header, compute the stable text, upsert it into `block_contents` under its new hash, set `blocks.content_hash` to
the new hash and write `attrs["volatile_header"]`. Leave `token_count` untouched. Blocks whose content was purged
(`content_hash` with no `block_contents` row) cannot be recovered and keep their old hash, so they still show as changed.
The old per-request `block_contents` rows become orphans; the existing `startup_vacuum` retention cleanup should collect
them, verify this rather than assuming it.

This is cheaper and safer than re-analysing from raw request bodies as `_migrate_to_v7` does, because it also works
where raw bodies were already purged but block contents were retained.

## Tests

- `split_volatile_header`: header + body, header only, no header, header not at the start (left alone), CRLF.
- `Block.make`: two blocks differing only in the header get equal `content_hash` and `content`; `token_count` equals
  the count of the original text; the header is in `attrs`.
- Anthropic adapter end to end: two captured request bodies differing only in the header yield an identical first
  system block hash.
- `context_diff`: with those blocks the system prompt is `persisted`, not `replaced`; `new_child_block_ids` excludes it.
- Migration: seeded rows with old hashes are rewritten, duplicates collapse to one `block_contents` row, purged
  blocks are left alone, running it twice is a no-op. `tests/test_migrations.py` has the pattern.

## Risks

- A future header format or a different agent's volatile line would not be stripped until added to the pattern list.
- Hash change means `first_seen_session_seq` for the system prompt becomes the session's first request after
  migration, which is the desired behaviour but a visible change.
- Anything that previously relied on the header being part of `content` (search in the workbench matches stable text
  only; add the header to the searchable fields if that matters).

## Verify

`pytest`, then `contextspy db-upgrade` on a copy of a real database, then open two consecutive requests of a session
with Show = New only: the system prompt must no longer be listed as new.
