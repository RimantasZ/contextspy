"""Typer CLI entry point — implemented in Phase 5."""
import typer

app = typer.Typer(name="token-scrooge", help="LLM context window analyser and proxy.")


@app.command()
def start(
    proxy_port: int = typer.Option(8080, "--proxy-port"),
    web_port: int = typer.Option(5173, "--web-port"),
) -> None:
    """Start the proxy and web server."""
    typer.echo("token-scrooge start — not yet implemented (Phase 5)")


if __name__ == "__main__":
    app()
