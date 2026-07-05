# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Typer CLI entry point for ContextSpy."""

from __future__ import annotations

import importlib.metadata
import os
import pathlib
import subprocess
import webbrowser
from typing import Callable, Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table


def _version_callback(value: bool) -> None:
    if value:
        version = importlib.metadata.version("contextspy")
        typer.echo(version)
        raise typer.Exit()


app = typer.Typer(
    name="contextspy",
    help="LLM context window analyser and proxy.",
    no_args_is_help=True,
)


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


session_app = typer.Typer(help="Manage named sessions.")
app.add_typer(session_app, name="session")

console = Console()


def _api(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}/api{path}"


def _web_port() -> int:
    from contextspy.config import Settings

    return Settings.load().web.port


def _cert_path() -> pathlib.Path:
    return pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def _base_proxy_env(proxy_port: int) -> dict[str, str]:
    env = os.environ.copy()
    url = f"http://127.0.0.1:{proxy_port}"
    env["HTTP_PROXY"] = url
    env["http_proxy"] = url
    env["HTTPS_PROXY"] = url
    env["https_proxy"] = url
    env["NO_PROXY"] = "github.com,localhost,127.0.0.1,::1"
    env["no_proxy"] = env["NO_PROXY"]
    return env


def _inject_node_cert(env: dict[str, str], cert: pathlib.Path) -> None:
    if cert.exists():
        env["NODE_EXTRA_CA_CERTS"] = cert.as_posix()


def _inject_go_cert(env: dict[str, str], cert: pathlib.Path) -> None:
    if cert.exists():
        env["SSL_CERT_FILE"] = cert.as_posix()


def _inject_python_cert(env: dict[str, str], cert: pathlib.Path) -> None:
    # SSL_CERT_FILE: Python ssl module + Go; REQUESTS_CA_BUNDLE: requests library.
    # httpx uses certifi and ignores both — see `contextspy setup-python` for httpx/openai-sdk.
    if cert.exists():
        cert_posix = cert.as_posix()
        env["SSL_CERT_FILE"] = cert_posix
        env["REQUESTS_CA_BUNDLE"] = cert_posix


# Registry: tool name → injectors applied on top of base proxy env.
# Add new tools here; unknown tools fall back to base proxy env only.
_TOOL_INJECTORS: dict[str, list[Callable[[dict[str, str], pathlib.Path], None]]] = {
    "code": [_inject_node_cert],  # VS Code
    "cursor": [_inject_node_cert],  # Cursor
    "claude": [_inject_node_cert],  # Claude Code (Electron/Node)
    "opencode": [_inject_node_cert, _inject_go_cert],  # opencode (Node + Go TLS)
    "python": [_inject_python_cert],
    "python3": [_inject_python_cert],
    "uv": [_inject_python_cert],
    "uvx": [_inject_python_cert],
}

# Electron-based tools: on Windows env-var proxy doesn't reach the Node.js
# extension host; a PAC file via --proxy-pac-url routes only LLM API domains
# through mitmproxy and sends everything else DIRECT (avoids auth breakage).
_ELECTRON_TOOLS: frozenset[str] = frozenset({"code", "cursor"})


def _abort_if_migrations_pending(settings) -> None:
    """Exit before starting if the DB has pending data migrations (e.g. after an upgrade).

    Starting on a partially-migrated DB would serve stale/incomplete data (e.g.
    older requests missing blocks), so this blocks startup rather than just warning.
    """
    from contextspy.db import migrations
    from contextspy.db.database import get_db, init_db

    init_db(settings.storage.db_path)
    with get_db() as db:
        pending = migrations.check_and_flag_pending_migrations(db)
    if pending:
        console.print(
            f"[red]Database schema has pending data migrations: {pending}.[/red]\n"
            "[yellow]Run [bold]contextspy db-upgrade[/bold] to backfill, "
            "or [bold]contextspy reset-db[/bold] to start fresh (recommended).[/yellow]\n"
        )
        raise typer.Exit(1)


@app.command()
def start(
    proxy_port: int = typer.Option(8888, "--proxy-port", help="Proxy listen port"),
    web_port: int = typer.Option(5173, "--web-port", help="Web server listen port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser"),
) -> None:
    """Start the proxy and web server in cloud/forward mode (Ctrl+C to stop)."""
    import uvicorn

    from contextspy.config import Settings
    from contextspy.proxy.cert import generate_cert, install_cert

    settings = Settings.load()
    settings.proxy.port = proxy_port
    settings.web.port = web_port
    settings.ensure_dirs()
    settings.write_defaults()
    _abort_if_migrations_pending(settings)

    # Validate cert (regenerates if missing or corrupted). Abort on failure —
    # a broken cert causes silent TLS drops, which is worse than not starting.
    ok, msg = generate_cert()
    if not ok:
        console.print(f"[red]Cannot start — CA certificate error:[/red] {msg}")
        raise typer.Exit(1)
    newly_generated = "already exists" not in msg
    if newly_generated:
        console.print(f"[green]{msg}[/green]")
        # Only attempt system-wide install when a new cert was just created.
        ok2, msg2 = install_cert()
        if ok2:
            console.print(f"[green]CA cert installed.[/green]")
        else:
            console.print(f"[yellow]CA cert not installed:[/yellow] {msg2}")

    url = f"http://{settings.web.bind_addr}:{settings.web.port}"
    console.print(f"[bold green]ContextSpy[/bold green] starting at {url}")
    console.print(f"  Proxy:  {settings.proxy.bind_addr}:{settings.proxy.port}")
    console.print(f"  DB:     {settings.storage.db_path}")
    console.print("Press [bold]Ctrl+C[/bold] to stop.\n")

    if not no_browser:
        import threading

        def _open():
            import time

            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    from contextspy.api.main import create_app

    application = create_app(settings)

    uvicorn.run(
        application,
        host=settings.web.bind_addr,
        port=settings.web.port,
        log_level="info",
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                    "datefmt": "%H:%M:%S",
                },
            },
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "contextspy": {
                    "handlers": ["default"],
                    "level": "DEBUG",
                    "propagate": False,
                },
                "uvicorn": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        },
    )


# ---------------------------------------------------------------------------
# start-local
# ---------------------------------------------------------------------------


@app.command("start-local")
def start_local(
    web_port: int = typer.Option(5173, "--web-port", help="Web server listen port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser"),
) -> None:
    """Start reverse-proxy listeners for local LLM servers (no cert needed).

    Reads [[reverse_targets]] from ~/.contextspy/config.toml.  Each target
    specifies a listen port and the upstream URL of your local server.  Point
    your client at http://127.0.0.1:<listen_port> instead of the server
    directly and contextspy will intercept every request.

    The web dashboard and SQLite database are shared with 'start', so you can
    run both modes simultaneously.
    """
    import uvicorn

    from contextspy.config import Settings

    settings = Settings.load()
    settings.web.port = web_port
    settings.ensure_dirs()
    settings.write_defaults()
    _abort_if_migrations_pending(settings)

    if not settings.reverse_targets:
        console.print(
            "[red]No [[reverse_targets]] found in ~/.contextspy/config.toml[/red]\n"
            "Add at least one target, for example:\n\n"
            "  [[reverse_targets]]\n"
            '  name        = "llama-server"\n'
            "  listen_port = 8889\n"
            '  target_url  = "http://127.0.0.1:8080"\n'
            '  provider    = "openai"\n'
        )
        raise typer.Exit(1)

    url = f"http://{settings.web.bind_addr}:{settings.web.port}"
    console.print(f"[bold green]ContextSpy[/bold green] (local mode) starting at {url}")
    for t in settings.reverse_targets:
        console.print(
            f"  [{t.name}]  localhost:{t.listen_port} → {t.target_url}  (provider={t.provider})"
        )
    console.print(f"  DB:  {settings.storage.db_path}")
    console.print("Press [bold]Ctrl+C[/bold] to stop.\n")

    if not no_browser:
        import threading

        def _open():
            import time

            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    from contextspy.api.main import create_app_local

    application = create_app_local(settings)

    uvicorn.run(
        application,
        host=settings.web.bind_addr,
        port=settings.web.port,
        log_level="info",
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                    "datefmt": "%H:%M:%S",
                },
            },
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "contextspy": {
                    "handlers": ["default"],
                    "level": "DEBUG",
                    "propagate": False,
                },
                "uvicorn": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        },
    )


@app.command()
def status() -> None:
    """Show proxy status and active session."""
    port = _web_port()
    try:
        resp = httpx.get(_api(port, "/proxy/status"), timeout=3)
        data = resp.json()
        console.print(
            f"Proxy running:   [bold]{'yes' if data['running'] else 'no'}[/bold]"
        )
        console.print(f"Proxy port:      {data['port']}")
        console.print(f"Cert installed:  {'yes' if data['cert_installed'] else 'no'}")
    except Exception:
        console.print("[red]Web server not reachable. Is contextspy running?[/red]")
        return

    try:
        resp2 = httpx.get(_api(port, "/sessions"), timeout=3)
        sessions = resp2.json().get("sessions", [])
        active = next((s for s in sessions if s["is_active"]), None)
        if active:
            console.print(
                f"Active session:  [bold green]{active['name']}[/bold green] (id: {active['id'][:8]}…)"
            )
        else:
            console.print("Active session:  [dim]none[/dim]")
    except Exception:
        pass


@app.command("install-cert")
def install_cert_cmd() -> None:
    """Generate (if needed) and install the mitmproxy CA certificate into the system trust store."""
    from contextspy.proxy.cert import generate_cert, install_cert

    ok, msg = generate_cert()
    if not ok:
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)
    if "already exists" not in msg:
        console.print(f"[green]{msg}[/green]")

    ok, msg = install_cert()
    if ok:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[yellow]{msg}[/yellow]")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command(
    "run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def run_cmd(
    ctx: typer.Context,
    proxy_port: Optional[int] = typer.Option(
        None, "--proxy-port", help="Proxy port (default: from config)"
    ),
) -> None:
    """Run a command with HTTPS_PROXY and cert env vars pre-set.

    Known tools (code, cursor, claude, opencode) get tool-specific cert vars injected.
    Any other command gets base proxy vars (HTTPS_PROXY, NO_PROXY) only.

    Contextspy options must come before the tool name:
      contextspy run --proxy-port 9000 code .
      contextspy run claude --dangerously-skip-permissions .
    """
    from contextspy.config import Settings

    args = ctx.args
    if not args:
        console.print(
            "[bold]Usage:[/bold] contextspy run [dim][--proxy-port N][/dim] <tool> [args...]\n"
        )
        console.print("  contextspy run code .")
        console.print("  contextspy run claude .")
        console.print("  contextspy run opencode .")
        console.print("  contextspy run cursor /path/to/project")
        console.print("  contextspy run <any-command> [args...]")
        raise typer.Exit(1)

    tool, tool_args = args[0], args[1:]

    settings = Settings.load()
    port = proxy_port if proxy_port is not None else settings.proxy.port
    cert = _cert_path()

    try:
        httpx.get(f"http://127.0.0.1:{settings.web.port}/api/stats", timeout=1.0)
    except Exception:
        console.print(
            "[red]Error:[/red] ContextSpy is not running. "
            "Start it first with [bold]contextspy start[/bold]."
        )
        raise typer.Exit(1)

    env = _base_proxy_env(port)
    injectors = _TOOL_INJECTORS.get(tool, [])
    for inject in injectors:
        inject(env, cert)

    if injectors and not cert.exists():
        console.print(
            f"[red]Error:[/red] CA cert not found at {cert}.\n"
            "Run [bold]contextspy install-cert[/bold] first, "
            "then [bold]contextspy start[/bold]."
        )
        raise typer.Exit(1)

    extra_args: list[str] = []
    if tool in _ELECTRON_TOOLS and os.name == "nt":
        pac_url = f"http://127.0.0.1:{settings.web.port}/api/proxy.pac"
        extra_args = [f"--proxy-pac-url={pac_url}"]
        # Extension host Node.js ignores NODE_EXTRA_CA_CERTS on Windows;
        # --use-system-ca makes it trust the Windows Root store where
        # contextspy install-cert already placed the mitmproxy CA.
        node_opts = env.get("NODE_OPTIONS", "")
        if "--use-system-ca" not in node_opts:
            env["NODE_OPTIONS"] = (node_opts + " --use-system-ca").strip()
        console.print(
            "[dim]Note (Windows): close any existing VS Code/Cursor window before "
            "running this — a reused process won't pick up the PAC proxy.[/dim]"
        )

    cmd = [tool, *extra_args, *tool_args]
    console.print(
        f"[dim]contextspy run: {' '.join(cmd)}  HTTPS_PROXY={env['HTTPS_PROXY']}[/dim]"
    )
    result = subprocess.run(cmd, env=env, shell=(os.name == "nt"))
    raise typer.Exit(result.returncode)


# ---------------------------------------------------------------------------
# Session sub-commands
# ---------------------------------------------------------------------------


@session_app.command("start")
def session_start(name: str = typer.Argument(..., help="Session name")) -> None:
    """Start a named session (ends any current session)."""
    port = _web_port()
    try:
        resp = httpx.post(_api(port, "/sessions"), json={"name": name}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("warning"):
            console.print(f"[yellow]{data['warning']}[/yellow]")
        console.print(
            f"[green]Session started:[/green] {data['session']['name']} ({data['session']['id'][:8]}…)"
        )
    except Exception as exc:
        console.print(f"[red]Error: {exc}. Is contextspy running?[/red]")
        raise typer.Exit(1)


@session_app.command("end")
def session_end() -> None:
    """End the active session."""
    port = _web_port()
    try:
        resp = httpx.get(_api(port, "/sessions"), timeout=5)
        sessions = resp.json().get("sessions", [])
        active = next((s for s in sessions if s["is_active"]), None)
        if not active:
            console.print("[yellow]No active session.[/yellow]")
            return
        resp2 = httpx.post(_api(port, f"/sessions/{active['id']}/end"), timeout=5)
        resp2.raise_for_status()
        console.print(f"[green]Session ended:[/green] {active['name']}")
    except Exception as exc:
        console.print(f"[red]Error: {exc}. Is contextspy running?[/red]")
        raise typer.Exit(1)


@session_app.command("list")
def session_list() -> None:
    """List all sessions."""
    port = _web_port()
    try:
        resp = httpx.get(_api(port, "/sessions"), timeout=5)
        sessions = resp.json().get("sessions", [])
    except Exception as exc:
        console.print(f"[red]Error: {exc}. Is contextspy running?[/red]")
        raise typer.Exit(1)

    table = Table(title="Sessions")
    table.add_column("Name", style="bold")
    table.add_column("ID")
    table.add_column("Started")
    table.add_column("Ended")
    table.add_column("Active")
    for s in sessions:
        table.add_row(
            s["name"],
            s["id"][:8] + "…",
            s["started_at"][:19],
            s["ended_at"][:19] if s.get("ended_at") else "—",
            "[green]yes[/green]" if s["is_active"] else "no",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------


@app.command("help")
def help_cmd() -> None:
    """List all available commands."""
    console.print(
        "\n[bold cyan]ContextSpy[/bold cyan] — LLM context window analyser and proxy\n"
    )
    rows = [
        ("start", "Start the proxy and web dashboard (cloud APIs, forward mode)"),
        ("start-local", "Start reverse proxies for local LLM servers (no cert needed)"),
        ("status", "Show proxy status and active session"),
        ("install-cert", "Install the mitmproxy CA cert into the system trust store"),
        (
            "run <tool>",
            "Run a tool with proxy env vars injected (code/cursor/claude/opencode + fallback)",
        ),
        ("reset-db", "Delete all requests and sessions from the local database"),
        (
            "db-upgrade",
            "Apply pending data migrations (e.g. backfill blocks from raw bodies)",
        ),
        ("db-stats", "Print row counts for each database table"),
        ("report", "Print aggregate stats: requests, tokens, category breakdown"),
        (
            "setup-claude",
            "Print env-var commands to route Claude Code through the proxy",
        ),
        (
            "setup-copilot",
            "Print env-var commands to route GitHub Copilot through the proxy",
        ),
        (
            "setup-opencode",
            "Print env-var commands to route opencode through the proxy",
        ),
        (
            "setup-python",
            "Print instructions for Python scripts using OpenAI SDK / httpx",
        ),
        ("inject-cert", "Append mitmproxy CA to certifi so httpx/OpenAI SDK trusts it"),
        ("setup-llamaserver", "Print setup instructions for llama.cpp/llama-server"),
        ("setup-ollama", "Print setup instructions for Ollama"),
        ("setup-vllm", "Print setup instructions for vLLM"),
        ("session start", "Start a named session"),
        ("session end", "End the current active session"),
        ("session list", "List all sessions"),
    ]
    table = Table(show_header=True, header_style="bold")
    table.add_column("Command", style="bold green", min_width=18)
    table.add_column("Description")
    for cmd, desc in rows:
        table.add_row(cmd, desc)
    console.print(table)
    console.print(
        "\nRun [bold]contextspy <command> --help[/bold] for details on any command.\n"
    )


# ---------------------------------------------------------------------------
# reset-db
# ---------------------------------------------------------------------------


@app.command("reset-db")
def reset_db(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete all requests and sessions from the local database."""
    if not yes:
        typer.confirm(
            "This will permanently delete ALL requests and sessions. Continue?",
            abort=True,
        )
    import sqlite3

    from contextspy.config import Settings

    db_path = Settings.load().storage.db_path
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    for table in (
        "tool_stats",
        "blocks",
        "block_contents",
        "requests",
        "sessions",
        "schema_meta",
    ):
        try:
            cur.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # table not yet created (old DB)
    con.commit()
    con.close()
    console.print("[green]Database cleared.[/green]")


# ---------------------------------------------------------------------------
# db-upgrade
# ---------------------------------------------------------------------------


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Apply pending data migrations (e.g. backfill blocks from raw request bodies).

    Safe to re-run — already-migrated requests are skipped. Requests whose raw
    bodies were already purged by retention simply get no blocks.
    """
    from contextspy.config import Settings
    from contextspy.db import migrations
    from contextspy.db.database import get_db, init_db

    settings = Settings.load()
    settings.ensure_dirs()
    init_db(settings.storage.db_path)

    with get_db() as db:
        pending = migrations.check_and_flag_pending_migrations(db)
        if not pending:
            console.print(
                "[green]Database is already up to date. No migrations pending.[/green]"
            )
            return
        console.print(f"[bold]Applying data migrations:[/bold] {pending}")
        applied = migrations.apply_data_migrations(db)

    console.print(f"[green]Done.[/green] Applied migrations: {applied}")


# ---------------------------------------------------------------------------
# db-stats
# ---------------------------------------------------------------------------


@app.command("db-stats")
def db_stats() -> None:
    """Print row counts for each database table."""
    import sqlite3

    from contextspy.config import Settings

    db_path = Settings.load().storage.db_path
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]
    table = Table(title=f"DB: {db_path}", show_header=True, header_style="bold")
    table.add_column("Table", style="bold")
    table.add_column("Rows", justify="right")
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        count = cur.fetchone()[0]
        table.add_row(t, str(count))
    con.close()
    console.print(table)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@app.command("report")
def report() -> None:
    """Print aggregate stats: requests, tokens in/out, context category breakdown."""
    import sqlite3

    from contextspy.config import Settings

    db_path = Settings.load().storage.db_path
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM requests")
    total_requests = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COALESCE(SUM(tokens_total_input), 0),
            COALESCE(SUM(tokens_total_output), 0),
            COALESCE(SUM(provider_input_tokens), 0),
            COALESCE(SUM(provider_output_tokens), 0),
            COALESCE(SUM(tokens_system_prompt), 0),
            COALESCE(SUM(tokens_tool_definitions), 0),
            COALESCE(SUM(tokens_tool_results), 0),
            COALESCE(SUM(tokens_file_contents), 0),
            COALESCE(SUM(tokens_conversation_history), 0),
            COALESCE(SUM(tokens_current_user_message), 0),
            COALESCE(SUM(tokens_assistant_prefill), 0),
            COALESCE(SUM(tokens_uncategorized), 0)
        FROM requests
    """)
    row = cur.fetchone()
    (
        total_in,
        total_out,
        prov_in,
        prov_out,
        sys_p,
        tool_def,
        tool_res,
        file_c,
        conv_hist,
        cur_msg,
        prefill,
        uncat,
    ) = row

    # Per-tool breakdown (only if tool_stats table exists)
    tool_rows: list[tuple] = []
    try:
        cur.execute("""
            SELECT tool_name,
                   SUM(definition_tokens) AS def_tok,
                   SUM(result_tokens)     AS res_tok
            FROM tool_stats
            GROUP BY tool_name
            ORDER BY def_tok DESC
        """)
        tool_rows = cur.fetchall()
    except sqlite3.OperationalError:
        pass  # table not yet created (old DB)

    con.close()

    console.print(f"\n[bold cyan]ContextSpy Report[/bold cyan]\n")

    # Summary
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Key", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total requests", str(total_requests))
    summary.add_row("Total input tokens (estimated)", f"{total_in:,}")
    summary.add_row("Total output tokens (estimated)", f"{total_out:,}")
    summary.add_row("Total input tokens (provider)", f"{prov_in:,}")
    summary.add_row("Total output tokens (provider)", f"{prov_out:,}")
    console.print(summary)

    # Category breakdown
    categories = [
        ("System prompt", sys_p),
        ("Tool definitions", tool_def),
        ("Tool results", tool_res),
        ("File contents", file_c),
        ("Conversation history", conv_hist),
        ("Current user message", cur_msg),
        ("Assistant prefill", prefill),
        ("Uncategorized", uncat),
    ]
    total_cat = sum(v for _, v in categories) or 1

    breakdown = Table(title="Input token category breakdown", header_style="bold")
    breakdown.add_column("Category", style="bold")
    breakdown.add_column("Tokens", justify="right")
    breakdown.add_column("Share", justify="right")
    for name, val in categories:
        pct = val / total_cat * 100
        bar = "█" * int(pct / 5)
        breakdown.add_row(name, f"{val:,}", f"{pct:5.1f}%  {bar}")
    console.print(breakdown)

    # Per-tool breakdown
    if tool_rows:
        total_def = sum(r[1] for r in tool_rows) or 1
        total_res = sum(r[2] for r in tool_rows) or 1
        has_results = any(r[2] > 0 for r in tool_rows)

        tools_table = Table(
            title="Tool definition tokens (top 30)", header_style="bold"
        )
        tools_table.add_column("Tool name", style="bold")
        tools_table.add_column("Def tokens", justify="right")
        tools_table.add_column("Def %", justify="right")
        if has_results:
            tools_table.add_column("Result tokens", justify="right")
            tools_table.add_column("Result %", justify="right")
        for name, def_tok, res_tok in tool_rows[:30]:
            def_pct = def_tok / total_def * 100
            row_vals = [name, f"{def_tok:,}", f"{def_pct:5.1f}%"]
            if has_results:
                res_pct = res_tok / total_res * 100
                row_vals += [f"{res_tok:,}", f"{res_pct:5.1f}%"]
            tools_table.add_row(*row_vals)
        console.print(tools_table)

    console.print()


# ---------------------------------------------------------------------------
# setup-claude
# ---------------------------------------------------------------------------


@app.command("setup-claude")
def setup_claude() -> None:
    """Print commands to route Claude Code through the ContextSpy proxy."""
    from contextspy.config import Settings

    settings = Settings.load()
    port = settings.proxy.port
    cert = str(settings.storage.db_path).replace("contextspy.db", "").rstrip("/\\")
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print("\n[bold cyan]Claude Code — proxy setup[/bold cyan]\n")
    console.print(
        "Run the following in the terminal where you launch [bold]claude[/bold]:\n"
    )
    console.print("[bold yellow]PowerShell:[/bold yellow]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:NODE_EXTRA_CA_CERTS = "{cert_path}"')
    console.print('  $env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print("[bold yellow]Bash / Zsh:[/bold yellow]")
    console.print(f"  export HTTPS_PROXY=http://127.0.0.1:{port}")
    console.print(f'  export NODE_EXTRA_CA_CERTS="{cert_path}"')
    console.print('  export NO_PROXY="github.com,localhost,127.0.0.1,::1"')
    console.print('  export no_proxy="github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print(
        "[dim]NO_PROXY prevents git and other tools from routing through the proxy.[/dim]"
    )
    console.print(
        "[dim]Tip: add these to your shell profile to make them permanent.[/dim]"
    )
    console.print(
        "[dim]Run [bold]contextspy install-cert[/bold] if SSL errors occur.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# setup-copilot
# ---------------------------------------------------------------------------


@app.command("setup-copilot")
def setup_copilot() -> None:
    """Print commands to route GitHub Copilot through the ContextSpy proxy."""
    from contextspy.config import Settings

    settings = Settings.load()
    port = settings.proxy.port
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print("\n[bold cyan]GitHub Copilot — proxy setup[/bold cyan]\n")
    console.print(
        "Run the following in the terminal where VS Code / the Copilot extension runs:\n"
    )
    console.print("[bold yellow]PowerShell:[/bold yellow]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:NODE_EXTRA_CA_CERTS = "{cert_path}"')
    console.print('  $env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print("[bold yellow]Bash / Zsh:[/bold yellow]")
    console.print(f"  export HTTPS_PROXY=http://127.0.0.1:{port}")
    console.print(f'  export NODE_EXTRA_CA_CERTS="{cert_path}"')
    console.print('  export NO_PROXY="github.com,localhost,127.0.0.1,::1"')
    console.print('  export no_proxy="github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print(
        "[bold]VS Code settings.json[/bold] (alternative — applies to all extensions):"
    )
    console.print(f'  "http.proxy": "http://127.0.0.1:{port}",')
    console.print('  "http.proxyStrictSSL": false,')
    console.print('  "http.noProxy": ["github.com", "localhost", "127.0.0.1"]')
    console.print()
    console.print(
        "[dim]NO_PROXY prevents git and other tools from routing through the proxy.[/dim]"
    )
    console.print(
        "[dim]Copilot uses copilot-proxy.githubusercontent.com — already in the provider list.[/dim]"
    )
    console.print(
        "[dim]Run [bold]contextspy install-cert[/bold] if SSL errors occur.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# setup-opencode  (already present above)
# setup-llamaserver
# ---------------------------------------------------------------------------


def _print_local_setup_header(
    server_name: str, default_server_port: int, default_listen_port: int
) -> tuple[int, int]:
    """Print the common config.toml block and return (server_port, listen_port)."""
    from contextspy.config import Settings

    settings = Settings.load()
    console.print(
        f"\n[bold cyan]{server_name} — ContextSpy local reverse-proxy setup[/bold cyan]\n"
    )
    console.print(
        f"In this mode contextspy acts as a reverse proxy in front of {server_name}.\n"
        "No CA certificate is needed — traffic stays on localhost and is plain HTTP.\n"
    )
    console.print("[bold]1. Add to ~/.contextspy/config.toml:[/bold]")
    console.print(f"\n  [[reverse_targets]]", markup=False)
    console.print(
        f'  name        = "{server_name.lower().replace(" ", "-")}"', markup=False
    )
    console.print(f"  listen_port = {default_listen_port}   # contextspy listens here")
    console.print(
        f'  target_url  = "http://127.0.0.1:{default_server_port}"  # your {server_name} port'
    )
    console.print('  provider    = "openai"   # OpenAI-compatible API')
    console.print()
    console.print("[bold]2. Start contextspy in local mode:[/bold]")
    console.print("  uv run contextspy start-local\n")
    return default_server_port, default_listen_port


@app.command("setup-llamaserver")
def setup_llamaserver() -> None:
    """Print setup instructions for llama.cpp / llama-server."""
    server_port, listen_port = _print_local_setup_header("llama-server", 8080, 8889)
    console.print(
        "[bold]3. Point your client at ContextSpy instead of llama-server directly:[/bold]"
    )
    console.print(f"  Change base URL from  http://127.0.0.1:{server_port}/v1")
    console.print(f"                    to  http://127.0.0.1:{listen_port}/v1\n")
    console.print("[bold]4. Launch llama-server as normal:[/bold]")
    console.print(f"  llama-server -m your-model.gguf --port {server_port}\n")
    console.print(
        "[dim]Tip: llama-server exposes the OpenAI-compatible /v1/chat/completions endpoint.[/dim]"
    )
    console.print(
        "[dim]The 'openai' provider parser handles it with full token breakdown.[/dim]\n"
    )


@app.command("setup-ollama")
def setup_ollama() -> None:
    """Print setup instructions for Ollama."""
    server_port, listen_port = _print_local_setup_header("Ollama", 11434, 8890)
    console.print(
        "[bold]3. Point your client at ContextSpy instead of Ollama directly:[/bold]"
    )
    console.print(f"  Change base URL from  http://127.0.0.1:{server_port}/v1")
    console.print(f"                    to  http://127.0.0.1:{listen_port}/v1\n")
    console.print(
        "[bold]4. Ollama runs as a background service — no extra step needed.[/bold]\n"
    )
    console.print(
        "[bold]Alternatively[/bold] — use contextspy's built-in Ollama forward-proxy support:"
    )
    console.print("  Run [bold]contextspy start[/bold] (normal cloud mode).")
    console.print(f"  Ollama on port 11434 is auto-detected by the forward proxy.")
    console.print("  Set HTTPS_PROXY=http://127.0.0.1:8888 in your client.\n")
    console.print(
        "[dim]Ollama /v1/chat/completions is OpenAI-compatible (Ollama >= 0.1.24).[/dim]"
    )
    console.print(
        "[dim]The raw /api/generate and /api/chat endpoints are not yet parsed.[/dim]\n"
    )


@app.command("setup-vllm")
def setup_vllm() -> None:
    """Print setup instructions for vLLM."""
    server_port, listen_port = _print_local_setup_header("vLLM", 8000, 8891)
    console.print(
        "[bold]3. Point your client at ContextSpy instead of vLLM directly:[/bold]"
    )
    console.print(f"  Change base URL from  http://127.0.0.1:{server_port}/v1")
    console.print(f"                    to  http://127.0.0.1:{listen_port}/v1\n")
    console.print("[bold]4. Launch vLLM as normal:[/bold]")
    console.print(f"  vllm serve your-model --port {server_port}\n")
    console.print(
        '[dim]vLLM exposes a fully OpenAI-compatible API; use provider = "openai" in config.[/dim]'
    )
    console.print(
        "[dim]If vLLM uses a different port, update target_url and listen_port accordingly.[/dim]\n"
    )


@app.command("setup-opencode")
def setup_opencode() -> None:
    """Print commands to route opencode through the ContextSpy proxy."""
    from contextspy.config import Settings

    settings = Settings.load()
    port = settings.proxy.port
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print("\n[bold cyan]opencode — proxy setup[/bold cyan]\n")
    console.print(
        "Run the following in the terminal where you launch [bold]opencode[/bold]:\n"
    )
    console.print("[bold yellow]PowerShell:[/bold yellow]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:SSL_CERT_FILE = "{cert_path}"')
    console.print(f'  $env:NODE_EXTRA_CA_CERTS = "{cert_path}"')
    console.print('  $env:NO_PROXY = "github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print("[bold yellow]Bash / Zsh:[/bold yellow]")
    console.print(f"  export HTTPS_PROXY=http://127.0.0.1:{port}")
    console.print(f'  export SSL_CERT_FILE="{cert_path}"')
    console.print(f'  export NODE_EXTRA_CA_CERTS="{cert_path}"')
    console.print('  export NO_PROXY="github.com,localhost,127.0.0.1,::1"')
    console.print('  export no_proxy="github.com,localhost,127.0.0.1,::1"')
    console.print()
    console.print("[bold]opencode config (~/.config/opencode/config.json):[/bold]")
    console.print("  {")
    console.print(f'    "proxy": "http://127.0.0.1:{port}"')
    console.print("  }")
    console.print()
    console.print(
        "[dim]NO_PROXY prevents git and other tools from routing through the proxy.[/dim]"
    )
    console.print(
        "[dim]Tip: add the env vars to your shell profile to make them permanent.[/dim]"
    )
    console.print(
        "[dim]Run [bold]contextspy install-cert[/bold] if SSL errors occur.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# setup-python
# ---------------------------------------------------------------------------


@app.command("setup-python")
def setup_python() -> None:
    """Print instructions to route Python scripts (OpenAI SDK, httpx) through the proxy."""
    from contextspy.config import Settings

    settings = Settings.load()
    port = settings.proxy.port
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print(
        "\n[bold cyan]Python (OpenAI SDK / httpx) — proxy setup[/bold cyan]\n"
    )
    console.print(
        "[bold yellow]Why SSL errors occur[/bold yellow]\n"
        "The OpenAI Python SDK uses [bold]httpx[/bold], which verifies TLS certificates\n"
        "against [bold]certifi[/bold]'s bundled CA store — not the Windows/macOS system store.\n"
        "mitmproxy's CA is not in certifi, so certificate verification fails.\n"
        "Setting [bold]SSL_CERT_FILE[/bold] or [bold]REQUESTS_CA_BUNDLE[/bold] does [bold]not[/bold] fix this\n"
        "(httpx passes certifi's path explicitly, bypassing those env vars).\n"
    )

    console.print("[bold yellow]Option 1 — code change (recommended)[/bold yellow]")
    console.print("Pass a custom httpx client when creating the OpenAI client:\n")
    console.print(
        "  [dim]import httpx[/dim]\n"
        "  [dim]from openai import OpenAI[/dim]\n"
        "\n"
        f"  [dim]client = OpenAI([/dim]\n"
        f"  [dim]    http_client=httpx.Client([/dim]\n"
        f'  [dim]        proxy="http://127.0.0.1:{port}",[/dim]\n'
        f'  [dim]        verify=r"{cert_path}",[/dim]\n'
        f"  [dim]    )[/dim]\n"
        f"  [dim])[/dim]\n"
    )

    console.print(
        "[bold yellow]Option 2 — no code change (append cert to certifi)[/bold yellow]"
    )
    console.print(
        "Append the mitmproxy CA to certifi's bundle in your virtual environment.\n"
        "[yellow]Note:[/yellow] this is reset when certifi is upgraded.\n"
    )
    console.print("[bold]PowerShell:[/bold]")
    console.print(
        f'  $cert = python -c "import certifi; print(certifi.where())"\n'
        f'  Get-Content "{cert_path}" | Add-Content $cert\n'
    )
    console.print("[bold]Bash / Zsh:[/bold]")
    console.print(
        f'  cat "{cert_path}" >> $(python -c "import certifi; print(certifi.where())")\n'
    )
    console.print(
        "Or run: [bold]contextspy inject-cert[/bold] to do this automatically.\n"
    )

    console.print("[bold yellow]Env vars (for requests / urllib only)[/bold yellow]")
    console.print(
        "These help if your code uses [bold]requests[/bold] or [bold]urllib[/bold] (not httpx):\n"
    )
    console.print("[bold]PowerShell:[/bold]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:REQUESTS_CA_BUNDLE = "{cert_path}"')
    console.print(f'  $env:SSL_CERT_FILE = "{cert_path}"')
    console.print()
    console.print("[bold]Bash / Zsh:[/bold]")
    console.print(f"  export HTTPS_PROXY=http://127.0.0.1:{port}")
    console.print(f'  export REQUESTS_CA_BUNDLE="{cert_path}"')
    console.print(f'  export SSL_CERT_FILE="{cert_path}"')
    console.print()


# ---------------------------------------------------------------------------
# inject-cert
# ---------------------------------------------------------------------------


@app.command("inject-cert")
def inject_cert(
    python_exe: Optional[str] = typer.Option(
        None,
        "--python",
        help="Python executable to use (default: python in PATH or active venv)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Append the mitmproxy CA cert to certifi's bundle so httpx/OpenAI SDK trusts it.

    The change is scoped to the active virtual environment (or the Python in PATH).
    It is reset when certifi is upgraded — re-run this command after upgrading certifi.
    """
    import shutil
    import subprocess as sp

    cert = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if not cert.exists():
        console.print(
            "[red]mitmproxy CA cert not found.[/red] "
            "Run [bold]contextspy install-cert[/bold] first."
        )
        raise typer.Exit(1)

    exe = python_exe or shutil.which("python") or shutil.which("python3")
    if not exe:
        console.print("[red]No Python executable found in PATH.[/red]")
        raise typer.Exit(1)

    # Locate certifi bundle
    try:
        result = sp.run(
            [exe, "-c", "import certifi; print(certifi.where())"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            console.print(
                f"[red]Failed to locate certifi:[/red] {result.stderr.strip()}\n"
                "Install certifi with: pip install certifi"
            )
            raise typer.Exit(1)
    except (sp.TimeoutExpired, FileNotFoundError) as exc:
        console.print(f"[red]Error running Python:[/red] {exc}")
        raise typer.Exit(1)

    bundle = pathlib.Path(result.stdout.strip())

    # Check if already injected
    bundle_text = bundle.read_text(encoding="utf-8", errors="replace")
    cert_text = cert.read_text(encoding="utf-8")
    if cert_text.strip() in bundle_text:
        console.print(
            f"[green]mitmproxy CA is already present in {bundle}[/green]\n"
            "No changes needed."
        )
        return

    console.print(f"[bold]Python:[/bold]  {exe}")
    console.print(f"[bold]Bundle:[/bold]  {bundle}")
    console.print(f"[bold]Cert:[/bold]    {cert}")
    console.print()
    console.print(
        "[yellow]Note:[/yellow] This modifies certifi's CA bundle. "
        "The change is reset when certifi is upgraded.\n"
        "Re-run [bold]contextspy inject-cert[/bold] after upgrading certifi."
    )

    if not yes:
        typer.confirm("Append mitmproxy CA to certifi bundle?", abort=True)

    with bundle.open("a", encoding="utf-8") as f:
        f.write("\n# mitmproxy CA — injected by contextspy\n")
        f.write(cert_text)

    console.print(f"[green]Done.[/green] mitmproxy CA appended to {bundle}")
    console.print(
        "httpx and the OpenAI SDK will now trust the contextspy proxy certificate."
    )


if __name__ == "__main__":
    app()
