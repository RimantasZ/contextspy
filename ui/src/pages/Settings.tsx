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
import { useState } from 'react';
import { useProxyStatus, useProxyStart, useProxyStop, useInstallCert } from '../api/hooks';

function Tab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-[var(--accent)] text-[var(--accent-soft-text)]'
          : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
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
      <div className="panel space-y-3 p-5">
        <h2 className="font-medium text-[var(--text)]">CA certificate</h2>
        <p className="text-sm text-[var(--text-muted)]">
          ContextSpy uses mitmproxy to intercept HTTPS traffic. Install the CA certificate
          to avoid SSL errors.
        </p>
        <p className="text-xs text-[var(--text-muted)]">
                CA cert: {status?.cert_installed ? '\u2714 installed' : '\u26A0 not installed'}
        </p>
        <button
          onClick={() =>
            installCert.mutate(undefined, {
              onSuccess: (d) => setCertMsg(d.message),
              onError: (e) => setCertMsg(String(e)),
            })
          }
          disabled={installCert.isPending}
          className="app-button-primary"
        >
          Install CA certificate
        </button>
        {certMsg && (
          <p className="code-surface rounded p-3 font-mono text-sm">{certMsg}</p>
        )}
      </div>
    </div>
  );
}

const CERT_PATH = '~/.mitmproxy/mitmproxy-ca-cert.pem';

type SetupStep = { text: string; code?: string[] };

function AgentSetupTab() {
  const { data: status } = useProxyStatus();
  const port = status?.port ?? 8888;

  // Cloud (forward-proxy) agents. Run `contextspy setup-<agent>` for the same instructions.
  const cloudAgents: { name: string; steps: SetupStep[] }[] = [
    {
      name: 'Claude Code / Anthropic API',
      steps: [
        { text: 'Install the ContextSpy CA certificate (Proxy tab above).' },
        {
          text: 'In the terminal where you launch claude, set the proxy and CA cert:',
          code: [
            '# PowerShell',
            `$env:HTTPS_PROXY = "http://127.0.0.1:${port}"`,
            `$env:NODE_EXTRA_CA_CERTS = "${CERT_PATH}"`,
            '$env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"',
            '',
            '# Bash / Zsh',
            `export HTTPS_PROXY=http://127.0.0.1:${port}`,
            `export NODE_EXTRA_CA_CERTS="${CERT_PATH}"`,
            'export NO_PROXY="github.com,localhost,127.0.0.1,::1"',
            'export no_proxy="github.com,localhost,127.0.0.1,::1"',
          ],
        },
        { text: 'NO_PROXY keeps git and other localhost traffic off the proxy.' },
      ],
    },
    {
      name: 'GitHub Copilot (VS Code)',
      steps: [
        { text: 'Install the ContextSpy CA certificate (Proxy tab above).' },
        {
          text: 'Add to VS Code settings.json (applies to all extensions):',
          code: [
            `"http.proxy": "http://127.0.0.1:${port}",`,
            '"http.proxyStrictSSL": false,',
            '"http.noProxy": ["github.com", "localhost", "127.0.0.1"]',
          ],
        },
        {
          text: 'Or set environment variables in the terminal where VS Code runs:',
          code: [
            '# PowerShell',
            `$env:HTTPS_PROXY = "http://127.0.0.1:${port}"`,
            `$env:NODE_EXTRA_CA_CERTS = "${CERT_PATH}"`,
            '$env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"',
            '',
            '# Bash / Zsh',
            `export HTTPS_PROXY=http://127.0.0.1:${port}`,
            `export NODE_EXTRA_CA_CERTS="${CERT_PATH}"`,
            'export NO_PROXY="github.com,localhost,127.0.0.1,::1"',
            'export no_proxy="github.com,localhost,127.0.0.1,::1"',
          ],
        },
      ],
    },
    {
      name: 'opencode',
      steps: [
        { text: 'Install the ContextSpy CA certificate (Proxy tab above).' },
        {
          text: 'In the terminal where you launch opencode, set:',
          code: [
            '# PowerShell',
            `$env:HTTPS_PROXY = "http://127.0.0.1:${port}"`,
            `$env:SSL_CERT_FILE = "${CERT_PATH}"`,
            `$env:NODE_EXTRA_CA_CERTS = "${CERT_PATH}"`,
            '$env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"',
            '',
            '# Bash / Zsh',
            `export HTTPS_PROXY=http://127.0.0.1:${port}`,
            `export SSL_CERT_FILE="${CERT_PATH}"`,
            `export NODE_EXTRA_CA_CERTS="${CERT_PATH}"`,
            'export NO_PROXY="github.com,localhost,127.0.0.1,::1"',
            'export no_proxy="github.com,localhost,127.0.0.1,::1"',
          ],
        },
        {
          text: 'Or configure the proxy in ~/.config/opencode/config.json:',
          code: [
            '{',
            `  "proxy": "http://127.0.0.1:${port}"`,
            '}',
          ],
        },
      ],
    },
  ];

  // Local LLM servers use reverse-proxy mode — no CA certificate, plain HTTP on localhost.
  const localAgents: { name: string; serverPort: number; listenPort: number; launch?: string }[] = [
    { name: 'llama-server', serverPort: 8080, listenPort: 8889, launch: 'llama-server -m your-model.gguf --port 8080' },
    { name: 'Ollama', serverPort: 11434, listenPort: 8890 },
    { name: 'vLLM', serverPort: 8000, listenPort: 8891, launch: 'vllm serve your-model --port 8000' },
  ];

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div>
          <h2 className="font-medium text-[var(--text)]">Cloud APIs (forward proxy)</h2>
          <p className="text-sm text-[var(--text-muted)]">
            Proxy currently listens on <span className="font-mono text-[var(--text)]">127.0.0.1:{port}</span>.
            Run <span className="font-mono text-[var(--text)]">contextspy setup-&lt;agent&gt;</span> in a
            terminal for the same instructions.
          </p>
        </div>
        {cloudAgents.map((a) => (
          <div key={a.name} className="panel space-y-3 p-5">
            <h3 className="font-medium text-[var(--text)]">{a.name}</h3>
            <ol className="list-decimal list-inside space-y-2">
              {a.steps.map((step, i) => (
                <li key={i} className="text-sm text-[var(--text-muted)]">
                  {step.text}
                  {step.code && (
                    <pre className="code-surface ml-5 mt-1 overflow-x-auto rounded p-3 font-mono text-xs whitespace-pre">
                      {step.code.join('\n')}
                    </pre>
                  )}
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>

      <div className="space-y-4">
        <div>
          <h2 className="font-medium text-[var(--text)]">Local LLM servers (reverse proxy)</h2>
          <p className="text-sm text-[var(--text-muted)]">
            Loopback traffic bypasses HTTPS_PROXY, so local servers use reverse-proxy mode instead.
            No CA certificate needed. Add a <span className="font-mono text-[var(--text)]">[[reverse_targets]]</span>{' '}
            block to <span className="font-mono text-[var(--text)]">~/.contextspy/config.toml</span>, then run{' '}
            <span className="font-mono text-[var(--text)]">contextspy start-local</span> and point your
            client's base URL at ContextSpy.
          </p>
        </div>
        {localAgents.map((a) => (
          <div key={a.name} className="panel space-y-3 p-5">
            <h3 className="font-medium text-[var(--text)]">{a.name}</h3>
            <pre className="code-surface overflow-x-auto rounded p-3 font-mono text-xs whitespace-pre">
              {[
                '[[reverse_targets]]',
                `name        = "${a.name.toLowerCase()}"`,
                `listen_port = ${a.listenPort}   # contextspy listens here`,
                `target_url  = "http://127.0.0.1:${a.serverPort}"  # your ${a.name} port`,
                'provider    = "openai"   # OpenAI-compatible API',
              ].join('\n')}
            </pre>
            <p className="text-sm text-[var(--text-muted)]">
              Point your client's base URL to{' '}
              <span className="font-mono text-[var(--text)]">http://127.0.0.1:{a.listenPort}/v1</span>{' '}
              (instead of <span className="font-mono text-[var(--text)]">:{a.serverPort}/v1</span>).
            </p>
            {a.launch && (
              <p className="text-sm text-[var(--text-muted)]">
                Launch as usual: <span className="font-mono text-[var(--text)]">{a.launch}</span>
              </p>
            )}
            {a.name === 'Ollama' && (
              <p className="text-sm text-[var(--text-muted)]">
                Alternatively, run <span className="font-mono text-[var(--text)]">contextspy start</span>{' '}
                (cloud mode) — Ollama on port 11434 is auto-detected by the forward proxy.
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Settings() {
  const [tab, setTab] = useState<'proxy' | 'agents'>('proxy');

  return (
    <div className="page-shell">
      <h1 className="text-2xl font-bold text-[var(--text)]">Settings</h1>

      <div className="flex gap-1 border-b border-[var(--border)]">
        <Tab label="Proxy" active={tab === 'proxy'} onClick={() => setTab('proxy')} />
        <Tab label="Agent setup" active={tab === 'agents'} onClick={() => setTab('agents')} />
      </div>

      <div className="pt-2">
        {tab === 'proxy' ? <ProxyTab /> : <AgentSetupTab />}
      </div>
    </div>
  );
}
