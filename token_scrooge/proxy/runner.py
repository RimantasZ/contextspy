from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.addons import errorcheck as _errorcheck

from token_scrooge.proxy.addon import TokenScroogeAddon

if TYPE_CHECKING:
    from token_scrooge.api.websocket import ConnectionManager
    from token_scrooge.config import Settings

logger = logging.getLogger(__name__)

_master: DumpMaster | None = None
_thread: threading.Thread | None = None
_addon: TokenScroogeAddon | None = None


def start_proxy(settings: "Settings", ws_manager: "ConnectionManager | None" = None) -> None:
    global _master, _thread, _addon

    if _master is not None:
        return  # already running

    _addon = TokenScroogeAddon()
    _addon.ws_manager = ws_manager

    options = Options(
        listen_host=settings.proxy.bind_addr,
        listen_port=settings.proxy.port,
        ssl_insecure=False,
    )

    def _run() -> None:
        global _master
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        master = DumpMaster(options, loop=loop, with_termlog=False, with_dumper=False)
        # Remove ErrorCheck — it calls sys.exit(1) on any startup error which
        # would kill the entire uvicorn process.
        for addon in list(master.addons.chain):
            if isinstance(addon, _errorcheck.ErrorCheck):
                master.addons.remove(addon)
                break
        master.addons.add(_addon)
        _master = master
        try:
            loop.run_until_complete(master.run())
        except Exception as exc:
            logger.info("mitmproxy stopped: %s", exc)
        finally:
            _master = None
            loop.close()

    _thread = threading.Thread(target=_run, name="mitmproxy", daemon=True)
    _thread.start()
    logger.info(
        "Proxy started on %s:%d", settings.proxy.bind_addr, settings.proxy.port
    )


def stop_proxy() -> None:
    global _master, _thread, _addon
    if _master is not None:
        try:
            _master.shutdown()
        except Exception as exc:
            logger.debug("Proxy shutdown error: %s", exc)
        if _thread is not None:
            _thread.join(timeout=3)
        _master = None
        _thread = None
        _addon = None
        logger.info("Proxy stopped.")


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
