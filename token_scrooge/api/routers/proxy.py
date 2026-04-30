from __future__ import annotations

from fastapi import APIRouter

from token_scrooge.proxy import runner
from token_scrooge.proxy.cert import cert_exists, install_cert

router = APIRouter(tags=["proxy"])


@router.get("/proxy/status")
def proxy_status():
    from token_scrooge.api.main import create_app  # noqa: F401 – to get settings
    import token_scrooge.api.main as _main
    # Access settings from app state if available
    settings = getattr(_main, "_cached_settings", None)
    port = settings.proxy.port if settings else 8080
    return {
        "running": runner.is_running(),
        "port": port,
        "cert_installed": cert_exists(),
    }


@router.post("/proxy/start")
def proxy_start():
    if runner.is_running():
        return {"status": "already_running"}
    import token_scrooge.api.main as _main
    settings = getattr(_main, "_cached_settings", None)
    if settings is None:
        from token_scrooge.config import Settings
        settings = Settings.load()
    ws_manager = _main.get_ws_manager()
    runner.start_proxy(settings, ws_manager)
    return {"status": "started"}


@router.post("/proxy/stop")
def proxy_stop():
    runner.stop_proxy()
    return {"status": "stopped"}


@router.post("/proxy/install-cert")
def proxy_install_cert():
    success, message = install_cert()
    return {"success": success, "message": message}
