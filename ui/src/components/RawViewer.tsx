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

const TOKEN_COLORS = [
  'rgba(99,102,241,0.32)', 'rgba(52,211,153,0.25)', 'rgba(251,191,36,0.28)',
  'rgba(239,68,68,0.22)',  'rgba(56,189,248,0.25)', 'rgba(167,139,250,0.28)',
  'rgba(251,146,60,0.25)', 'rgba(34,197,94,0.22)',  'rgba(244,114,182,0.22)',
  'rgba(20,184,166,0.25)',
];

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
// Main RawViewer
// ---------------------------------------------------------------------------

interface Props {
  title: string;
  requestId: string;
  content: string | null | undefined;
  /** When true shows 3-tab output view: JSON tree / Raw text / Response text */
  responseMode?: boolean;
  totalInputTokens?: number | null;
  /** Increment to toggle open/close; scroll into view when opening */
  expandToggle?: number;
}

export function RawViewer({ title, requestId, content, responseMode, totalInputTokens, expandToggle }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [respTab, setRespTab] = useState<'json' | 'raw' | 'text'>('text');
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

  // Response "Text" tab — token highlight state
  const [showHighlight, setShowHighlight] = useState(true);
  const [respTokens, setRespTokens] = useState<string[] | null>(null);
  const [loadingText, setLoadingText] = useState(false);

  // Output blocks power the response "Text" tab even when the raw body has
  // been purged by retention — block contents/token counts persist longer.
  const blocksQuery = useRequestBlocks(requestId, !!responseMode);
  const textBlocks = (blocksQuery.data?.blocks ?? []).filter(
    b => b.direction === 'output' && b.block_type === 'assistant_message'
  );
  const respText = textBlocks.map(b => b.content ?? '').join('\n');
  const respTextPurged = textBlocks.length > 0 && textBlocks.every(b => b.content_purged);
  const respTokenCount = textBlocks.reduce((s, b) => s + b.token_count, 0);

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
  useEffect(() => { setRespTokens(null); }, [respText]);

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

  // Fetch tokens for response "Text" tab
  useEffect(() => {
    if (!responseMode || respTab !== 'text' || respTokens !== null || loadingText) return;
    if (!respText || respTextPurged) return;
    setLoadingText(true);
    tokenizeApi.tokenize([respText])
      .then(r => setRespTokens(r.results[0] ?? []))
      .catch(() => setRespTokens([]))
      .finally(() => setLoadingText(false));
  }, [responseMode, respTab, respText, respTextPurged, respTokens, loadingText]);

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
          {totalInputTokens != null && (
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
          {responseMode ? (
            /* ----------------------------------------------------------------
               Response mode: JSON | Raw | Text tabs
            ---------------------------------------------------------------- */
            <>
              <div className="flex border-b border-gray-800">
                {([
                  ['text', 'Text'],
                  ['json', 'JSON'],
                  ['raw',  'Raw'],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => setRespTab(key)}
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

              {/* JSON tab — collapsible tree (needs the raw body; purged if gone) */}
              {respTab === 'json' && (
                purged ? (
                  <p className="px-4 py-3 text-sm text-gray-500 italic">Raw content has been purged.</p>
                ) : (
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
                )
              )}

              {/* Raw tab — plain text (needs the raw body; purged if gone) */}
              {respTab === 'raw' && (
                purged ? (
                  <p className="px-4 py-3 text-sm text-gray-500 italic">Raw content has been purged.</p>
                ) : (
                  <div className="p-4 overflow-auto max-h-[600px]">
                    <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-all">
                      {isJson ? JSON.stringify(parsed, null, 2) : content}
                    </pre>
                  </div>
                )
              )}

              {/* Text tab — response text derived from output blocks, which
                  outlive the raw body under retention */}
              {respTab === 'text' && (
                respTextPurged ? (
                  <p className="px-4 py-3 text-sm text-gray-500 italic">
                    Response text has been purged ({respTokenCount.toLocaleString()} tokens).
                  </p>
                ) : !respText ? (
                  <p className="px-4 py-3 text-sm text-gray-500 italic">
                    No response text found.
                  </p>
                ) : (
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
