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
import { useState, useRef, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useSession, useStatsSession, useTimeline, useRequests, useEndSession, useToolStats, useRenameSession } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { TimeSeriesChart } from '../components/TimeSeriesChart';
import { RequestTable } from '../components/RequestTable';
import type { SortKey } from '../components/RequestTable';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';
import { DeleteSessionModal } from '../components/DeleteSessionModal';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';

type Bucket = 'minute' | 'hour' | 'day';

function fmtTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  const s = ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z';
  return new Date(s).toLocaleString();
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  const totalS = Math.floor(ms / 1000);
  if (totalS < 60) return `${totalS}s`;
  const m = Math.floor(totalS / 60);
  const s = totalS % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [bucket, setBucket] = useState<Bucket>('hour');
  const [renamingTitle, setRenamingTitle] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const renameTitleRef = useRef<HTMLInputElement>(null);

  const [reqSortKey, setReqSortKey] = useState<SortKey | null>(null);
  const [reqSortDir, setReqSortDir] = useState<'asc' | 'desc'>('asc');

  function handleReqSortChange(key: SortKey | null, dir: 'asc' | 'desc') {
    setReqSortKey(key);
    setReqSortDir(dir);
  }

  const session = useSession(id ?? '');
  const stats = useStatsSession(id ?? '');
  const timeline = useTimeline(id, bucket);
  const requests = useRequests({ session_id: id, sort_by: reqSortKey ?? undefined, sort_dir: reqSortKey ? reqSortDir : undefined, limit: 500 });
  const toolStats = useToolStats(id);
  const endSession = useEndSession();
  const renameSession = useRenameSession();
  const [deletingSession, setDeletingSession] = useState(false);

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
        ['Context tokens', (st?.tokens_total_input ?? 0).toLocaleString()],
        ['Generated tokens', (st?.tokens_total_output ?? 0).toLocaleString()],
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
            onClick={() => setDeletingSession(true)}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 text-red-400 rounded"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Timing panel */}
      <div className="bg-gray-800 rounded-lg p-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4 text-sm">
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">Session started</p>
            <p className="text-white font-medium">{fmtTime(s.started_at)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">Session closed</p>
            <p className="text-white font-medium">
              {s.ended_at ? fmtTime(s.ended_at) : <span className="text-green-400">Active</span>}
            </p>
          </div>
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">First request</p>
            <p className="text-white font-medium">{fmtTime(st?.session_timing?.first_request_at)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">Last request</p>
            <p className="text-white font-medium">{fmtTime(st?.session_timing?.last_request_at)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">Active duration</p>
            <p className="text-white font-medium">{fmtMs(st?.session_timing?.active_duration_ms)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">Elapsed time</p>
            <p className="text-white font-medium">{fmtMs(st?.session_timing?.elapsed_ms)}</p>
          </div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-3 gap-4">
        {[
          ['Context tokens', st ? st.tokens_total_input.toLocaleString() : '—'],
          ['Generated tokens', st ? st.tokens_total_output.toLocaleString() : '—'],
          ['Requests', st?.request_count ?? '—'],
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
          sortKey={reqSortKey}
          sortDir={reqSortDir}
          onSortChange={handleReqSortChange}
        />
      </div>

      {deletingSession && (
        <DeleteSessionModal
          sessionId={s.id}
          sessionName={s.name}
          onClose={() => setDeletingSession(false)}
          onDeleted={() => navigate('/sessions')}
        />
      )}
    </div>
  );
}
