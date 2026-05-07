import { useState, useRef, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useSession, useStatsSession, useTimeline, useRequests, useEndSession, useDeleteSession, useToolStats, useRenameSession } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { TimeSeriesChart } from '../components/TimeSeriesChart';
import { RequestTable } from '../components/RequestTable';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';

type Bucket = 'minute' | 'hour' | 'day';

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [bucket, setBucket] = useState<Bucket>('hour');
  const [renamingTitle, setRenamingTitle] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const renameTitleRef = useRef<HTMLInputElement>(null);

  const session = useSession(id ?? '');
  const stats = useStatsSession(id ?? '');
  const timeline = useTimeline(id, bucket);
  const requests = useRequests({ session_id: id, limit: 200 });
  const toolStats = useToolStats(id);
  const endSession = useEndSession();
  const deleteSession = useDeleteSession();
  const renameSession = useRenameSession();

  // focus input when rename mode activates
  useEffect(() => {
    if (renamingTitle) renameTitleRef.current?.select();
  }, [renamingTitle]);

  if (session.isLoading) return <div className="p-6 text-gray-400">Loading…</div>;
  if (!session.data) return <div className="p-6 text-red-400">Session not found.</div>;

  const s = session.data.session;
  const st = stats.data;

  function startRename() {
    setRenameValue(s.name);
    setRenamingTitle(true);
  }

  function commitRename() {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== s.name) {
      renameSession.mutate({ id: s.id, name: trimmed }, { onSuccess: () => setRenamingTitle(false), onError: () => setRenamingTitle(false) });
    } else {
      setRenamingTitle(false);
    }
  }

  function exportPdf() {
    const doc = new jsPDF({ orientation: 'portrait', unit: 'pt', format: 'a4' });
    const pageW = doc.internal.pageSize.getWidth();
    let y = 40;

    // ── Title ──────────────────────────────────────────────────────────
    doc.setFontSize(18);
    doc.setTextColor(30, 30, 30);
    doc.text(s.name, 40, y);
    y += 22;

    doc.setFontSize(9);
    doc.setTextColor(100, 100, 100);
    const startLabel = `Started: ${new Date(s.started_at).toLocaleString()}`;
    const endLabel = s.ended_at ? `  Ended: ${new Date(s.ended_at).toLocaleString()}` : '  (active)';
    doc.text(startLabel + endLabel, 40, y);
    y += 20;

    // ── Summary stats ──────────────────────────────────────────────────
    doc.setFontSize(12);
    doc.setTextColor(30, 30, 30);
    doc.text('Summary', 40, y);
    y += 6;

    autoTable(doc, {
      startY: y,
      head: [['Metric', 'Value']],
      body: [
        ['Requests', String(st?.request_count ?? 0)],
        ['Tokens in', (st?.tokens_total_input ?? 0).toLocaleString()],
        ['Tokens out', (st?.tokens_total_output ?? 0).toLocaleString()],
      ],
      theme: 'striped',
      headStyles: { fillColor: [55, 65, 81] },
      margin: { left: 40, right: 40 },
      tableWidth: pageW - 80,
    });
    y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 18;

    // ── Token breakdown by category ────────────────────────────────────
    if (st && Object.keys(st.by_category).length > 0) {
      doc.setFontSize(12);
      doc.setTextColor(30, 30, 30);
      doc.text('Token breakdown by category', 40, y);
      y += 6;

      autoTable(doc, {
        startY: y,
        head: [['Category', 'Tokens', '%']],
        body: Object.entries(st.by_category).map(([k, v]) => [
          k.replace(/_/g, ' '),
          v.tokens.toLocaleString(),
          `${v.pct.toFixed(1)}%`,
        ]),
        theme: 'striped',
        headStyles: { fillColor: [55, 65, 81] },
        margin: { left: 40, right: 40 },
        tableWidth: pageW - 80,
      });
      y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 18;
    }

    // ── Tool usage ─────────────────────────────────────────────────────
    const tools = toolStats.data?.tools ?? [];
    if (tools.length > 0) {
      doc.setFontSize(12);
      doc.setTextColor(30, 30, 30);
      doc.text('Tool usage', 40, y);
      y += 6;

      const totalInput = st?.tokens_total_input ?? 0;
      autoTable(doc, {
        startY: y,
        head: [['Tool', 'Def tokens', 'Result tokens', '% context']],
        body: tools.map(t => {
          const combined = t.definition_tokens + t.result_tokens;
          const pct = totalInput > 0 ? `${((combined / totalInput) * 100).toFixed(1)}%` : '—';
          return [
            t.tool_name,
            t.definition_tokens.toLocaleString(),
            t.result_tokens > 0 ? t.result_tokens.toLocaleString() : '—',
            pct,
          ];
        }),
        theme: 'striped',
        headStyles: { fillColor: [55, 65, 81] },
        margin: { left: 40, right: 40 },
        tableWidth: pageW - 80,
      });
      y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 18;
    }

    // ── Requests ───────────────────────────────────────────────────────
    const reqs = requests.data?.requests ?? [];
    if (reqs.length > 0) {
      doc.setFontSize(12);
      doc.setTextColor(30, 30, 30);
      doc.text('Requests', 40, y);
      y += 6;

      autoTable(doc, {
        startY: y,
        head: [['Time', 'Provider', 'Model', 'Tokens in', 'Tokens out', 'Status']],
        body: reqs.map(r => [
          new Date(r.timestamp).toLocaleString(),
          r.provider,
          r.model ?? '—',
          r.tokens_total_input.toLocaleString(),
          r.tokens_total_output.toLocaleString(),
          String(r.status_code ?? '—'),
        ]),
        theme: 'striped',
        headStyles: { fillColor: [55, 65, 81] },
        margin: { left: 40, right: 40 },
        tableWidth: pageW - 80,
        styles: { fontSize: 8 },
      });
    }

    // ── Save ───────────────────────────────────────────────────────────
    const ts = new Date(s.started_at)
      .toISOString()
      .replace('T', ' ')
      .replace(/:/g, '-')
      .slice(0, 19);
    doc.save(`${s.name} - ${ts}.pdf`);
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/sessions')} className="text-gray-400 hover:text-white text-sm">
            ← Sessions
          </button>
          <h1 className="text-xl font-bold text-white">
            {renamingTitle ? (
              <span className="flex items-center gap-1">
                <input
                  ref={renameTitleRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenamingTitle(false); }}
                  className="bg-gray-700 text-white text-xl font-bold rounded px-2 py-0.5 border border-gray-500 focus:outline-none focus:border-indigo-400 w-64"
                />
                <button onClick={commitRename} className="text-green-400 hover:text-green-300 text-sm px-1" title="Save">✓</button>
                <button onClick={() => setRenamingTitle(false)} className="text-gray-400 hover:text-gray-300 text-sm px-1" title="Cancel">✕</button>
              </span>
            ) : (
              s.name
            )}
          </h1>
          {s.ended_at === null && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-900 text-green-300 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              Active
            </span>
          )}
        </div>
        <div className="flex gap-2">
          {s.ended_at === null && (
            <button
              onClick={() => endSession.mutate(s.id)}
              disabled={endSession.isPending}
              className="px-3 py-1 text-sm bg-red-700 hover:bg-red-600 text-white rounded disabled:opacity-50"
            >
              End session
            </button>
          )}
          <button
            onClick={exportPdf}
            className="px-3 py-1 text-sm bg-indigo-700 hover:bg-indigo-600 text-white rounded"
          >
            Export PDF
          </button>
          <button
            onClick={startRename}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 text-white rounded"
          >
            Rename
          </button>
          <button
            onClick={() => {
              if (confirm(`Delete session "${s.name}"?`)) {
                deleteSession.mutate(s.id, { onSuccess: () => navigate('/sessions') });
              }
            }}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 text-red-400 rounded"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          ['Requests', st?.request_count ?? '—'],
          ['Tokens in', st ? st.tokens_total_input.toLocaleString() : '—'],
          ['Tokens out', st ? st.tokens_total_output.toLocaleString() : '—'],
          ['Started', new Date(s.started_at).toLocaleTimeString()],
        ].map(([label, value]) => (
          <div key={String(label)} className="bg-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</p>
            <p className="text-2xl font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Token composition</p>
          {st ? (
            <TokenDonut data={Object.fromEntries(Object.entries(st.by_category).map(([k, v]) => [k, v.tokens]))} />
          ) : (
            <div className="h-60 flex items-center justify-center text-gray-500 text-sm">No data</div>
          )}
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <TimeSeriesChart
            data={timeline.data?.timeline ?? []}
            bucket={bucket}
            onBucketChange={setBucket}
            loading={timeline.isLoading}
          />
        </div>
      </div>

      {/* Tool breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ToolBreakdownCharts tools={toolStats.data?.tools ?? []} />
        <ToolBreakdownTable tools={toolStats.data?.tools ?? []} totalInputTokens={st?.tokens_total_input} />
      </div>

      {/* Requests table */}
      <div className="bg-gray-800 rounded-lg p-4">
        <p className="text-sm font-medium text-gray-300 mb-4">Requests in this session</p>
        <RequestTable
          requests={requests.data?.requests ?? []}
          onRowClick={(reqId) => navigate(`/requests/${reqId}`)}
        />
      </div>
    </div>
  );
}
