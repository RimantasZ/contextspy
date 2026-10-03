# How ContextSpy tracks requests and conversations

ContextSpy records the LLM calls it can observe, then uses provider links and captured context to
show how those calls relate. The key distinction is that **a conversation row is not necessarily
one unbroken chain of requests**. ContextSpy can have good evidence that two runs belong to the
same conversation without knowing which request directly preceded the next one.

This guide describes the current behavior of the Overview dashboard and Session Detail. It
supersedes the original dashboard design's assumption that consecutive requests in a session
should be compared as parent and child.

## Terms

| Term | Meaning |
| --- | --- |
| **Request** or **invocation** | One observable call to an LLM provider and its response or failure. One user prompt or agent task can produce several requests. Streaming chunks and WebSocket frames do not each become requests. |
| **Provider** | The LLM API/service that handles a request, such as OpenAI or Anthropic. This is different from the **agent**, the client application such as Codex or Claude Code. An `agent: codex` label does not identify a particular Codex task, process, or subagent. |
| **Session** | A named recording time window. Requests starting while a session is active belong to it; requests captured without an active session are ungrouped. A session can contain several independent or forked conversations. Only one session is active at a time. |
| **Session request number** (`#404`, for example) | A stable, session-local recording label assigned when the request row is saved. It is not a turn number, conversation position, or parent link. Concurrent requests can finish in a different order from when they started. |
| **Parent / predecessor** | The earlier request whose response or context is directly continued by a later request. A parent need not have the immediately preceding session request number; an explicitly referenced parent may even be in another session. |
| **Lineage** | The graph of accepted direct parent-to-child continuation links. An **exact** link comes from a provider-issued predecessor response ID; an **inferred** link is ContextSpy's conservative conclusion from captured context. |
| **Lineage segment** | A run of requests joined by accepted lineage links. A gap between segments means direct continuity was not established. |
| **Diagnostic path** | One root-to-leaf route through the lineage graph. Paths can share ancestors, and several paths or disconnected segments can belong to one displayed conversation. A path count is not a chat or agent count. Older API responses call this `lineage_fragment_count`; `diagnostic_path_count` is the clearer alias. |
| **Conversation** or **request stream** | A supported backend display group. A long accepted chain can have a row even when its relationship to another row remains unresolved; the row's evidence label says so. A row can contain more than one lineage segment and does **not** prove one unbroken chat. |
| **Auxiliary requests** | An unnumbered holding block for unclassified one-offs and short provisional chains (normally no more than three evidence-connected requests). It may contain unrelated calls and is **not** itself one conversation or proof of a subagent. |
| **Fork** | Two or more children of the same parent. A structural branch in the graph is not automatically a confirmed split into separate conversations. |
| **Capture** | The act of observing and storing provider traffic. It is not another grouping level above or below session; older API or diagnostic wording may use “capture” for the recorded session. |

## From traffic to a request

ContextSpy observes provider invocations through its proxy. A buffered HTTP exchange, a completed
SSE/NDJSON stream, or a supported WebSocket start-to-terminal lifecycle produces one request row.
The request keeps its provider, agent label, model, observed start when available, completion
time, outcome, token counts, provider response/predecessor IDs when supplied, and analyzed input
and output blocks. Failed or incomplete calls can still be recorded. See [REST, streaming, and
WebSocket request handling](transport-normalization.md) for the exact invocation boundaries.

Session membership is fixed when an invocation starts, even if its response arrives after the
session ends. Session request numbers are assigned when rows are saved. Neither membership nor
adjacent numbers establish a conversation or a parent.

For a provider-managed request that names a previous response, ContextSpy can expand the visible
canonical input from that **explicit** predecessor's input and output plus the current input.
An inferred display-lineage link never authorizes reconstruction of missing provider state.
When explicit state is missing or opaque, ContextSpy reports partial or opaque context instead
of inventing content.

Short Anthropic `thread` requests are handled in the same spirit: an exact
`thread.previous_message_id` can connect the retained earlier messages and assistant response
to the newly sent messages. The request's raw JSON still shows only what crossed the wire;
its canonical JSON and blocks show the reconstructed, client-visible history. This is not a
claim that the provider's internal prompt is identical: server-side context edits can remove
content, and an omitted system-block tail is shown as an uncertain reconstruction. A cache
diagnostic `previous_message_id` is not used as a parent link. If earlier raw/canonical bodies
have been purged, the missing content cannot be recovered from billed token counts.

## How a direct parent is established

ContextSpy analyzes lineage in the Python backend from stored request and block metadata,
separately from session recording order. The UI displays those decisions; it does not infer
parents or conversation membership itself.

1. **Use an explicit provider link first.** If a request contains a predecessor response ID,
   ContextSpy matches it to a captured response from the same provider. That produces an
   **exact** parent edge, including when the parent was captured in another session. If the
   referenced response cannot be linked, the state is **provider predecessor missing**; the
   analyzer does not replace that explicit reference with a guessed parent. A link that would
   create a cycle is also rejected.
2. **Otherwise, look for retained context.** For requests without an explicit predecessor ID,
   ContextSpy considers earlier captured requests with matching meaningful block fingerprints.
   A fingerprint includes the block type, content hash, and relevant tool name/call ID. Ordered
   matches distinguish input retained from a candidate parent's context and parent output that
   appears in the child's input. Repeated configuration such as system prompts and tool
   definitions carries little weight; transcript and tool-call evidence matter more. A known
   change of agent raises the evidence required for an **inferred** edge: copied context alone
   does not establish a direct handoff. Exact provider links can still cross agent labels.
3. **Accept only a clear winner.** The heuristic measures how much of the candidate parent's
   input was retained, how much of the child's input it explains, and—when available—how much
   parent output was carried forward. Its score must be at least **0.80** and exceed the next
   candidate by at least **0.15** to become an **inferred** direct edge. When an accepted lineage
   path establishes that one candidate is an older ancestor of another, ContextSpy also checks
   the *turn frontier*: does the newer candidate contribute distinctive output that occurs later
   in the child's ordered input? If so, it discounts the older ancestor **for the comparison**.
   Sibling candidates are not discounted, so a real fork remains possible. Candidates scoring
   at least **0.45** but lacking a clear winner leave the parent **ambiguous**. A sole weak
   candidate does not bypass the 0.80 floor. The detailed UI labels this an **inference score**
   out of 100, **not** a calibrated probability of correctness.

An accepted exact successor can later help reassess an earlier close heuristic boundary, within
a small bounded look-ahead. It confirms that the successor continues its named parent; it does
**not** by itself prove who preceded that parent. Revising an earlier inferred edge still needs
ordered, distinctive context evidence. The analyzer recomputes this projection when the captured
request set changes, so an inferred link or display grouping can change retrospectively; an
explicit provider predecessor is never overridden by a guessed one.

For transparency, the current score weights retained input / child coverage / promoted output
at 35% / 45% / 20% when parent output is available, or 45% / 55% for the first two signals
otherwise. Common blocks are downweighted, configuration blocks are heavily discounted, and
matching tool-call IDs are weighted more strongly. Boilerplate-only matches cannot reach the
   acceptance threshold. Partial or opaque child context reduces the score. Candidate search is
bounded to 64 plausible earlier requests for performance, so this is evidence from what
ContextSpy captured, not omniscient reconstruction of the agent's internal state.

If no usable candidate exists, ContextSpy reports **no predecessor established** (or
**predecessor context unavailable** when block evidence is absent). It never creates a direct
edge merely because requests have consecutive numbers, similar timestamps, the same model, or
the same generic agent label.

## How requests become conversation rows

First, accepted exact and inferred edges form lineage segments and diagnostic paths. ContextSpy
then computes a separate, conservative **conversation projection** for the dashboard and
Session Detail. These groups are derived when the data is read, not stored as permanent
conversation IDs; new requests or newly available evidence can change a grouping or row order.

The projection follows these rules:

- A parentless or otherwise unclassified request starts in **Auxiliary requests**. An accepted
  link between two such requests grows a provisional cluster, but does not automatically turn
  it into a numbered conversation. Several unrelated one-offs may be displayed in the same
  auxiliary block, with explicit lineage gaps. A session can have zero confirmed conversations
  and still show this block.
- A coherent chain of at least three accepted linked requests can become a confirmed
  conversation. To make it a **separate** conversation from an established stream, ContextSpy
  also needs substantial observed context that is disjoint from the streams' recent and anchor
  context, plus an independent distinction such as a known different agent, a corroborated
  stream hint, or a long sustained independent chain. Sparse or opaque captures with too few
  usable fingerprints cannot turn apparent non-overlap into proof. A provider or model change
  alone is not a split. An unlinked cluster can instead rejoin an existing row through strong
  retained context; that display bridge preserves the unknown direct-parent boundary.
- A chain of **four or more evidence-connected requests** does not remain in Auxiliary requests
  merely because separation from another row cannot be proven. It gets a numbered row labelled
  **relationship to other conversations not established**. This is a supported stream, not a
  claim that the two rows are independent. Later evidence can join them.
- A graph branch becomes a **confirmed conversation fork** only when both child branches have
  sustained accepted continuation with exact diverging edges, or when their observed calls
  overlap and carry distinct meaningful context. A one-off replay or an uncertain structural
  branch stays diagnostic, not a new conversation.
- Two disconnected chains can establish **parallel independent conversations** when each has
  at least three requests, their *observed* starts show sustained A–B–A–B interleaving, they
  share neither ancestry nor meaningful non-configuration fingerprints, and no plausible
  ambiguous cross-parent remains. Estimated start times and two isolated calls alone are not
  enough.
- For Codex requests to a Responses endpoint, a valid `prompt_cache_key` can be a
  **stream-affinity hint**. ContextSpy stores only its digest and source, not the raw key as a
  separate field. The raw/canonical request body can still contain the key until normal payload
  retention purges it. A matching hint **plus substantial shared non-configuration context** can
  place disconnected segments in the same display row. A distinct hint can support another
  row only when an independent accepted chain and dissimilar substantive context corroborate
  it. An exact parent edge overrides a changed hint. [OpenAI documents the key for cache
  routing and accounting](https://developers.openai.com/api/docs/guides/prompt-caching);
  ContextSpy therefore treats it as a hint, **not** a documented conversation, Codex process,
  or subagent ID.

The current affinity check requires at least three shared meaningful fingerprints, at least
70% overlap by both distinct fingerprints and block-token weight relative to the smaller
context, and at least 128 shared block-token weight. This strict check deliberately excludes
shared boilerplate. After a large context reset, a narrower bridge can use a matching stream
hint and same known agent, a non-overlapping boundary within five minutes, an older supported
chain, and at least ten shared fingerprints covering 70% of the smaller fingerprint set with
512 shared block-token weight. This can join two rows even when the usual 70% token-weight
ratio fails. The boundary card is marked **same stream after context reset**; the direct
predecessor remains unknown. A display-only affinity bridge **never** becomes a parent edge or a
parent-relative token comparison. Short requests or chains that cannot be assigned confidently
remain in Auxiliary requests; a longer coherent chain gets a row with unresolved-relationship
evidence. A previously auxiliary cluster can be promoted or rejoined as later requests supply
corroboration. This can
change its row on refresh while the original direct-parent state remains visible. Cards in the
auxiliary block say **provisional stream** separately from their direct-parent marker: an exact
edge between two provisional cards confirms that edge, but does not by itself confirm a new
conversation. ContextSpy currently does not label a request as "auxiliary to" a specific
conversation without positive task evidence.

For example, if `#404` ends one exact chain, `#407–#410` continue with strong affinity to it
but no recorded direct parent for `#407`, and `#405–#406` form a separate supported stream, the
rows can look like this:

```text
Latest conversation:  #410  #409  #408  #407⋯  #404  #403  #402 …
Other conversation:   #406  #405⋯  …
```

The `⋯` on `#407` means “same stream, direct predecessor not established”; it does **not** draw
an edge from `#404` to `#407`. Rows are ordered by their latest completed request, so the row
ending at `#410` is above the one ending at `#406`. Cards within a row are shown newest-first.
Confirmed forks can show the same earlier request in multiple rows as **shared history**;
session totals still count that stored request only once. Do not add row request counts to
calculate session totals.

## Reading the screens

- **Overview → Active session:** defaults to a single chronological **Sequence** row for the
  currently active session. It shows each stored request once, newest first, including auxiliary
  requests and any shared history. If there are multiple display groups, the “N conversations”
  button switches to separate conversation rows. Here `N` includes Auxiliary requests when
  present; the numbered-conversation count shown elsewhere excludes it. The dashboard previews
  up to 15 cards per row; “More” opens Session Detail. The activity chart above the rows shows
  the last 10 requests from the *whole session*, not one conversation. The separate global
  **Recent requests** table remains a chronological audit list.
- **Session Detail → Conversations:** also defaults to **Sequence**, with “More” loading older
  cards. Switch to conversation rows to see backend-supported streams, their evidence, segment
  boundaries, and Auxiliary requests. Numbered rows are ordered by latest activity, with
  Auxiliary requests last. You can load more conversations or older cards in each row. “More
  (N total)” counts the represented requests in that row; grouped rows may repeat shared
  history. The segment index helps find lineage breaks in a long row. The activity chart above
  the rows again shows only the last 10 session requests.
- **Request cards and context:** compact cards are the default. The first line shows a label
  such as `#C1-128` (request 128 in Conversation 1) or `#AUX-345` (Auxiliary requests) and the
  lineage icon. The second shows time and duration in seconds; the third shows `↑` input-context
  tokens and `↓` output tokens. In a grouped view, shared-history cards carry that row's
  conversation code. In the chronological row, shared history uses its first confirmed
  conversation. A selected card has a darker background. The Request flow header contains the
  conversation switch and a Compact/Detailed toggle. Grouped conversations share one section,
  with an independent toggle on each row (including Auxiliary requests), so one can be expanded
  without expanding the others. Sequence and each conversation default to Compact independently;
  explicit card-density choices are kept only for that session until the page is reloaded.
  A fresh load returns to Compact. Detailed cards
  show additional metadata; both modes explain the top-right icon in a tooltip.
  Click a card once to select it and update the context panel; click it again, or use “Open
  request” in the panel, for Request Detail. As new requests arrive, selection follows the
  newest request automatically. The panel can grow to include request actions in the future.
- **Session Detail → Conversations → Lineage diagnostics:** shows the accepted exact and
  inferred parent edges, uncertain candidates, missing/external parents, structural branches,
  and root-to-leaf diagnostic paths. This is the place to inspect *why* a row has gaps. A
  diagnostic path is not automatically a separate conversation. A “graph branch” badge means
  only a structural split; a “confirmed fork” badge means the stricter conversation rule passed.
  Selecting an edge shows which input blocks persisted, which parent output was carried into
  child input, and which blocks were added, removed, or replaced relative to that parent.
- **Context size:** the primary number is **estimated visible input tokens** from local block
  analysis, not necessarily the provider's complete context usage. When supplied, the
  provider-reported input total appears separately. This distinction matters most for opaque
  or partial captures. Selecting a card compares that request with its **resolved direct parent**,
  not the preceding session request number or the nearest card in the row. If the
  parent is missing or uncertain, there is no token delta. An exact parent from another session
  may be used for comparison but is excluded from this session's cards and totals. The input
  token delta is a difference between locally analyzed input sizes; “Block changes” are net
  counts of input block types, not a proof that content was added or removed. A zero block-count
  delta can still hide replaced content. Block comparison is marked partial when either context
  is partial. When either context is opaque, ContextSpy shows observed-only counts for visible
  block types and **Opaque changes: x** for the absolute difference in opaque input-item counts.
  It does not compare encrypted contents: `0` does not mean the hidden state stayed the same.
  A parent-relative token delta can still be shown for an opaque comparison.

The `?view=lineage` URL opens the default **Sequence** layout;
`?view=lineage&layout=conversations` opens grouped conversations. Older links with a
`conversation` parameter still open the requested grouped view. `?view=lineage&mode=fragments`
opens **Lineage diagnostics**.

## Request-card corner icons

Hover over (or keyboard-focus) a request card's top-right icon for its full explanation, in
compact and detailed mode alike. Other tooltips, such as the full timestamp, remain
detailed-mode only. The explanation is also included in the card's accessible label. The same glyph may represent two
related states, so read the detailed explanation rather than interpreting the shape alone.

| Icon | Tooltip heading and explanation | What it means |
| --- | --- | --- |
| ↳ | **Exact predecessor.** The provider explicitly linked this request to its predecessor. | Confirmed direct provider link. |
| ≈ | **Inferred predecessor.** ContextSpy inferred a direct predecessor from the captured context. | Accepted heuristic parent link, not provider-confirmed. |
| ≈ | **Suggested predecessor.** A possible predecessor was found, but the link is not confirmed. | Candidate only; no accepted direct edge. The UI supports this label, but the current analyzer does not emit it; weaker candidates are reported as ambiguous. |
| ⋯ | **Same stream; direct predecessor not established.** Shared context places this request in the same conversation, but its direct predecessor is unknown. | Display membership across a lineage gap, not a parent link. |
| ⋯ | **Same stream after context reset; direct predecessor not established.** Earlier retained context and a matching stream hint support this conversation after a context reset. | Guarded display bridge across a context reset, not a parent link. |
| ? | **Direct predecessor ambiguous.** More than one request could be the direct predecessor. | Plausible candidates, no accepted parent. |
| ! | **Provider predecessor missing.** The provider named a predecessor that ContextSpy could not link in this session. | Explicit reference exists, but no accepted link. |
| ? | **Predecessor context unavailable.** There is not enough captured context to identify a predecessor. | Insufficient block evidence. |
| ○ | **No predecessor established.** No direct predecessor was established for this request. | Start of a diagnostic segment; not necessarily the first turn of a real-world chat. |
| ↗ | **Predecessor in another session.** This request follows a predecessor captured in another session. | Exact cross-session parent; only the child belongs to this session's counts. |

## What not to conclude

The corner icon reports the **direct-parent or same-stream evidence for that card**, not the
status of the entire row. A `○`, `?`, or `!` card can appear in Auxiliary requests or alongside
linked cards in a confirmed conversation; row membership alone never upgrades its parent.

Conversation rows express **supported streams, not verified agent identities**. ContextSpy
currently does not persist a reliable Codex task or subagent ID. The Auxiliary requests block
may hide several unproven independent activities; multiple diagnostic paths may still be one ongoing
conversation. It also does not currently establish delegation or contribution links between
agent tasks. An inferred score is evidence strength, not certainty. Missing or opaque context,
purged raw request bodies, and older captures without a recoverable cache hint can make grouping
more conservative. Retained block fingerprints may still support lineage after readable block
content has been purged.

When upgrading an existing database, `contextspy db-upgrade` attempts to backfill supported
stream-hint digests from request bodies that are still retained. It cannot recover a hint from a
body already purged by retention. Missing historical hints leave the analyzer with the other
lineage and context evidence; they do not justify guessing a separate conversation.

For details on session commands, see the [CLI reference](cli.md#session-commands). For how visible
provider context and token counts are reconstructed, see [REST, streaming, and WebSocket request
handling](transport-normalization.md).
