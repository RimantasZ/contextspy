"""Typer CLI entry point for Token-Scrooge."""
from __future__ import annotations

import webbrowser
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="token-scrooge", help="LLM context window analyser and proxy.", no_args_is_help=True)
session_app = typer.Typer(help="Manage named sessions.")
app.add_typer(session_app, name="session")

console = Console()


def _api(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}/api{path}"


def _web_port() -> int:
    from token_scrooge.config import Settings
    return Settings.load().web.port


@app.command()
def start(
    proxy_port: int = typer.Option(8888, "--proxy-port", help="Proxy listen port"),
    web_port: int = typer.Option(5173, "--web-port", help="Web server listen port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser"),
) -> None:
    """Start the proxy and web server (Ctrl+C to stop)."""
    import uvicorn
    from token_scrooge.config import Settings
    from token_scrooge.proxy.cert import cert_exists, install_cert

    settings = Settings.load()
    settings.proxy.port = proxy_port
    settings.web.port = web_port
    settings.ensure_dirs()
    settings.write_defaults()

    # Cert check
    if not cert_exists():
        console.print("[yellow]mitmproxy CA certificate not found. Generating and installing...[/yellow]")
        console.print("[dim]You may need to run the proxy once first for mitmproxy to generate the cert.[/dim]")
    else:
        ok, msg = install_cert()
        if not ok:
            console.print(f"[yellow]CA cert install warning:[/yellow] {msg}")

    url = f"http://{settings.web.bind_addr}:{settings.web.port}"
    console.print(f"[bold green]Token-Scrooge[/bold green] starting at {url}")
    console.print(f"  Proxy:  {settings.proxy.bind_addr}:{settings.proxy.port}")
    console.print(f"  DB:     {settings.storage.db_path}")
    console.print("Press [bold]Ctrl+C[/bold] to stop.\n")

    if not no_browser:
        import threading
        def _open():
            import time; time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    from token_scrooge.api.main import create_app
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
                "default": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s", "datefmt": "%H:%M:%S"},
            },
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "token_scrooge": {"handlers": ["default"], "level": "DEBUG", "propagate": False},
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": "WARNING", "propagate": False},
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
        console.print(f"Proxy running:   [bold]{'yes' if data['running'] else 'no'}[/bold]")
        console.print(f"Proxy port:      {data['port']}")
        console.print(f"Cert installed:  {'yes' if data['cert_installed'] else 'no'}")
    except Exception:
        console.print("[red]Web server not reachable. Is token-scrooge running?[/red]")
        return

    try:
        resp2 = httpx.get(_api(port, "/sessions"), timeout=3)
        sessions = resp2.json().get("sessions", [])
        active = next((s for s in sessions if s["is_active"]), None)
        if active:
            console.print(f"Active session:  [bold green]{active['name']}[/bold green] (id: {active['id'][:8]}…)")
        else:
            console.print("Active session:  [dim]none[/dim]")
    except Exception:
        pass


@app.command("install-cert")
def install_cert_cmd() -> None:
    """Install the mitmproxy CA certificate into the system trust store."""
    from token_scrooge.proxy.cert import install_cert
    ok, msg = install_cert()
    if ok:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[yellow]{msg}[/yellow]")


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
        console.print(f"[green]Session started:[/green] {data['session']['name']} ({data['session']['id'][:8]}…)")
    except Exception as exc:
        console.print(f"[red]Error: {exc}. Is token-scrooge running?[/red]")
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
        console.print(f"[red]Error: {exc}. Is token-scrooge running?[/red]")
        raise typer.Exit(1)


@session_app.command("list")
def session_list() -> None:
    """List all sessions."""
    port = _web_port()
    try:
        resp = httpx.get(_api(port, "/sessions"), timeout=5)
        sessions = resp.json().get("sessions", [])
    except Exception as exc:
        console.print(f"[red]Error: {exc}. Is token-scrooge running?[/red]")
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
    console.print("\n[bold cyan]token-scrooge[/bold cyan] — LLM context window analyser and proxy\n")
    rows = [
        ("start",           "Start the proxy + web dashboard (Ctrl+C to stop)"),
        ("status",          "Show proxy status and active session"),
        ("install-cert",    "Install the mitmproxy CA cert into the system trust store"),
        ("reset-db",        "Delete all requests and sessions from the local database"),
        ("db-stats",        "Print row counts for each database table"),
        ("report",          "Print aggregate stats: requests, tokens, category breakdown"),
        ("setup-claude",    "Print env-var commands to route Claude Code through the proxy"),
        ("setup-copilot",   "Print env-var commands to route GitHub Copilot through the proxy"),
        ("session start",   "Start a named session"),
        ("session end",     "End the current active session"),
        ("session list",    "List all sessions"),
    ]
    table = Table(show_header=True, header_style="bold")
    table.add_column("Command", style="bold green", min_width=18)
    table.add_column("Description")
    for cmd, desc in rows:
        table.add_row(cmd, desc)
    console.print(table)
    console.print("\nRun [bold]token-scrooge <command> --help[/bold] for details on any command.\n")


# ---------------------------------------------------------------------------
# reset-db
# ---------------------------------------------------------------------------

@app.command("reset-db")
def reset_db(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete all requests and sessions from the local database."""
    if not yes:
        typer.confirm("This will permanently delete ALL requests and sessions. Continue?", abort=True)
    import sqlite3
    from token_scrooge.config import Settings
    db_path = Settings.load().storage.db_path
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("DELETE FROM requests")
    cur.execute("DELETE FROM sessions")
    con.commit()
    con.close()
    console.print("[green]Database cleared.[/green]")


# ---------------------------------------------------------------------------
# db-stats
# ---------------------------------------------------------------------------

@app.command("db-stats")
def db_stats() -> None:
    """Print row counts for each database table."""
    import sqlite3
    from token_scrooge.config import Settings
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
    from token_scrooge.config import Settings
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
    (total_in, total_out, prov_in, prov_out,
     sys_p, tool_def, tool_res, file_c, conv_hist, cur_msg, prefill, uncat) = row

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

    console.print(f"\n[bold cyan]Token-Scrooge Report[/bold cyan]\n")

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
        ("System prompt",           sys_p),
        ("Tool definitions",        tool_def),
        ("Tool results",            tool_res),
        ("File contents",           file_c),
        ("Conversation history",    conv_hist),
        ("Current user message",    cur_msg),
        ("Assistant prefill",       prefill),
        ("Uncategorized",           uncat),
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

        tools_table = Table(title="Tool definition tokens (top 30)", header_style="bold")
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
    """Print commands to route Claude Code through the token-scrooge proxy."""
    from token_scrooge.config import Settings
    settings = Settings.load()
    port = settings.proxy.port
    cert = str(settings.storage.db_path).replace("token-scrooge.db", "").rstrip("/\\")
    # mitmproxy stores certs in ~/.mitmproxy
    import pathlib
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print("\n[bold cyan]Claude Code — proxy setup[/bold cyan]\n")
    console.print("Run the following in the terminal where you launch [bold]claude[/bold]:\n")
    console.print("[bold yellow]PowerShell:[/bold yellow]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:NODE_EXTRA_CA_CERTS = "{cert_path}"')
    console.print()
    console.print("[bold yellow]Bash / Zsh:[/bold yellow]")
    console.print(f'  export HTTPS_PROXY=http://127.0.0.1:{port}')
    console.print(f'  export NODE_EXTRA_CA_CERTS="{cert_path}"')
    console.print()
    console.print("[dim]Tip: add these to your shell profile to make them permanent.[/dim]")
    console.print("[dim]Run [bold]token-scrooge install-cert[/bold] if SSL errors occur.[/dim]\n")


# ---------------------------------------------------------------------------
# setup-copilot
# ---------------------------------------------------------------------------

@app.command("setup-copilot")
def setup_copilot() -> None:
    """Print commands to route GitHub Copilot through the token-scrooge proxy."""
    from token_scrooge.config import Settings
    settings = Settings.load()
    port = settings.proxy.port
    import pathlib
    cert_path = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

    console.print("\n[bold cyan]GitHub Copilot — proxy setup[/bold cyan]\n")
    console.print("Run the following in the terminal where VS Code / the Copilot extension runs:\n")
    console.print("[bold yellow]PowerShell:[/bold yellow]")
    console.print(f'  $env:HTTPS_PROXY = "http://127.0.0.1:{port}"')
    console.print(f'  $env:NODE_EXTRA_CA_CERTS = "{cert_path}"')
    console.print()
    console.print("[bold yellow]Bash / Zsh:[/bold yellow]")
    console.print(f'  export HTTPS_PROXY=http://127.0.0.1:{port}')
    console.print(f'  export NODE_EXTRA_CA_CERTS="{cert_path}"')
    console.print()
    console.print("[bold]VS Code settings.json[/bold] (alternative — applies to all extensions):")
    console.print(f'  "http.proxy": "http://127.0.0.1:{port}",')
    console.print( '  "http.proxyStrictSSL": false')
    console.print()
    console.print("[dim]Copilot uses copilot-proxy.githubusercontent.com — already in the provider list.[/dim]")
    console.print("[dim]Run [bold]token-scrooge install-cert[/bold] if SSL errors occur.[/dim]\n")


if __name__ == "__main__":
    app()
