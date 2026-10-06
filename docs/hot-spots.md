# Hot spots and block analysis

ContextSpy shows what each request puts into the context window. These features answer a second question: **what
does a conversation keep carrying, and what does it cost?** All numbers are **visible tokens** (what ContextSpy could
see and count), not billed or cached tokens, and nothing here needs a provider API.

For the internals see [development.md](development.md#hot-spots-queries); for what changed in a release see the
[changelog](changelog.md).

## Vocabulary

| Term | Meaning |
|---|---|
| **Block** | One piece of a request: a system prompt, a tool definition, a message, a tool call or result, reasoning. |
| **Same block** | Blocks with identical text. An edited file or a changed prompt is a new block. |
| **Occurrence** | One request carrying the block. A block sent with 54 requests has 54 occurrences. |
| **Total tokens** | Tokens per occurrence × occurrences, so a 12,000-token tool definition sent 54 times is 648,000. |
| **Purpose** | Why a request was made: `user_turn`, `tool_continuation` (results of tools such as Read or Grep), `compaction`, or `unknown`. Shown in the request header. |
| **Source** | What produced a block: `tool:Read`, `mcp:github/create_issue`, `bash:git`, `user`, `system`, ... Shell commands are reduced to the program name; arguments are not stored. |
| **Activity** | A group of sources: read, search, edit, vcs, test, command, web, orchestration, mcp, other. |
| **File** | The file a read/edit tool call targets (`Read`, `Edit`, `Write`, `cat`, `sed -n`, `apply_patch`, ...), and the result that answers it. It is the only tool argument ContextSpy keeps. |

## Present in (request page)

Select a block on a request page and the inspector's **Present in** section shows in how many requests of the
conversation (or the whole session, via the toggle) the same block occurs, its tokens per occurrence and in total, the
first and last request, whether it is still in the latest request, and the runs of requests it appears in. Click a
request to open it with that block selected; the selection is kept in the URL (`?block=<id>`), so it survives a refresh.

## The Hot spots page

Open a session and choose **Hot spots** in the session view control (or follow **Hot spots of C1** from the
Conversations view). It ranks what the context window carries most.

- **Scope:** one conversation (default: the one holding the latest request) or the whole session.
- **Group by:** **Blocks** (identical content), **Sources** (which tool or program costs the most) or **Files**
  (tokens attributed to each file, split into *read* by tool results and *edited* by the calls that wrote it).
- **Sort by:** total tokens or occurrences. Clicking the metric column header switches between the two.
- **Filters (Blocks):** category (the same eight as the context bar), block type, and *Still in context* /
  *Dropped*. Choosing **Blocks** on a source row filters the block view by that source.
- **Rows:** the bar is proportional to the first (largest) row. The number on the right is the main metric with its
  share of the scope in parentheses. Click a row to expand it: the other metric, requests, tokens per occurrence, first
  and last request, a short preview, and **Open latest request** (opens that request with the block selected).
- **Badges:** *dropped* means the block is not in the latest request of the scope; *reappears* (in the detail) means
  it left the context and came back; *N types* means the same text appears as several block types.
- **Labels switch:** **Name column** puts the label in its own column; **On the bar** writes it inside the bar when it
  fits and just after the bar otherwise. Both are available while we collect feedback (`layout=bar` in the URL).

The summary line says how much of the scope the listed rows cover and how many blocks are *unidentifiable* (hidden or
empty content has no identity, so it cannot be matched across requests and is not listed).

## Reading the numbers

- **Visible tokens only.** Requests marked *partial* or *opaque* hide part of their context; the summary counts them.
- **Exact text.** A file that changed between reads counts as different blocks in the Blocks view; the Files view ties
  versions together by path.
- **Paths are not resolved.** `src/app.py` and `/home/me/proj/src/app.py` appear as two files.
- **Archived sessions** keep the numbers, structure and paths, but have no text previews.
- **Large sessions:** the first Hot spots (or Conversations) load of a very long session performs a one-off
  conversation analysis that can take up to about a minute at 4,000 requests; afterwards it takes about a second.
  "Show more" repeats the ranking, so very large lists get slower to page.

## Upgrading an existing database

Run `contextspy db-upgrade` once before starting (the server refuses to start until you do). It backs up the database
first and fills in purpose, block sources, JSON locations and file paths for existing requests. It can only do that
where the request's text is still stored: requests whose payloads were removed earlier keep generic source labels and
no file paths. Details and timings are in the [changelog](changelog.md#upgrading-from-054).

## Keeping disk usage in check

Nothing is deleted automatically. **Archive** an ended session (Archive button, or `contextspy session archive <id>`)
to remove its raw payloads and block text while keeping counts, structure, sources, paths and the analysis, then run
`contextspy db-compact` with ContextSpy stopped to return the space to the disk. See the [CLI reference](cli.md).
