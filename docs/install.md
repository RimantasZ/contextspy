# Install guide

To start using ContextSpy, three steps are required:

1. Install contextspy from the binary release, PyPI, or build from source
2. [Install CA certificate](#ca-certificate-setup-cloudforward-proxy-mode-only), if you are planning to profile with AI cloud providers (not required for local models)
3. Setup coding agent - for [cloud](cloud-mode.md) or [local](local-mode.md) llms

## Installing ContextSpy

There are several options to install and run ContextSpy profiler:
- install prebuilt binary
- install python package through PyPI
- build and run from source

## Installing binary release

The easiest and least complicated approach for general use

### macOS - Homebrew

The easiest way to install on Mac is using Homebrew. This supports Apple Silicon only — for older models it is recommended to use PyPI or build from source.

Add contextspy tap and install using:

```bash
brew tap RimantasZ/contextspy
brew install contextspy
contextspy help
```
Next steps: [install CA certificate](#ca-certificate-setup-cloudforward-proxy-mode-only), [setup coding agent](cloud-mode.md)

### Linux (Ubuntu / Debian)

Download `contextspy_VERSION_amd64.deb` from the [latest release](https://github.com/RimantasZ/contextspy/releases/latest), then:

```bash
sudo dpkg -i contextspy_*_amd64.deb
contextspy help
```
Installs to `/usr/bin/contextspy`. Remove with `apt remove contextspy`.

Next steps: [install CA certificate](#ca-certificate-setup-cloudforward-proxy-mode-only), [setup coding agent](cloud-mode.md)

### Windows (x86_64)

No specific installer. Download a binary release from the section below, unzip the executable and run it locally. Add to PATH in environment variables manually, if needed.

```powershell
.\contextspy help
```
In some cases, Windows Defender or antivirus software might flag the release binary as a threat - as they don't like that it bundles mitmproxy library for creating proxy connections. If you cannot make an exception, the only other option is installing as a [PyPI package](#install-from-pypi)

Next steps: [install CA certificate](#ca-certificate-setup-cloudforward-proxy-mode-only), [setup coding agent](cloud-mode.md)

### Download binary archive

Pre-built single-file executables are attached to each [GitHub release](https://github.com/RimantasZ/contextspy/releases).
Download and extract the archive for your platform, then run `./contextspy`.

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `contextspy-macos-arm64.tar.gz` |
| Linux x86_64 | `contextspy-linux-x86_64.tar.gz` |
| Windows  | `contextspy-windows-x86_64.zip` |

> **macOS Gatekeeper warning** — binaries downloaded directly from the internet are
> quarantined by macOS. Remove the quarantine flag after extracting:
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

On externally managed Python installations (e.g. if Python is installed through Homebrew on macOS, or Linux distributions), you may get the following error:
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

### Install the certificate

Run this command once after installation:

*macOS / Linux:*
```bash
sudo contextspy install-cert
```

*Windows (elevated PowerShell):*
```powershell
contextspy install-cert
```

This generates the CA key and certificate in `~/.mitmproxy/` and installs them into
the system trust store. Files are always owned by your user account, even when run
with `sudo`.

> **No sudo / no admin rights?** Run `contextspy install-cert` without elevated privileges.
> The cert will be generated but not installed into the system trust store — you'll see
> the manual command to run. For `contextspy run` (the recommended way to launch tools),
> `NODE_EXTRA_CA_CERTS` is set automatically so most setups work without the system install.

> **One-time operation.** The certificate persists across restarts. Re-run only if you
> delete `~/.mitmproxy/` or need to reset the trust store.

You can also install from the dashboard → **Settings** → **CA Certificate** tab.

### Start the proxy

```bash
contextspy start
```

The proxy listens on port 8888 and the dashboard on port 5173 by default.

### Launch your coding agent

Use `contextspy run` to launch your tool — it automatically sets `HTTPS_PROXY` and
`NODE_EXTRA_CA_CERTS` for you:

```bash
contextspy run claude <path to your project>
contextspy run code <path to your project>
contextspy run opencode <path to your project>
```

If you prefer to launch your tool manually, set these environment variables first:

```bash
# macOS / Linux
export HTTPS_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem

# PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:8888"
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
```

The `contextspy setup-copilot`, `setup-claude`, and `setup-opencode` commands print the
exact snippet for your shell.

---

## Troubleshooting

### `contextspy start` prints "CA cert not installed" in yellow

The cert was just generated but could not be installed into the system trust store
(sudo required on macOS/Linux). Run the install command to complete setup:

```bash
sudo contextspy install-cert   # macOS / Linux
```

If you only use `contextspy run` to launch your tools, this step is optional —
`contextspy run` sets `NODE_EXTRA_CA_CERTS` automatically.

### `contextspy start` prints "CA certificate error" in red and exits

The CA key cannot be read or is corrupted. The most common cause is a previous
`sudo` run that left root-owned files in `~/.mitmproxy/`. Fix:

```bash
sudo chown -R $USER ~/.mitmproxy/
contextspy start
```

If chown does not help (files are corrupted rather than just mis-owned):

```bash
sudo rm -rf ~/.mitmproxy/
contextspy install-cert
sudo contextspy install-cert   # to reinstall into system trust store
```

### `contextspy run` prints "Error: CA cert not found"

The cert file is missing. Regenerate it:

```bash
contextspy install-cert
sudo contextspy install-cert   # to also install into system trust store
```

### `contextspy run` prints "Error: ContextSpy is not running"

The proxy must be started before launching a tool. Open a separate terminal and run:

```bash
contextspy start
```

Then retry `contextspy run` in your original terminal.

### TLS errors in VS Code / Copilot when setting env vars manually

If VS Code was already open when you set `HTTPS_PROXY`, it reuses the existing process
and the new env vars never reach the extension host. Quit VS Code completely
(Cmd+Q on macOS, not just closing the window), then use `contextspy run code .` to
launch it fresh with the correct environment.

### All HTTPS requests fail with `SSL_ERROR_SYSCALL` through the proxy

The proxy is running but mitmproxy cannot complete TLS interception. Check:

```bash
ls -la ~/.mitmproxy/
```

- If `mitmproxy-ca.pem` is missing → run `contextspy install-cert` to regenerate
- If files are owned by `root` → run `sudo chown -R $USER ~/.mitmproxy/`
- If both files exist and are readable → check contextspy's terminal output for a
  more specific error from mitmproxy
