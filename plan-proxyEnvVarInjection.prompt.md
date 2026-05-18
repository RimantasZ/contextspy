# Plan: Proxy Env-Var Injection

## Problem

VS Code's `http.proxy` setting is insufficient for Copilot — the extension host is a
separate Node.js process that only reads `HTTPS_PROXY` and `NODE_EXTRA_CA_CERTS` from
the environment. Users currently must set these manually before every VS Code launch.

---

## Option A — `contextspy code` subcommand (recommended)

Launches VS Code as a subprocess with proxy vars injected. Vars are scoped to VS Code
only; nothing to clean up when VS Code exits.

### Usage

```bash
contextspy code .
contextspy code /path/to/project
contextspy code                   # no path
```

### What to implement

**File: `contextspy/cli.py`** — add `@app.command("code")` near the `setup-*` group:

```python
@app.command("code")
def launch_code(
    paths: list[str] = typer.Argument(default=None),
    proxy_port: int = typer.Option(8888, "--proxy-port", help="Override proxy port"),
) -> None:
    """Launch VS Code with HTTPS_PROXY and NODE_EXTRA_CA_CERTS pre-set."""
    import os, subprocess, pathlib
    from contextspy.config import Settings

    settings = Settings.load()
    port = proxy_port or settings.proxy.port
    cert = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    env = os.environ.copy()
    env["HTTPS_PROXY"] = f"http://127.0.0.1:{port}"
    env["https_proxy"] = env["HTTPS_PROXY"]
    if cert.exists():
        env["NODE_EXTRA_CA_CERTS"] = str(cert)
    else:
        console.print(f"[yellow]Warning:[/yellow] cert not found at {cert}. "
                      "Run [bold]contextspy install-cert[/bold] first.")

    cmd = ["code", *paths] if paths else ["code"]
    console.print(f"[dim]Launching: {' '.join(cmd)} (HTTPS_PROXY={env['HTTPS_PROXY']})[/dim]")
    result = subprocess.run(cmd, env=env)
    raise typer.Exit(result.returncode)
```

- Add `("code", "Launch VS Code with proxy env vars set")` to the `help_cmd` rows table.

### Pros / cons
- ✅ Cross-platform identical syntax on Windows, macOS, Linux
- ✅ Vars scoped to VS Code only; no cleanup needed
- ✅ `--proxy-port` override for non-default setups
- ⚠️ Requires VS Code `code` CLI to be on PATH (VS Code "Install 'code' command in PATH")

---

## Option B — `contextspy env` subcommand (shell eval)

Prints shell export/unset statements for the current shell session to evaluate.

### Usage

```bash
# bash / zsh
eval "$(contextspy env)"           # set vars in current shell
eval "$(contextspy env --unset)"   # clear them

# PowerShell
contextspy env --shell pwsh | Invoke-Expression
contextspy env --shell pwsh --unset | Invoke-Expression
```

### What to implement

**File: `contextspy/cli.py`** — add `@app.command("env")`:

```python
@app.command("env")
def print_env(
    unset: bool = typer.Option(False, "--unset"),
    shell: str = typer.Option("posix", "--shell", help="posix | pwsh | fish"),
    proxy_port: int = typer.Option(8888, "--proxy-port"),
) -> None:
    """Print shell commands to export (or unset) proxy env vars. Use with eval."""
    import pathlib
    from contextspy.config import Settings

    settings = Settings.load()
    port = proxy_port or settings.proxy.port
    cert = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    proxy_val = f"http://127.0.0.1:{port}"

    if unset:
        if shell == "pwsh":
            print('Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue')
            print('Remove-Item Env:NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue')
        elif shell == "fish":
            print('set -e HTTPS_PROXY; set -e NODE_EXTRA_CA_CERTS')
        else:
            print('unset HTTPS_PROXY https_proxy NODE_EXTRA_CA_CERTS')
    else:
        if shell == "pwsh":
            print(f'$env:HTTPS_PROXY = "{proxy_val}"')
            print(f'$env:NODE_EXTRA_CA_CERTS = "{cert}"')
        elif shell == "fish":
            print(f'set -x HTTPS_PROXY "{proxy_val}"')
            print(f'set -x NODE_EXTRA_CA_CERTS "{cert}"')
        else:
            print(f'export HTTPS_PROXY="{proxy_val}"')
            print(f'export https_proxy="{proxy_val}"')
            print(f'export NODE_EXTRA_CA_CERTS="{cert}"')
```

**IMPORTANT:** use plain `print()` for all output — `console.print()` would corrupt
the `eval` input with Rich markup / ANSI codes.

### Pros / cons
- ✅ Sets vars for whole shell session — all subsequent `code`, `claude`, `python` inherit them
- ✅ User controls precisely when to set/unset
- ⚠️ Different incantation per shell (bash vs PowerShell vs fish)
- ⚠️ Easy to forget to unset — vars persist after ContextSpy stops

---

## Option C — Shell profile function (printed by `setup-copilot`)

No new CLI command. Extend `setup-copilot`, `setup-claude`, and `setup-opencode` to
also print a ready-to-paste shell function. The function wraps `code`/`claude`/`opencode`,
probes ContextSpy's health endpoint, and conditionally injects the proxy vars — so it
is fully transparent when ContextSpy is not running.

### bash / zsh snippet (for `setup-copilot`)

```bash
# Add to ~/.zshrc or ~/.bashrc (one-time setup):
code() {
  if curl -sf --max-time 0.5 http://127.0.0.1:5173/api/stats >/dev/null 2>&1; then
    HTTPS_PROXY=http://127.0.0.1:8888 \
    NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem \
    command code "$@"
  else
    command code "$@"
  fi
}
```

### PowerShell snippet (for `setup-copilot`)

```powershell
# Add to $PROFILE (one-time setup):
function Invoke-Code {
  param([Parameter(ValueFromRemainingArguments)]$Rest)
  try {
    $null = Invoke-WebRequest http://127.0.0.1:5173/api/stats -TimeoutSec 1 -ErrorAction Stop
    $env:HTTPS_PROXY = "http://127.0.0.1:8888"
    $env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
    & code @Rest
    Remove-Item Env:HTTPS_PROXY, Env:NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue
  } catch {
    & code @Rest
  }
}
Set-Alias code Invoke-Code
```

### What to implement

**File: `contextspy/cli.py`** — in `setup_copilot()`, `setup_claude()`, `setup_opencode()`:
After the existing env-var block, add a "Shell profile (one-time setup)" section that
prints the appropriate function snippet. The health endpoint to probe is
`http://127.0.0.1:{settings.web.port}/api/stats`.

### Pros / cons
- ✅ Fully transparent — correct behaviour whether or not ContextSpy is running
- ✅ No new commands to learn
- ⚠️ One-time shell profile edit required per machine
- ⚠️ ~0.5 s probe delay on each VS Code / claude launch (curl with `--max-time 0.5`)
- ⚠️ curl required (pre-installed macOS/Linux; available Windows 10+)

---

## Files to modify (all options)

| File | Change |
|------|--------|
| `contextspy/cli.py` | New command(s) and/or extended `setup-*` output |
| `contextspy/cli.py` | New row(s) in `help_cmd` table (Options A and B) |
| `README.md` | New CLI reference entry (Options A and B) |

## Recommendation

Implement **Option A** (`contextspy code`) as the primary cross-platform UX, and also
add the **Option C** shell function snippets to `setup-copilot` / `setup-claude` /
`setup-opencode` for users who prefer a one-time profile setup.
Option B can be deferred — it overlaps with A and C.
