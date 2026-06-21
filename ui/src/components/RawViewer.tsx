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

// ---------------------------------------------------------------------------
// Response text extractor
// ---------------------------------------------------------------------------

type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };

function extractResponseText(parsed: unknown): string | null {
  if (!parsed || typeof parsed !== 'object') return null;
  const body = parsed as Record<string, unknown>;
  // OpenAI / synthetic format
  const choices = body.choices;
  if (Array.isArray(choices) && choices.length > 0) {
    const msg = ((choices[0] as Record<string, unknown>).message ?? {}) as Record<string, unknown>;
    const content = msg.content;
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return (content as { type?: string; text?: string }[])
        .filter(b => b.type === 'text')
        .map(b => b.text ?? '')
        .join('\n');
    }
    return null;
  }
  // Anthropic format — only text blocks, not thinking
  const content = body.content;
  if (Array.isArray(content)) {
    return (content as { type?: string; text?: string }[])
      .filter(b => b.type === 'text')
      .map(b => b.text ?? '')
      .join('\n');
  }
  return null;
}

function extractThinkingText(parsed: unknown): string | null {
  if (!parsed || typeof parsed !== 'object') return null;
  const body = parsed as Record<string, unknown>;
  // Anthropic format — thinking blocks only
  const content = body.content;
  if (Array.isArray(content)) {
    const parts = (content as { type?: string; thinking?: string }[])
      .filter(b => b.type === 'thinking')
      .map(b => b.thinking ?? '');
    return parts.length > 0 ? parts.join('\n') : null;
  }
  return null;
}

const TOKEN_COLORS = [
  'rgba(99,102,241,0.32)', 'rgba(52,211,153,0.25)', 'rgba(251,191,36,0.28)',
  'rgba(239,68,68,0.22)',  'rgba(56,189,248,0.25)', 'rgba(167,139,250,0.28)',
  'rgba(251,146,60,0.25)', 'rgba(34,197,94,0.22)',  'rgba(244,114,182,0.22)',
  'rgba(20,184,166,0.25)',
];

// ---------------------------------------------------------------------------
// Syntax-highlighted, collapsible JSON tree
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
      <mark className="bg-yellow-400 text-gray-900 rounded">{text.slice(idx, idx + searchLower.length)}</mark>
      {text.slice(idx + searchLower.length)}
    </>
  );
}

function JsonNode({ value, depth = 0, searchLower }: NodeProps) {
  const [collapsed, setCollapsed] = useState(depth > 2);
  const indent = depth * 14;

  if (value === null) return <span className="text-gray-500">null</span>;
  if (typeof value === 'boolean') return <span className="text-yellow-400">{String(value)}</span>;
  if (typeof value === 'number') return <span className="text-blue-400">{value}</span>;
  if (typeof value === 'string') {
    return <span className="text-green-400">"{highlight(value, searchLower)}"</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-400">[]</span>;
    return (
      <span>
        <button
          onClick={() => setCollapsed(c => !c)}
          className="text-gray-400 hover:text-white font-mono cursor-pointer select-none"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '▶' : '▼'}
        </button>
        <span className="text-gray-400"> [</span>
        {collapsed ? (
          <button
            onClick={() => setCollapsed(false)}
            className="text-gray-500 hover:text-gray-300 text-xs ml-1 italic"
          >
            {value.length} item{value.length !== 1 ? 's' : ''} …
          </button>
        ) : (
          <div style={{ paddingLeft: indent + 14 }}>
            {value.map((item, i) => (
              <div key={i} className="my-0.5">
                <JsonNode value={item} depth={depth + 1} searchLower={searchLower} />
                {i < value.length - 1 && <span className="text-gray-600">,</span>}
              </div>
            ))}
          </div>
        )}
        {!collapsed && <span className="text-gray-400" style={{ paddingLeft: indent }}>]</span>}
        {collapsed && <span className="text-gray-400"> ]</span>}
      </span>
    );
  }

  // object
  const entries = Object.entries(value as { [k: string]: JsonValue });
  if (entries.length === 0) return <span className="text-gray-400">{'{}'}</span>;
  return (
    <span>
      <button
        onClick={() => setCollapsed(c => !c)}
        className="text-gray-400 hover:text-white font-mono cursor-pointer select-none"
        title={collapsed ? 'Expand' : 'Collapse'}
      >
        {collapsed ? '▶' : '▼'}
      </button>
      <span className="text-gray-400"> {'{'}</span>
      {collapsed ? (
        <button
          onClick={() => setCollapsed(false)}
          className="text-gray-500 hover:text-gray-300 text-xs ml-1 italic"
        >
          {entries.length} key{entries.length !== 1 ? 's' : ''} …
        </button>
      ) : (
        <div style={{ paddingLeft: indent + 14 }}>
          {entries.map(([k, v], i) => (
            <div key={k} className="my-0.5">
              <span className="text-purple-300">
                "{highlight(k, searchLower)}"
              </span>
              <span className="text-gray-400">: </span>
              <JsonNode value={v} depth={depth + 1} searchLower={searchLower} />
              {i < entries.length - 1 && <span className="text-gray-600">,</span>}
            </div>
          ))}
        </div>
      )}
      {!collapsed && <span className="text-gray-400" style={{ paddingLeft: indent }}>{'}'}</span>}
      {collapsed && <span className="text-gray-400"> {'}'}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// SSE event stream viewer
// ---------------------------------------------------------------------------

function SseViewer({ raw, searchLower }: { raw: string; searchLower: string }) {
  const events = useMemo(() => {
    const result: { type: string; data: JsonValue | string }[] = [];
    let currentType = '';
    for (const line of raw.split('\n')) {
      if (line.startsWith('event: ')) {
        currentType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') {
          result.push({ type: currentType || 'done', data: '[DONE]' });
        } else {
          try {
            result.push({ type: currentType || 'data', data: JSON.parse(payload) });
          } catch {
            result.push({ type: currentType || 'data', data: payload });
          }
        }
        currentType = '';
      }
    }
    return result;
  }, [raw]);

  return (
    <div className="space-y-1">
      {events.map((ev, i) => (
        <div key={i} className="border border-gray-700 rounded">
          <div className="px-2 py-0.5 bg-gray-800 text-xs text-indigo-400 font-mono">{ev.type}</div>
          <div className="px-3 py-1 text-xs font-mono leading-relaxed">
            {typeof ev.data === 'string' ? (
              <span className="text-gray-400">{ev.data}</span>
            ) : (
              <JsonNode value={ev.data} depth={0} searchLower={searchLower} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main RawViewer
// ---------------------------------------------------------------------------

interface Props {
  title: string;
  content: string | null | undefined;
  parsedBody?: string | null;
  /** When true shows 3-tab output view: JSON tree / Raw text / Response text */
  responseMode?: boolean;
  totalInputTokens?: number | null;
  /** Increment to toggle open/close; scroll into view when opening */
  expandToggle?: number;
}

export function RawViewer({ title, content, parsedBody, responseMode, totalInputTokens, expandToggle }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'parsed' | 'raw'>('parsed');
  const [respTab, setRespTab] = useState<'thinking' | 'output' | 'json' | 'raw'>('output');
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

  // Response "Text" tab — token highlight state
  const [showHighlight, setShowHighlight] = useState(true);
  const [respTokens, setRespTokens] = useState<string[] | null>(null);
  const [loadingText, setLoadingText] = useState(false);

  const { parsed, isSse, isJson } = useMemo(() => {
    if (!content) return { parsed: null, isSse: false, isJson: false };
    if (content.includes('data: ') && content.includes('\n')) {
      // Likely SSE
      return { parsed: content, isSse: true, isJson: false };
    }
    try {
      return { parsed: JSON.parse(content), isSse: false, isJson: true };
    } catch {
      return { parsed: content, isSse: false, isJson: false };
    }
  }, [content]);

  const copyToClipboard = useCallback(() => {
    if (content) navigator.clipboard.writeText(content);
  }, [content]);

  // Reset tokens when content changes
  useEffect(() => { setRespTokens(null); }, [content]);

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

  // Fetch tokens for response "Output" or "Thinking" tab
  useEffect(() => {
    if (!responseMode || (respTab !== 'output' && respTab !== 'thinking') || !isJson || respTokens !== null || loadingText) return;
    const text = respTab === 'thinking' ? extractThinkingText(parsed) : extractResponseText(parsed);
    if (!text) return;
    setLoadingText(true);
    tokenizeApi.tokenize([text])
      .then(r => setRespTokens(r.results[0] ?? []))
      .catch(() => setRespTokens([]))
      .finally(() => setLoadingText(false));
  }, [responseMode, respTab, isJson, parsed, respTokens, loadingText]);

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
    <div ref={containerRef} className="border border-gray-700 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-800">
        <button
          onClick={handleToggle}
          className="flex items-center gap-2 text-sm text-gray-300 font-medium hover:text-white"
        >
          <span className="text-gray-500 text-xs">{open ? '▼' : '▶'}</span>
          {title}
          {!purged && totalInputTokens != null && (
            <span className="ml-2 text-xs text-gray-500 font-mono">{totalInputTokens.toLocaleString()} tokens</span>
          )}
        </button>
        {open && !purged && (
          <button
            onClick={copyToClipboard}
            className="text-xs text-gray-500 hover:text-gray-300 px-2 py-0.5 rounded border border-gray-600 hover:border-gray-400"
          >
            Copy
          </button>
        )}
      </div>

      {open && (
        <div className="bg-gray-900">
          {purged ? (
            <p className="px-4 py-3 text-sm text-gray-500 italic">
              Raw content has been purged.
            </p>
          ) : responseMode ? (
            /* ----------------------------------------------------------------
               Response mode: JSON | Raw | Text tabs
            ---------------------------------------------------------------- */
            <>
              <div className="flex border-b border-gray-800">
                {([
                  ['thinking', 'Thinking'],
                  ['output',   'Output'],
                  ['json',     'JSON'],
                  ['raw',      'Raw'],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => { setRespTab(key); setRespTokens(null); }}
                    className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                      respTab === key
                        ? 'border-indigo-500 text-indigo-300'
                        : 'border-transparent text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* JSON tab — collapsible tree */}
              {respTab === 'json' && (
                <>
                  <div className="px-3 py-2 border-b border-gray-800">
                    <input
                      type="text"
                      placeholder="Search…"
                      value={search}
                      onChange={e => setSearch(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                    {isJson ? (
                      <JsonNode value={parsed as JsonValue} depth={0} searchLower={searchLower} />
                    ) : (
                      <pre className="text-gray-300 whitespace-pre-wrap break-all">{content}</pre>
                    )}
                  </div>
                </>
              )}

              {/* Raw tab — plain text */}
              {respTab === 'raw' && (
                <div className="p-4 overflow-auto max-h-[600px]">
                  <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-all">
                    {isJson ? JSON.stringify(parsed, null, 2) : content}
                  </pre>
                </div>
              )}

              {/* Thinking tab — extracted thinking blocks */}
              {respTab === 'thinking' && (() => {
                const thinkingText = isJson ? extractThinkingText(parsed) : null;
                if (!thinkingText) {
                  return (
                    <p className="px-4 py-3 text-sm text-gray-500 italic">
                      No thinking content found.
                    </p>
                  );
                }
                return (
                  <>
                    <div className="flex items-center justify-end px-3 py-1.5 border-b border-gray-800">
                      <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={showHighlight}
                          onChange={e => setShowHighlight(e.target.checked)}
                          className="accent-indigo-500"
                        />
                        Highlight tokens
                      </label>
                    </div>
                    <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                      {showHighlight && respTokens !== null ? (
                        <span className="whitespace-pre-wrap break-words leading-6">
                          {respTokens.map((tok, i) => (
                            <span
                              key={i}
                              style={{ background: TOKEN_COLORS[i % TOKEN_COLORS.length] }}
                              className="rounded-[2px] text-gray-100"
                            >
                              {tok}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <pre className="text-gray-300 whitespace-pre-wrap break-words">{thinkingText}</pre>
                      )}
                    </div>
                  </>
                );
              })()}

              {/* Output tab — response text with optional token highlighting */}
              {respTab === 'output' && (() => {
                const respText = isJson ? extractResponseText(parsed) : null;
                if (!respText) {
                  return (
                    <p className="px-4 py-3 text-sm text-gray-500 italic">
                      No response text found.
                    </p>
                  );
                }
                return (
                  <>
                    <div className="flex items-center justify-end px-3 py-1.5 border-b border-gray-800">
                      <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={showHighlight}
                          onChange={e => setShowHighlight(e.target.checked)}
                          className="accent-indigo-500"
                        />
                        Highlight tokens
                      </label>
                    </div>
                    <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                      {showHighlight && respTokens !== null ? (
                        <span className="whitespace-pre-wrap break-words leading-6">
                          {respTokens.map((tok, i) => (
                            <span
                              key={i}
                              style={{ background: TOKEN_COLORS[i % TOKEN_COLORS.length] }}
                              className="rounded-[2px] text-gray-100"
                            >
                              {tok}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <pre className="text-gray-300 whitespace-pre-wrap break-words">{respText}</pre>
                      )}
                    </div>
                  </>
                );
              })()}
            </>
          ) : (
            /* ----------------------------------------------------------------
               Default mode: ParsedViewer owns Overview / Parsed / Raw tabs
            ---------------------------------------------------------------- */
            <>
              {parsedBody != null ? (
                <ParsedViewer rawBody={parsedBody} rawContent={content} totalInputTokens={totalInputTokens} />
              ) : (
                /* No parsedBody — fall back to JSON/SSE/text viewer */
                <>
                  <div className="px-3 py-2 border-b border-gray-800">
                    <input
                      type="text"
                      placeholder="Search…"
                      value={search}
                      onChange={e => setSearch(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div className="p-4 overflow-auto max-h-[600px] text-xs font-mono leading-relaxed">
                    {isSse ? (
                      <SseViewer raw={parsed as string} searchLower={searchLower} />
                    ) : isJson ? (
                      <JsonNode value={parsed as JsonValue} depth={0} searchLower={searchLower} />
                    ) : (
                      <pre className="text-gray-300 whitespace-pre-wrap break-all">
                        {typeof parsed === 'string' ? parsed : String(parsed)}
                      </pre>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
