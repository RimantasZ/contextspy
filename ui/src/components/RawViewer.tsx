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
import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { ParsedViewer } from './ParsedViewer';
import { tokenizeApi } from '../api/client';
import { useRequestBlocks } from '../api/hooks';

type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };

const TOKEN_COLORS = Array.from({ length: 7 }, (_, index) => `var(--token-highlight-${index + 1})`);

// ---------------------------------------------------------------------------
// Syntax-highlighted, collapsible JSON tree (used by the response JSON tab)
// ---------------------------------------------------------------------------

interface NodeProps {
  value: JsonValue;
  depth?: number;
  searchLower: string;
}

function highlight(text: string, searchLower: string): React.ReactNode {
  if (!searchLower || !text.toLowerCase().includes(searchLower)) return text;
  const idx = text.toLowerCase().indexOf(searchLower);
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded bg-[var(--search-mark)] text-[var(--search-mark-text)]">{text.slice(idx, idx + searchLower.length)}</mark>
      {text.slice(idx + searchLower.length)}
    </>
  );
}

function JsonNode({ value, depth = 0, searchLower }: NodeProps) {
  const [collapsed, setCollapsed] = useState(depth > 2);
  const indent = depth * 14;

  if (value === null) return <span className="text-[var(--text-muted)]">null</span>;
  if (typeof value === 'boolean') return <span className="text-[var(--syntax-boolean)]">{String(value)}</span>;
  if (typeof value === 'number') return <span className="text-[var(--syntax-number)]">{value}</span>;
  if (typeof value === 'string') {
    return <span className="text-[var(--syntax-string)]">"{highlight(value, searchLower)}"</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-[var(--text-muted)]">[]</span>;
    return (
      <span>
        <button
          onClick={() => setCollapsed(c => !c)}
          className="cursor-pointer select-none font-mono text-[var(--text-muted)] hover:text-[var(--text)]"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '▶' : '▼'}
        </button>
        <span className="text-[var(--text-muted)]"> [</span>
        {collapsed ? (
          <button
            onClick={() => setCollapsed(false)}
            className="ml-1 text-xs italic text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            {value.length} item{value.length !== 1 ? 's' : ''} …
          </button>
        ) : (
          <div style={{ paddingLeft: indent + 14 }}>
            {value.map((item, i) => (
              <div key={i} className="my-0.5">
                <JsonNode value={item} depth={depth + 1} searchLower={searchLower} />
                {i < value.length - 1 && <span className="text-[var(--text-subtle)]">,</span>}
              </div>
            ))}
          </div>
        )}
        {!collapsed && <span className="text-[var(--text-muted)]" style={{ paddingLeft: indent }}>]</span>}
        {collapsed && <span className="text-[var(--text-muted)]"> ]</span>}
      </span>
    );
  }

  // object
  const entries = Object.entries(value as { [k: string]: JsonValue });
  if (entries.length === 0) return <span className="text-[var(--text-muted)]">{'{}'}</span>;
  return (
    <span>
      <button
        onClick={() => setCollapsed(c => !c)}
        className="cursor-pointer select-none font-mono text-[var(--text-muted)] hover:text-[var(--text)]"
        title={collapsed ? 'Expand' : 'Collapse'}
      >
        {collapsed ? '▶' : '▼'}
      </button>
      <span className="text-[var(--text-muted)]"> {'{'}</span>
      {collapsed ? (
        <button
          onClick={() => setCollapsed(false)}
          className="ml-1 text-xs italic text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          {entries.length} key{entries.length !== 1 ? 's' : ''} …
        </button>
      ) : (
        <div style={{ paddingLeft: indent + 14 }}>
          {entries.map(([k, v], i) => (
            <div key={k} className="my-0.5">
              <span className="text-[var(--syntax-key)]">
                "{highlight(k, searchLower)}"
              </span>
              <span className="text-[var(--text-muted)]">: </span>
              <JsonNode value={v} depth={depth + 1} searchLower={searchLower} />
              {i < entries.length - 1 && <span className="text-[var(--text-subtle)]">,</span>}
            </div>
          ))}
        </div>
      )}
      {!collapsed && <span className="text-[var(--text-muted)]" style={{ paddingLeft: indent }}>{'}'}</span>}
      {collapsed && <span className="text-[var(--text-muted)]"> {'}'}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tokenized text pane — shared by the response Text and Thinking tabs
// ---------------------------------------------------------------------------

function TextPane({
  text,
  tokens,
  showHighlight,
  onToggleHighlight,
  note,
}: {
  text: string;
  tokens: string[] | null;
  showHighlight: boolean;
  onToggleHighlight: (v: boolean) => void;
  note?: React.ReactNode;
}) {
  return (
    <>
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-3 py-1.5">
        <span className="min-w-0 truncate text-xs text-[var(--text-muted)]">{note}</span>
        <label className="flex shrink-0 cursor-pointer select-none items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={showHighlight}
            onChange={e => onToggleHighlight(e.target.checked)}
            className="accent-indigo-500"
          />
          Highlight tokens
        </label>
      </div>
      <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
        {showHighlight && tokens !== null ? (
          <span className="whitespace-pre-wrap break-words leading-6">
            {tokens.map((tok, i) => (
              <span
                key={i}
                style={{ background: TOKEN_COLORS[i % TOKEN_COLORS.length] }}
                className="rounded-[2px] text-[var(--text)]"
              >
                {tok}
              </span>
            ))}
          </span>
        ) : (
          <pre className="whitespace-pre-wrap break-words text-[var(--text)]">{text}</pre>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Main RawViewer
// ---------------------------------------------------------------------------

type RespTab = 'text' | 'thinking' | 'json' | 'events' | 'raw';
type TextTab = Extract<RespTab, 'text' | 'thinking'>;

/** How far to trust a thinking block's token count — set by the adapters'
 *  shared reconcile_thinking(); see analysis/adapters/base.py. */
const THINKING_SOURCE_NOTE: Record<string, string> = {
  provider: 'Token count reported by the API.',
  estimated: 'Token count estimated from the returned text.',
  derived:
    'This API reports no separate thinking count — derived from the output tokens the visible response does not account for.',
  unknown: 'The provider returned neither the reasoning text nor a token count for it.',
};

interface Props {
  title: string;
  requestId: string;
  content: string | null | undefined;
  /** When true shows the output view: Text / Thinking / JSON / Raw tabs */
  responseMode?: boolean;
  responseEvents?: unknown[] | null;
  responseTransport?: string;
  responseReconstructed?: boolean;
  responseComplete?: boolean;
  captureError?: Record<string, unknown> | null;
  totalInputTokens?: number | null;
  /** Increment to toggle open/close; scroll into view when opening */
  expandToggle?: number;
}

export function RawViewer({
  title, requestId, content, responseMode, responseEvents, responseTransport,
  responseReconstructed, responseComplete, captureError, totalInputTokens, expandToggle,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [respTab, setRespTab] = useState<RespTab>('text');
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

  // Response "Text"/"Thinking" tabs — token highlight state, cached per pane
  const [showHighlight, setShowHighlight] = useState(true);
  const [paneTokens, setPaneTokens] = useState<Partial<Record<TextTab, string[]>>>({});
  const [loadingText, setLoadingText] = useState(false);

  // Output blocks power the response "Text" and "Thinking" tabs even when the
  // raw body has been purged by retention — block contents/token counts persist
  // longer, and hidden thinking never appears in the raw body at all.
  const blocksQuery = useRequestBlocks(requestId, !!responseMode);
  const outputBlocks = (blocksQuery.data?.blocks ?? []).filter(b => b.direction === 'output');

  const textBlocks = outputBlocks.filter(b => b.block_type === 'assistant_message');
  const respText = textBlocks.map(b => b.content ?? '').join('\n');
  const respTextPurged = textBlocks.length > 0 && textBlocks.every(b => b.content_purged);
  const respTokenCount = textBlocks.reduce((s, b) => s + b.token_count, 0);

  const thinkingBlocks = outputBlocks.filter(b => b.block_type === 'thinking');
  const thinkingText = thinkingBlocks.map(b => b.content ?? '').filter(Boolean).join('\n\n');
  const thinkingTokens = thinkingBlocks.reduce((s, b) => s + b.token_count, 0);
  const thinkingPurged =
    thinkingBlocks.length > 0 && thinkingBlocks.some(b => b.content_purged) && !thinkingText;
  const thinkingRedacted = thinkingBlocks.some(b => b.attrs?.redacted === true);
  const thinkingSource = thinkingBlocks
    .map(b => b.attrs?.token_source)
    .find(s => typeof s === 'string') as string | undefined;
  const hasThinking = thinkingBlocks.length > 0;

  const respTabs: Array<{ key: RespTab; label: string }> = [
    { key: 'text', label: 'Text' },
    ...(hasThinking ? [{ key: 'thinking' as RespTab, label: 'Thinking' }] : []),
    { key: 'json', label: 'JSON' },
    ...((responseEvents?.length ?? 0) > 0 ? [{ key: 'events' as RespTab, label: 'Events' }] : []),
    { key: 'raw', label: 'Raw' },
  ];

  const { parsed, isJson } = useMemo(() => {
    if (!content) return { parsed: null, isJson: false };
    try {
      return { parsed: JSON.parse(content), isJson: true };
    } catch {
      return { parsed: content, isJson: false };
    }
  }, [content]);

  const copyToClipboard = useCallback(() => {
    if (content) navigator.clipboard.writeText(content);
  }, [content]);

  // Reset tokens when the underlying text changes
  useEffect(() => { setPaneTokens({}); }, [respText, thinkingText]);

  // A request with no thinking must not stay parked on a tab that isn't there
  useEffect(() => {
    if (respTab === 'thinking' && !hasThinking) setRespTab('text');
  }, [respTab, hasThinking]);

  // Respond to external toggle requests (expandToggle prop)
  useEffect(() => {
    if (!expandToggle) return;
    setOpen(prev => {
      const next = !prev;
      if (next) {
        setTimeout(() => {
          containerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 50);
      }
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandToggle]);

  // Fetch tokens for whichever text pane is showing
  useEffect(() => {
    if (!responseMode || loadingText) return;
    if (respTab !== 'text' && respTab !== 'thinking') return;
    const pane: TextTab = respTab;
    const body = pane === 'text' ? respText : thinkingText;
    if (!body || paneTokens[pane] !== undefined) return;
    setLoadingText(true);
    tokenizeApi.tokenize([body])
      .then(r => setPaneTokens(t => ({ ...t, [pane]: r.results[0] ?? [] })))
      .catch(() => setPaneTokens(t => ({ ...t, [pane]: [] })))
      .finally(() => setLoadingText(false));
  }, [responseMode, respTab, respText, thinkingText, paneTokens, loadingText]);

  const purged = content === null || content === undefined;

  function handleToggle() {
    const opening = !open;
    setOpen(opening);
    if (opening) {
      setTimeout(() => {
        containerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 50);
    }
  }

  return (
    <div ref={containerRef} className="surface overflow-hidden rounded-lg border border-[var(--border)]">
      {/* Header */}
      <div className="flex items-center justify-between bg-[var(--surface)] px-4 py-2.5">
        <button
          onClick={handleToggle}
          className="flex items-center gap-2 text-sm font-medium text-[var(--text)]"
        >
          <span className="text-xs text-[var(--text-muted)]">{open ? '▼' : '▶'}</span>
          {title}
          {totalInputTokens != null && (
            <span className="ml-2 font-mono text-xs text-[var(--text-muted)]">{totalInputTokens.toLocaleString()} tokens</span>
          )}
        </button>
        {open && !purged && (
          <button
            onClick={copyToClipboard}
            className="app-button min-h-8 px-2 py-0.5 text-xs"
          >
            Copy
          </button>
        )}
      </div>

      {open && (
        <div className="code-surface">
          {responseMode ? (
            /* ----------------------------------------------------------------
               Response mode: JSON | Raw | Text tabs
            ---------------------------------------------------------------- */
            <>
              <div className="flex border-b border-[var(--border)]">
                {respTabs.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setRespTab(key)}
                    className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                      respTab === key
                        ? 'border-[var(--accent)] text-[var(--accent-soft-text)]'
                        : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
                    }`}
                  >
                    {label}
                    {key === 'thinking' && thinkingTokens > 0 && (
                      <span className="ml-1.5 font-mono text-[10px] text-[var(--thinking)]">
                        {thinkingTokens.toLocaleString()}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {(responseReconstructed || responseComplete === false || captureError) && (
                <div className="flex flex-wrap gap-2 border-b border-[var(--border)] px-3 py-2 text-[10px]">
                  {responseReconstructed && (
                    <span className="status-info rounded px-2 py-0.5">
                      Reconstructed from {responseTransport ?? 'stream'}
                    </span>
                  )}
                  {responseComplete === false && (
                    <span className="status-warning rounded px-2 py-0.5">Incomplete capture</span>
                  )}
                  {captureError && (
                    <span className="status-danger rounded px-2 py-0.5">
                      Capture warning: {String(captureError.message ?? captureError.stage ?? 'unknown error')}
                    </span>
                  )}
                </div>
              )}

              {/* JSON tab — collapsible tree (needs the raw body; purged if gone) */}
              {respTab === 'json' && (
                purged ? (
                  <p className="px-4 py-3 text-sm italic text-[var(--text-muted)]">Raw content has been purged.</p>
                ) : (
                  <>
                    <div className="border-b border-[var(--border)] px-3 py-2">
                      <input
                        type="text"
                        placeholder="Search…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        aria-label="Search response JSON"
                        className="app-field w-full py-1 text-xs"
                      />
                    </div>
                    <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                      {isJson ? (
                        <JsonNode value={parsed as JsonValue} depth={0} searchLower={searchLower} />
                      ) : (
                        <pre className="whitespace-pre-wrap break-all text-[var(--text)]">{content}</pre>
                      )}
                    </div>
                  </>
                )
              )}

              {/* Events tab — complete normalized SSE/WS application events. */}
              {respTab === 'events' && (
                <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                  <JsonNode
                    value={(responseEvents ?? []) as JsonValue}
                    depth={0}
                    searchLower={searchLower}
                  />
                </div>
              )}

              {/* Raw tab — plain text (needs the raw body; purged if gone) */}
              {respTab === 'raw' && (
                purged ? (
                  <p className="px-4 py-3 text-sm italic text-[var(--text-muted)]">Raw content has been purged.</p>
                ) : (
                  <div className="p-4 overflow-auto max-h-[600px]">
                    <pre className="whitespace-pre-wrap break-all font-mono text-xs text-[var(--text)]">
                      {isJson ? JSON.stringify(parsed, null, 2) : content}
                    </pre>
                  </div>
                )
              )}

              {/* Text tab — response text derived from output blocks, which
                  outlive the raw body under retention */}
              {respTab === 'text' && (
                respTextPurged ? (
                  <p className="px-4 py-3 text-sm italic text-[var(--text-muted)]">
                    Response text has been purged ({respTokenCount.toLocaleString()} tokens).
                  </p>
                ) : !respText ? (
                  <p className="px-4 py-3 text-sm italic text-[var(--text-muted)]">
                    No response text found.
                  </p>
                ) : (
                  <TextPane
                    text={respText}
                    tokens={paneTokens.text ?? null}
                    showHighlight={showHighlight}
                    onToggleHighlight={setShowHighlight}
                    note={`${respTokenCount.toLocaleString()} tokens`}
                  />
                )
              )}

              {/* Thinking tab — reasoning the model generated, when the
                  provider returns it. The tokens are charged either way, so
                  the pane still reports the count when the text is withheld. */}
              {respTab === 'thinking' && (
                thinkingPurged ? (
                  <p className="px-4 py-3 text-sm italic text-[var(--text-muted)]">
                    Thinking text has been purged ({thinkingTokens.toLocaleString()} tokens).
                  </p>
                ) : thinkingText ? (
                  <TextPane
                    text={thinkingText}
                    tokens={paneTokens.thinking ?? null}
                    showHighlight={showHighlight}
                    onToggleHighlight={setShowHighlight}
                    note={`${thinkingTokens.toLocaleString()} tokens · ${
                      THINKING_SOURCE_NOTE[thinkingSource ?? ''] ?? ''
                    }`}
                  />
                ) : (
                  <div className="px-4 py-4 space-y-1.5">
                    <p className="text-sm text-[var(--text)]">
                      <span className="font-mono text-[var(--thinking)]">
                        {thinkingTokens.toLocaleString()}
                      </span>{' '}
                      thinking tokens — the provider did not return the reasoning text.
                    </p>
                    <p className="text-xs text-[var(--text-muted)]">
                      {thinkingRedacted
                        ? 'The reasoning was redacted by the provider’s safety systems.'
                        : 'The model reasoned before answering but the text was withheld (e.g. thinking.display: "omitted", or encrypted reasoning).'}
                    </p>
                    {thinkingSource && (
                      <p className="text-xs text-[var(--text-subtle)]">
                        {THINKING_SOURCE_NOTE[thinkingSource] ?? ''}
                      </p>
                    )}
                  </div>
                )
              )}
            </>
          ) : (
            /* ----------------------------------------------------------------
               Default mode: ParsedViewer owns Overview / Parsed / Raw tabs.
               Overview/Parsed are server-driven (blocks API) and work even
               when the raw request body has already been purged.
            ---------------------------------------------------------------- */
            <ParsedViewer requestId={requestId} rawBody={content} totalInputTokens={totalInputTokens} />
          )}
        </div>
      )}
    </div>
  );
}
