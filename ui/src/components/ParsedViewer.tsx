// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useState, useEffect, useCallback } from "react";
import { tokenizeApi } from "../api/client";
import { useRequestBlocks } from "../api/hooks";
import type { RequestBlock } from "../api/client";

// ---------------------------------------------------------------------------
// Token color palette — cycles per token index
// ---------------------------------------------------------------------------
const TOKEN_COLORS = [
  "rgba(99,102,241,0.32)",
  "rgba(52,211,153,0.25)",
  "rgba(251,191,36,0.28)",
  "rgba(239,68,68,0.22)",
  "rgba(56,189,248,0.25)",
  "rgba(167,139,250,0.28)",
  "rgba(251,146,60,0.25)",
  "rgba(34,197,94,0.22)",
  "rgba(244,114,182,0.22)",
  "rgba(20,184,166,0.25)",
];

// ---------------------------------------------------------------------------
// Visual styling — derived from the backend's structural block_type
// ---------------------------------------------------------------------------
type Visual =
  | "system"
  | "tool_def"
  | "user"
  | "assistant"
  | "tool_call"
  | "tool_result"
  | "thinking"
  | "prefill"
  | "other";

const BLOCK_TYPE_VISUAL: Record<string, Visual> = {
  system_prompt: "system",
  tool_definition: "tool_def",
  user_message: "user",
  assistant_message: "assistant",
  assistant_prefill: "prefill",
  tool_call: "tool_call",
  tool_result: "tool_result",
  thinking: "thinking",
};

function visualOf(b: RequestBlock): Visual {
  return BLOCK_TYPE_VISUAL[b.block_type] ?? "other";
}

const CAT_BAR: Record<Visual, string> = {
  system: "bg-purple-500",
  tool_def: "bg-amber-500",
  user: "bg-blue-500",
  assistant: "bg-emerald-500",
  tool_call: "bg-orange-500",
  tool_result: "bg-teal-500",
  thinking: "bg-violet-500",
  prefill: "bg-lime-500",
  other: "bg-gray-500",
};
const CAT_LABEL: Record<Visual, string> = {
  system: "text-purple-300",
  tool_def: "text-amber-300",
  user: "text-blue-300",
  assistant: "text-emerald-300",
  tool_call: "text-orange-300",
  tool_result: "text-teal-300",
  thinking: "text-violet-300",
  prefill: "text-lime-300",
  other: "text-gray-400",
};
const CAT_BG: Record<Visual, string> = {
  system: "rgba(168,85,247,0.18)",
  tool_def: "rgba(245,158,11,0.18)",
  user: "rgba(59,130,246,0.18)",
  assistant: "rgba(16,185,129,0.18)",
  tool_call: "rgba(249,115,22,0.18)",
  tool_result: "rgba(20,184,166,0.18)",
  thinking: "rgba(139,92,246,0.18)",
  prefill: "rgba(132,204,22,0.18)",
  other: "rgba(107,114,128,0.14)",
};
const CAT_BORDER: Record<Visual, string> = {
  system: "rgba(168,85,247,0.55)",
  tool_def: "rgba(245,158,11,0.55)",
  user: "rgba(59,130,246,0.55)",
  assistant: "rgba(16,185,129,0.55)",
  tool_call: "rgba(249,115,22,0.55)",
  tool_result: "rgba(20,184,166,0.55)",
  thinking: "rgba(139,92,246,0.55)",
  prefill: "rgba(132,204,22,0.55)",
  other: "rgba(107,114,128,0.45)",
};

function blockLabel(b: RequestBlock): string {
  switch (b.block_type) {
    case "system_prompt":
      return "System";
    case "tool_definition":
      return b.tool_name ?? "Tool";
    case "tool_result":
      return b.tool_name
        ? `Result: ${b.tool_name}`
        : `Result (msg ${b.message_index ?? "?"})`;
    case "tool_call":
      return b.tool_name
        ? `Call: ${b.tool_name}`
        : `Tool call (msg ${b.message_index ?? "?"})`;
    case "assistant_prefill":
      return `Prefill (msg ${b.message_index ?? "?"})`;
    case "thinking":
      return `Thinking${b.message_index != null ? ` (msg ${b.message_index})` : ""}`;
    case "user_message":
      return `User${b.message_index != null ? ` ${b.message_index}` : ""}`;
    case "assistant_message":
      return `Assistant${b.message_index != null ? ` ${b.message_index}` : ""}`;
    default:
      return b.block_type;
  }
}

function blockKey(b: RequestBlock): number {
  return b.id;
}

// ---------------------------------------------------------------------------
// Link chips — shared between the Overview detail panel and Parsed-tab headers
// ---------------------------------------------------------------------------
function LinkChips({
  block,
  onJump,
}: {
  block: RequestBlock;
  onJump: (targetId: number) => void;
}) {
  return (
    <>
      {block.linked_previous_message_id != null && (
        <button
          onClick={() => onJump(block.linked_previous_message_id!)}
          title="Jump to previous message"
          className="text-[10px] px-1.5 py-0.5 rounded border border-blue-500/40 text-blue-300 hover:bg-blue-500/10 whitespace-nowrap"
        >
          ← previous
        </button>
      )}
      {block.linked_call_id != null && (
        <button
          onClick={() => onJump(block.linked_call_id!)}
          title={`Jump to tool call${block.tool_name ? `: ${block.tool_name}` : ""}`}
          className="text-[10px] px-1.5 py-0.5 rounded border border-orange-500/40 text-orange-300 hover:bg-orange-500/10 whitespace-nowrap"
        >
          → call
        </button>
      )}
      {block.linked_definition_id != null && (
        <button
          onClick={() => onJump(block.linked_definition_id!)}
          title={`Jump to tool definition${block.tool_name ? `: ${block.tool_name}` : ""}`}
          className="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-300 hover:bg-amber-500/10 whitespace-nowrap"
        >
          → definition
        </button>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Context overview — contribution-graph-style justified block layout
// ---------------------------------------------------------------------------
function packRows(
  blocks: RequestBlock[],
  tokensPerRow: number,
): RequestBlock[][] {
  const rows: RequestBlock[][] = [];
  let current: RequestBlock[] = [];
  let rowTotal = 0;
  for (const b of blocks) {
    const t = Math.max(b.token_count, 1);
    if (rowTotal + t > tokensPerRow && current.length > 0) {
      rows.push(current);
      current = [b];
      rowTotal = t;
    } else {
      current.push(b);
      rowTotal += t;
    }
  }
  if (current.length > 0) rows.push(current);
  return rows;
}

function ContextOverview({
  blocks,
  tokensList,
  selectedIdx,
  onSelect,
}: {
  blocks: RequestBlock[];
  tokensList?: (string[] | null)[] | null;
  selectedIdx: number | null;
  onSelect: (idx: number | null) => void;
}) {
  const totalTokens = blocks.reduce(
    (s, b) => s + Math.max(b.token_count, 1),
    0,
  );
  if (totalTokens === 0) return null;
  const tokensPerRow = Math.max(Math.ceil(totalTokens / 6), 200);
  const rows = packRows(blocks, tokensPerRow);
  const selectedBlock = selectedIdx != null ? blocks[selectedIdx] : null;
  const selectedTokens =
    selectedIdx != null && tokensList
      ? (tokensList[selectedIdx] ?? null)
      : null;

  const jumpToId = (targetId: number) => {
    const idx = blocks.findIndex((b) => b.id === targetId);
    if (idx !== -1) onSelect(idx);
  };

  return (
    <div>
      <div className="p-3 space-y-1">
        {rows.map((row, ri) => (
          <div key={ri} className="flex gap-1" style={{ height: 52 }}>
            {row.map((b) => {
              const idx = blocks.indexOf(b);
              const visual = visualOf(b);
              const isSelected = selectedIdx === idx;
              return (
                <button
                  key={blockKey(b)}
                  onClick={() => onSelect(isSelected ? null : idx)}
                  style={{
                    flex: `${Math.max(20, Math.sqrt(Math.max(b.token_count, 1)))} 1 0`,
                    minWidth: 0,
                    background: isSelected
                      ? CAT_BORDER[visual]
                      : CAT_BG[visual],
                    borderLeft: `3px solid ${CAT_BORDER[visual]}`,
                    outline: isSelected
                      ? `2px solid ${CAT_BORDER[visual]}`
                      : "none",
                    outlineOffset: "1px",
                  }}
                  className="rounded-sm px-2 py-1.5 overflow-hidden text-left transition-all hover:brightness-125 cursor-pointer"
                  title={`${blockLabel(b)}: ${b.token_count.toLocaleString()} tokens`}
                >
                  <div
                    className={`text-xs font-medium truncate leading-tight ${isSelected ? "text-white" : CAT_LABEL[visual]}`}
                  >
                    <span className="tabular-nums mr-1">
                      {b.token_count.toLocaleString()} tokens
                      <br />
                    </span>
                    {blockLabel(b)}
                  </div>
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {selectedBlock && (
        <div className="border-t border-gray-700 mx-3 mb-3">
          <div
            className="flex items-center justify-between px-3 py-2 rounded-t"
            style={{
              background: CAT_BG[visualOf(selectedBlock)],
              borderLeft: `3px solid ${CAT_BORDER[visualOf(selectedBlock)]}`,
            }}
          >
            <span
              className={`text-xs font-medium ${CAT_LABEL[visualOf(selectedBlock)]} truncate min-w-0`}
            >
              {blockLabel(selectedBlock)}
            </span>
            <div className="flex items-center gap-2 shrink-0">
              <LinkChips block={selectedBlock} onJump={jumpToId} />
              <span className="text-[10px] text-gray-500 tabular-nums">
                {selectedBlock.token_count.toLocaleString()} tokens
              </span>
              <button
                onClick={() => onSelect(null)}
                className="text-gray-500 hover:text-gray-300 text-xs leading-none"
              >
                ✕
              </button>
            </div>
          </div>
          <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-words max-h-[320px] overflow-auto bg-gray-900 px-3 py-2.5 rounded-b border border-gray-700 border-t-0 leading-6">
            {selectedBlock.content_purged ? (
              <span className="text-gray-500 italic">
                Content purged (older than retention window).
              </span>
            ) : selectedTokens ? (
              selectedTokens.map((tok, j) => (
                <span
                  key={j}
                  style={{ background: TOKEN_COLORS[j % TOKEN_COLORS.length] }}
                  className="rounded-[2px] text-gray-100"
                >
                  {tok}
                </span>
              ))
            ) : (
              selectedBlock.content
            )}
          </pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Block component — shows tokenized text with colored spans
// ---------------------------------------------------------------------------
function TokenBlock({
  block,
  tokens,
  collapsed,
  onToggleCollapsed,
  highlighted,
  onJump,
}: {
  block: RequestBlock;
  tokens: string[] | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  highlighted: boolean;
  onJump: (targetId: number) => void;
}) {
  const visual = visualOf(block);

  return (
    <div
      id={`block-${block.id}`}
      className={`border rounded-lg overflow-hidden transition-shadow ${
        highlighted
          ? "border-indigo-400 ring-2 ring-indigo-400/60"
          : "border-gray-700"
      }`}
    >
      <div className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800">
        <button
          onClick={onToggleCollapsed}
          className="flex items-center gap-2 flex-1 min-w-0 text-left"
        >
          <span
            className={`w-1 self-stretch rounded-full shrink-0 ${CAT_BAR[visual]}`}
          />
          <span className={`text-xs font-medium ${CAT_LABEL[visual]} truncate`}>
            {blockLabel(block)}
          </span>
        </button>
        <LinkChips block={block} onJump={onJump} />
        <span className="text-xs text-gray-500 tabular-nums">
          {block.token_count.toLocaleString()} tokens
        </span>
        <button
          onClick={onToggleCollapsed}
          className="text-gray-600 text-xs ml-1"
        >
          {collapsed ? "▶" : "▼"}
        </button>
      </div>
      {!collapsed && (
        <div className="px-3 py-2.5 bg-gray-900 text-xs font-mono leading-relaxed overflow-auto max-h-[400px]">
          {block.content_purged ? (
            <span className="text-gray-500 italic">
              Content purged (older than retention window).
            </span>
          ) : tokens === null ? (
            <span className="text-gray-400 whitespace-pre-wrap break-words">
              {block.content}
            </span>
          ) : (
            <span className="whitespace-pre-wrap break-words leading-6">
              {tokens.map((tok, j) => (
                <span
                  key={j}
                  style={{ background: TOKEN_COLORS[j % TOKEN_COLORS.length] }}
                  className="rounded-[2px] text-gray-100"
                >
                  {tok}
                </span>
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ParsedViewer — Overview / Parsed tabs are server-driven (blocks API);
// Raw tab still shows the exact raw request body when available.
// ---------------------------------------------------------------------------
interface Props {
  requestId: string;
  rawBody: string | null | undefined;
  totalInputTokens?: number | null;
}

export function ParsedViewer({ requestId, rawBody, totalInputTokens }: Props) {
  const [tab, setTab] = useState<"overview" | "parsed" | "raw">("overview");
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [tokenized, setTokenized] = useState<string[][] | null>(null);
  const [loading, setLoading] = useState(false);
  const [showHighlight, setShowHighlight] = useState(true);
  const [colorizeOverview, setColorizeOverview] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [highlightedId, setHighlightedId] = useState<number | null>(null);

  const blocksQuery = useRequestBlocks(requestId);
  const allBlocks = blocksQuery.data?.blocks;
  const blocks = allBlocks
    ? allBlocks.filter((b) => b.direction === "input")
    : [];

  const jumpToBlock = useCallback((targetId: number) => {
    setTab("parsed");
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.add(targetId);
      return next;
    });
    setHighlightedId(targetId);
    setTimeout(() => {
      document
        .getElementById(`block-${targetId}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    setTimeout(
      () => setHighlightedId((id) => (id === targetId ? null : id)),
      1500,
    );
  }, []);

  const fetchTokens = useCallback(async () => {
    if (blocks.length === 0) return;
    setLoading(true);
    try {
      const res = await tokenizeApi.tokenize(
        blocks.map((b) => b.content ?? ""),
      );
      setTokenized(res.results);
    } catch {
      setTokenized(null);
    } finally {
      setLoading(false);
    }
  }, [allBlocks]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setTokenized(null);
    setSelectedIdx(null);
    fetchTokens();
  }, [fetchTokens]);

  if (blocksQuery.isLoading) {
    return <p className="px-4 py-3 text-sm text-gray-500 italic">Loading…</p>;
  }

  if (blocks.length === 0 && rawBody == null) {
    return (
      <p className="px-4 py-3 text-sm text-gray-500 italic">
        No block data available for this request.
      </p>
    );
  }

  const totalTokens = tokenized
    ? blocks.reduce((s, _b, i) => s + (tokenized[i]?.length ?? 0), 0)
    : null;

  let rawPretty = rawBody ?? "";
  try {
    rawPretty = JSON.stringify(JSON.parse(rawPretty), null, 2);
  } catch {
    /* keep as-is */
  }

  return (
    <div className="overflow-auto max-h-[700px]">
      {/* Top-level tab bar */}
      <div className="flex items-center justify-between border-b border-gray-800">
        <div className="flex">
          {(["overview", "parsed", "raw"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors capitalize ${
                tab === t
                  ? "border-indigo-500 text-indigo-300"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {t === "overview"
                ? "Overview"
                : t === "parsed"
                  ? "Parsed"
                  : "Raw"}
            </button>
          ))}
        </div>
        <div className="pr-3 flex items-center gap-3">
          {loading && (
            <span className="text-xs text-gray-500 italic">Tokenizing…</span>
          )}
          {!loading &&
            (totalInputTokens != null || totalTokens !== null) &&
            tab !== "raw" && (
              <span className="text-xs text-gray-500 tabular-nums">
                {(totalInputTokens ?? totalTokens!).toLocaleString()} tokens
              </span>
            )}
          {tab === "overview" && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={colorizeOverview}
                onChange={(e) => setColorizeOverview(e.target.checked)}
                className="accent-indigo-500"
              />
              Highlight tokens
            </label>
          )}
          {tab === "parsed" && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showHighlight}
                onChange={(e) => setShowHighlight(e.target.checked)}
                className="accent-indigo-500"
              />
              Highlight tokens
            </label>
          )}
        </div>
      </div>

      {/* Overview tab */}
      {tab === "overview" &&
        (blocks.length === 0 ? (
          <div className="p-4 text-xs text-gray-500 italic">
            No block data available.
          </div>
        ) : (
          <ContextOverview
            blocks={blocks}
            tokensList={colorizeOverview ? tokenized : null}
            selectedIdx={selectedIdx}
            onSelect={setSelectedIdx}
          />
        ))}

      {/* Parsed tab */}
      {tab === "parsed" && (
        <div className="p-3 space-y-2">
          {blocks.length === 0 ? (
            <div className="p-4 text-xs text-gray-500 italic">
              No block data available.
            </div>
          ) : (
            blocks.map((block, i) => (
              <TokenBlock
                key={blockKey(block)}
                block={block}
                tokens={
                  showHighlight && tokenized ? (tokenized[i] ?? null) : null
                }
                collapsed={!expandedIds.has(block.id)}
                onToggleCollapsed={() =>
                  setExpandedIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(block.id)) next.delete(block.id);
                    else next.add(block.id);
                    return next;
                  })
                }
                highlighted={highlightedId === block.id}
                onJump={jumpToBlock}
              />
            ))
          )}
        </div>
      )}

      {/* Raw tab */}
      {tab === "raw" && (
        <div className="p-4 overflow-auto max-h-[600px]">
          {rawBody == null ? (
            <p className="text-sm text-gray-500 italic">
              Raw content has been purged.
            </p>
          ) : (
            <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-all">
              {rawPretty}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
