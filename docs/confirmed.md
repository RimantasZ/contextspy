# Confirmed Configurations

This page lists confirmed and tested environments (coding agent + provider API + OS).

Combinations that are *not* listed here are not necessarily broken — they simply haven't been
verified yet. If you get one working, please open an issue or PR so it can be added.

<table>
  <thead>
    <tr>
      <th>Coding agent</th>
      <th>Provider API</th>
      <th>OS</th>
      <th>Notes / specifics</th>
    </tr>
  </thead>
  <tbody>
    <!-- Claude Code -->
    <tr>
      <td rowspan="1"><strong>Claude Code</strong><br><em>(claude CLI)</em></td>
      <td>Anthropic<br><code>api.anthropic.com</code></td>
      <td>macOS<br>Windows</td>
      <td>
        Cloud (forward proxy) mode — <code>contextspy start</code>, then
        <code>contextspy setup-claude</code> or <code>contextspy run claude &lt;path&gt;</code>.<br><br>
        <code>NODE_EXTRA_CA_CERTS</code> is <strong>required</strong> — Claude Code is a Node app and
        ignores the OS trust store, so <code>contextspy install-cert</code> alone is not enough.<br><br>
        Set <code>NO_PROXY="github.com,localhost,127.0.0.1,::1"</code> so git and telemetry don't get
        routed through the proxy.<br><br>
        <strong>Thinking tokens:</strong> counts are captured automatically. To also capture the
        reasoning <em>text</em> (Thinking tab), add
        <code>{ "showThinkingSummaries": true }</code> to <code>~/.claude/settings.json</code>.
      </td>
    </tr>
    <!-- GitHub Copilot -->
    <tr>
      <td rowspan="1"><strong>GitHub Copilot</strong><br><em>(VS Code)</em></td>
      <td>GitHub Copilot API<br><code>api.githubcopilot.com</code></td>
      <td>macOS<br>Windows</td>
      <td>
        Cloud mode. Either set <code>http.proxy</code> / <code>http.proxyStrictSSL: false</code> in VS Code
        <code>settings.json</code>, or export <code>HTTPS_PROXY</code> + <code>NODE_EXTRA_CA_CERTS</code>
        before launching VS Code (<code>contextspy setup-copilot</code>).<br><br>
        VS Code must be <strong>fully quit</strong> (Cmd+Q / exit from the tray) before relaunching — a
        running instance won't pick up new proxy env vars.<br><br>
        Add <code>"http.noProxy": ["github.com", "localhost", "127.0.0.1"]</code> to keep git auth working.
      </td>
    </tr>
    <!-- opencode -->
    <tr>
      <td rowspan="2"><strong>opencode</strong></td>
      <td>Anthropic<br><code>api.anthropic.com</code></td>
      <td rowspan="2">macOS<br>Windows</td>
      <td rowspan="2">
        Cloud mode — <code>contextspy setup-opencode</code> or <code>contextspy run opencode .</code>.<br><br>
        opencode needs <strong>both</strong> <code>SSL_CERT_FILE</code> (Go TLS stack) and
        <code>NODE_EXTRA_CA_CERTS</code> (Node components) pointing at
        <code>~/.mitmproxy/mitmproxy-ca-cert.pem</code>.<br><br>
        Alternatively set <code>{"proxy": "http://127.0.0.1:8888"}</code> in
        <code>~/.config/opencode/config.json</code>.<br><br>
        The <code>opencode.ai</code> zen gateway is detected as its own provider; requests are parsed by
        wire format (<code>/zen/v1/messages</code> → Anthropic parser,
        <code>/zen/v1/chat/completions</code> → OpenAI parser), so the breakdown is identical either way.
      </td>
    </tr>
    <tr>
      <td>opencode zen gateway<br><code>opencode.ai</code></td>
    </tr>
    <!-- Codex CLI -->
    <tr>
      <td rowspan="2"><strong>Codex CLI</strong><br><em>(terminal tool only)</em></td>
      <td>OpenAI<br><code>api.openai.com</code><br><em>(API-key login)</em></td>
      <td>macOS<br>Windows</td>
      <td>
        Cloud mode — <code>contextspy run codex .</code> (preferred) or
        <code>contextspy setup-codex</code>.<br><br>
        <strong>Codex does not read <code>~/.codex/.env</code> for proxy settings</strong> — it only inherits
        the environment of the shell that launches it. Export <code>HTTPS_PROXY</code> and
        <code>NO_PROXY</code> in that shell (or use <code>contextspy run</code>); putting them in a dotfile
        or in <code>~/.codex/config.toml</code> has no effect.<br><br>
        No <code>NODE_EXTRA_CA_CERTS</code> needed — Codex is a Rust binary and uses the OS trust store,
        so <code>contextspy install-cert</code> is sufficient.
      </td>
    </tr>
    <tr>
      <td>ChatGPT backend<br><code>chatgpt.com/backend-api/codex/responses</code><br><em>(ChatGPT plan login)</em></td>
      <td>macOS<br>Windows</td>
      <td>
        Same proxy setup as above. On a ChatGPT plan Codex uses a <strong>WebSocket</strong> transport;
        ContextSpy captures it natively — those turns show a <strong>WS</strong> badge instead of an HTTP
        status code, with full token counts and category breakdown.<br><br>
        The old <code>chatgpt_http</code> <code>model_provider</code> workaround in
        <code>~/.codex/config.toml</code> is no longer needed (harmless if left in place).
      </td>
    </tr>
    <!-- Python / OpenAI SDK -->
    <tr>
      <td rowspan="3"><strong>Python scripts</strong><br><em>(OpenAI SDK / httpx)</em></td>
      <td>llama.cpp<br><code>llama-server</code></td>
      <td rowspan="3">macOS<br>Windows</td>
      <td rowspan="3">
        Local (reverse proxy) mode — <code>contextspy start-local</code>. <strong>No CA certificate
        needed</strong>; loopback traffic bypasses <code>HTTPS_PROXY</code> entirely, which is why forward-proxy
        mode can't be used here.<br><br>
        Add one <code>[[reverse_targets]]</code> block per server to <code>~/.contextspy/config.toml</code>
        with <code>provider = "openai"</code>, then point the client's <code>base_url</code> at ContextSpy's
        <code>listen_port</code> instead of the server's own port. Defaults:
        llama-server 8080 → 8889, Ollama 11434 → 8890, vLLM 8000 → 8891
        (<code>contextspy setup-llamaserver</code> / <code>-ollama</code> / <code>-vllm</code>).<br><br>
        All three expose the OpenAI-compatible <code>/v1/chat/completions</code> endpoint
        (Ollama ≥ 0.1.24). Ollama's raw <code>/api/generate</code> and <code>/api/chat</code> endpoints are
        <strong>not</strong> parsed.<br><br>
        For <em>cloud</em> APIs from Python, note that httpx verifies against certifi and ignores
        <code>SSL_CERT_FILE</code> — use <code>contextspy inject-cert</code> or pass an explicit
        <code>httpx.Client(proxy=..., verify=...)</code> (see <code>contextspy setup-python</code>).
      </td>
    </tr>
    <tr>
      <td>Ollama<br><code>/v1/chat/completions</code></td>
    </tr>
    <tr>
      <td>vLLM</td>
    </tr>
  </tbody>
</table>

## Notes on OS coverage

- **macOS** — Gatekeeper quarantines binaries downloaded from GitHub releases; run
  `xattr -d com.apple.quarantine` on the extracted files. `sudo` is required for
  `contextspy install-cert`.
- **Windows** — `contextspy install-cert` must be run from an **elevated** prompt. Windows Defender
  may flag the release binary because it bundles mitmproxy; install from PyPI if you can't add an
  exception. Use the PowerShell (`$env:...`) form of every env var shown above.
- **Linux** — not yet verified end-to-end, though installation is documented
  (see [Installation](install.md)) and nothing is known to be platform-specific.
