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


if __name__ == "__main__":
    app()
