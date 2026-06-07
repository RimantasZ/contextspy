# Install guide

There are several options to install and run ContextSpy profiler:
- install prebuilt binary
- install python package through PyPI
- build and run from source

## Installing binary release

Easiest and least complicated approach for general use

### MacOS - Homebrew

Easiest way to install on Mac is using Homebrew. This supports Apple Silicon only — for older models it is recommended to use PyPI or build from source.

Add contextspy tap and install using:

```bash
brew tap RimantasZ/contextspy
brew install contextspy
contextspy help
```
Next steps: install CA certificate, setup coding agent

### Linux (Ubuntu / Debian)

Download `contextspy_VERSION_amd64.deb` from the [latest release](https://github.com/RimantasZ/contextspy/releases/latest), then:

```bash
sudo dpkg -i contextspy_*_amd64.deb
contextspy help
```
Installs to `/usr/bin/contextspy`. Remove with `apt remove contextspy`.

Next steps: install CA certificate, setup coding agent

### Windows (x86_64)

No specific installer. Download a binary release from the section below, unzip the executable and run it locally. Add to PATH in environment variables manually, if needed.

```powershell
.\contextspy help
```
Next steps: install CA certificate, setup coding agent

### Download binary archive

Pre-built single-file executables are attached to each [GitHub release](https://github.com/RimantasZ/contextspy/releases).
Download and extract the archive for your platform, then run `./contextspy`.

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `contextspy-macos-arm64.tar.gz` |
| Linux x86_64 | `contextspy-linux-x86_64.tar.gz` |
| Windows  | `contextspy-windows-x86_64.zip` |

> **macOS Gatekeeper warning** — binaries downloaded directly from the internet are
> quarantined by MacOS. Remove the quarantine flag after extracting:
>
> ```bash
> xattr -d com.apple.quarantine ./contextspy
> ```
>
> Or right-click the binary in Finder → **Open** → **Open**. One-time step.
> To avoid this entirely, install via Homebrew instead.



## Install from PyPI 

Requires Python 3.11+ and pip

```bash
pip install contextspy
# or with uv (recommended):
uv tool install contextspy
```

After install, `contextspy` is available on your `PATH`.

On externally managed python installations (e.g. if python installed through Homebrew on MacOS, or Linux distributions), you may get a following error:
```
error: externally-managed-environment
```
This means it blocks `pip` installs to protect system stability. In this case you will need to create virtual environment, and run ContextSpy from it:
```
python3 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\activate
pip install contextspy
contextspy help
```

## From source

Requirements
- Python 3.11+
- Administrator / sudo access (for CA certificate installation, cloud mode only)
- Node.js 18+ and npm — only needed if you want to modify the frontend


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
