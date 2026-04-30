import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useSession, useStatsSession, useTimeline, useRequests, useEndSession, useDeleteSession } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { TimeSeriesChart } from '../components/TimeSeriesChart';
import { RequestTable } from '../components/RequestTable';

type Bucket = 'minute' | 'hour' | 'day';

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [bucket, setBucket] = useState<Bucket>('hour');

  const session = useSession(id ?? '');
  const stats = useStatsSession(id ?? '');
  const timeline = useTimeline(id, bucket);
  const requests = useRequests({ session_id: id, limit: 50 });
  const endSession = useEndSession();
  const deleteSession = useDeleteSession();

  if (session.isLoading) return <div className="p-6 text-gray-400">Loading\u2026</div>;
  if (!session.data) return <div className="p-6 text-red-400">Session not found.</div>;

  const s = session.data.session;
  const st = stats.data;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/sessions')} className="text-gray-400 hover:text-white text-sm">
            ← Sessions
          </button>
          <h1 className="text-xl font-bold text-white">{s.name}</h1>
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
