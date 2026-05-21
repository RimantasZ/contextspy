# Installation

## Requirements

- Python 3.11+
- Administrator / sudo access (for CA certificate installation, cloud mode only)
- Node.js 18+ and npm — only needed if you want to modify the frontend

---

## From PyPI

```bash
pip install contextspy
# or with uv (recommended):
uv tool install contextspy
```

After install, `contextspy` is available on your `PATH`.

---

## Via Homebrew (macOS)

```bash
brew install rimantas/contextspy/contextspy
```

Homebrew removes the macOS quarantine flag automatically — no Gatekeeper warning.

---

## Via `.deb` package (Ubuntu / Debian)

Download `contextspy_VERSION_amd64.deb` from the [latest release](https://github.com/RimantasZ/contextspy/releases/latest), then:

```bash
sudo dpkg -i contextspy_*_amd64.deb
```

Installs to `/usr/bin/contextspy`. Remove with `apt remove contextspy`.

---

## Binary releases (all platforms)

Pre-built single-file executables are attached to each [GitHub release](https://github.com/RimantasZ/contextspy/releases).
Download and extract the archive for your platform, then run `./contextspy`.

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `contextspy-macos-arm64.tar.gz` |
| Linux x86_64 | `contextspy-linux-x86_64.tar.gz` |
| Windows x86_64 | `contextspy-windows-x86_64.zip` |

> **macOS Gatekeeper warning** — binaries downloaded directly from the internet are
> quarantined by macOS. Remove the quarantine flag after extracting:
>
> ```bash
> xattr -d com.apple.quarantine ./contextspy
> ```
>
> Or right-click the binary in Finder → **Open** → **Open**. One-time step.
> To avoid this entirely, install via Homebrew instead.

---

## From source

```bash
git clone https://github.com/RimantasZ/contextspy.git
cd contextspy
uv venv
uv pip install -e .
```

---

## CA certificate setup (cloud/forward proxy mode only)

Cloud mode intercepts HTTPS traffic, which requires installing a local CA certificate
into your OS trust store once. This is not needed for local LLM mode.

**Step 1 — Generate the certificate** (mitmproxy creates it on first run):

```bash
contextspy start --no-browser
# wait a few seconds, then Ctrl+C
```

**Step 2 — Install into OS trust store:**

*macOS:*
```bash
contextspy install-cert
# or manually:
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  ~/.mitmproxy/mitmproxy-ca-cert.pem
```

*Ubuntu / Linux:*
```bash
contextspy install-cert
# or manually:
sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy-ca.crt
sudo update-ca-certificates
```

*Windows (elevated PowerShell):*
```powershell
contextspy install-cert
# or manually:
certutil -addstore -f Root "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
```

You can also install from the dashboard → **Settings** → **CA Certificate** tab.

> **One-time operation.** The certificate persists across restarts. Re-run only if you
> delete `~/.mitmproxy/` or reinstall the OS.

**Step 3 — Node.js-based tools** (VS Code / Copilot, Claude CLI, opencode) have their
own bundled certificate store and ignore the OS trust store. Set this before launching:

```bash
# macOS / Linux
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem

# PowerShell
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
```

The `contextspy setup-copilot`, `setup-claude`, and `setup-opencode` commands print the
exact snippet for your shell.
