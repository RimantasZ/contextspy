import { useState, useMemo, useCallback } from 'react';
import { ParsedViewer } from './ParsedViewer';

// ---------------------------------------------------------------------------
// Syntax-highlighted, collapsible JSON tree
// ---------------------------------------------------------------------------

type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };

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
}

export function RawViewer({ title, content, parsedBody }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'parsed' | 'raw'>('parsed');
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

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

  const purged = content === null || content === undefined;

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-800">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-2 text-sm text-gray-300 font-medium hover:text-white"
        >
          <span className="text-gray-500 text-xs">{open ? '▼' : '▶'}</span>
          {title}
          {!purged && (
            <span className="ml-2 text-xs text-gray-500">
              {isSse ? 'SSE stream' : isJson ? 'JSON' : 'text'}
            </span>
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
          ) : (
            <>
              {/* Tab bar — only when parsedBody is provided */}
              {parsedBody != null && (
                <div className="flex border-b border-gray-800">
                  {(['parsed', 'raw'] as const).map(t => (
                    <button
                      key={t}
                      onClick={() => setTab(t)}
                      className={`px-4 py-2 text-xs font-medium capitalize border-b-2 -mb-px transition-colors ${
                        tab === t
                          ? 'border-indigo-500 text-indigo-300'
                          : 'border-transparent text-gray-500 hover:text-gray-300'
                      }`}
                    >
                      {t === 'parsed' ? 'Parsed' : 'Raw'}
                    </button>
                  ))}
                </div>
              )}
              {/* Parsed tab */}
              {parsedBody != null && tab === 'parsed' && (
                <ParsedViewer rawBody={parsedBody} />
              )}
              {/* Raw tab (or sole content when no parsedBody) */}
              {(parsedBody == null || tab === 'raw') && (
                <>
                  {/* Search bar */}
                  <div className="px-3 py-2 border-b border-gray-800">
                    <input
                      type="text"
                      placeholder="Search…"
                      value={search}
                      onChange={e => setSearch(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  {/* Content */}
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
