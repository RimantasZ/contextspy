import { useState } from 'react';
import { useProxyStatus, useProxyStart, useProxyStop, useInstallCert } from '../api/hooks';

function Tab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-indigo-500 text-indigo-400'
          : 'border-transparent text-gray-400 hover:text-gray-300'
      }`}
    >
      {label}
    </button>
  );
}

function ProxyTab() {
  const { data: status, isLoading } = useProxyStatus();
  const startProxy = useProxyStart();
  const stopProxy = useProxyStop();
  const installCert = useInstallCert();
  const [certMsg, setCertMsg] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div className="bg-gray-800 rounded-lg p-5 space-y-4">
        <h2 className="text-white font-medium">Proxy status</h2>
        {isLoading ? (
          <p className="text-gray-400 text-sm">Loading\u2026</p>
        ) : (
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    status?.running ? 'bg-green-400' : 'bg-gray-500'
                  }`}
                />
                <span className="text-white text-sm">
                  {status?.running ? `Running on port ${status.port}` : 'Stopped'}
                </span>
              </div>
              <p className="text-xs text-gray-400">
                CA cert: {status?.cert_installed ? '\u2714 installed' : '\u26A0 not installed'}
              </p>
            </div>
            <div className="flex gap-2">
              {status?.running ? (
                <button
                  onClick={() => stopProxy.mutate()}
                  disabled={stopProxy.isPending}
                  className="px-3 py-1 text-sm bg-red-700 hover:bg-red-600 text-white rounded disabled:opacity-50"
                >
                  Stop proxy
                </button>
              ) : (
                <button
                  onClick={() => startProxy.mutate()}
                  disabled={startProxy.isPending}
                  className="px-3 py-1 text-sm bg-green-700 hover:bg-green-600 text-white rounded disabled:opacity-50"
                >
                  Start proxy
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="bg-gray-800 rounded-lg p-5 space-y-3">
        <h2 className="text-white font-medium">CA certificate</h2>
        <p className="text-sm text-gray-400">
          Token-Scrooge uses mitmproxy to intercept HTTPS traffic. Install the CA certificate
          to avoid SSL errors.
        </p>
        <button
          onClick={() =>
            installCert.mutate(undefined, {
              onSuccess: (d) => setCertMsg(d.message),
              onError: (e) => setCertMsg(String(e)),
            })
          }
          disabled={installCert.isPending}
          className="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 text-white rounded disabled:opacity-50"
        >
          Install CA certificate
        </button>
        {certMsg && (
          <p className="text-sm text-gray-300 bg-gray-900 rounded p-3 font-mono">{certMsg}</p>
        )}
      </div>
    </div>
  );
}

function AgentSetupTab() {
  const agents = [
    {
      name: 'GitHub Copilot',
      instructions: [
        'Set system proxy to http://127.0.0.1:8080 in VS Code settings or your OS network settings.',
        'Install the Token-Scrooge CA certificate (Proxy tab above).',
        'VS Code setting: "http.proxy": "http://127.0.0.1:8080"',
        '"http.proxyStrictSSL": false is NOT needed if cert is installed correctly.',
      ],
    },
    {
      name: 'Claude / Anthropic API',
      instructions: [
        'Set HTTPS_PROXY=http://127.0.0.1:8080 in the environment where you run your code.',
        'Install the CA cert (Proxy tab) or set SSL_CERT_FILE to the mitmproxy CA path.',
        'On macOS/Linux: export HTTPS_PROXY=http://127.0.0.1:8080',
        'On Windows: $env:HTTPS_PROXY="http://127.0.0.1:8080"',
      ],
    },
    {
      name: 'Ollama',
      instructions: [
        'Ollama communicates over HTTP on localhost — no proxy needed for local models.',
        'For remote Ollama instances, set HTTPS_PROXY or HTTP_PROXY as above.',
      ],
    },
  ];

  return (
    <div className="space-y-4">
      {agents.map((a) => (
        <div key={a.name} className="bg-gray-800 rounded-lg p-5 space-y-3">
          <h2 className="text-white font-medium">{a.name}</h2>
          <ol className="list-decimal list-inside space-y-1">
            {a.instructions.map((line, i) => (
              <li key={i} className="text-sm text-gray-400">{line}</li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

export default function Settings() {
  const [tab, setTab] = useState<'proxy' | 'agents'>('proxy');

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      <div className="flex gap-1 border-b border-gray-700">
        <Tab label="Proxy" active={tab === 'proxy'} onClick={() => setTab('proxy')} />
        <Tab label="Agent setup" active={tab === 'agents'} onClick={() => setTab('agents')} />
      </div>

      <div className="pt-2">
        {tab === 'proxy' ? <ProxyTab /> : <AgentSetupTab />}
      </div>
    </div>
  );
}
