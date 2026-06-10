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
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.addons import errorcheck as _errorcheck

from contextspy.proxy.addon import ContextSpyAddon

if TYPE_CHECKING:
    from contextspy.api.websocket import ConnectionManager
    from contextspy.config import Settings, ReverseTarget

logger = logging.getLogger(__name__)

_master: DumpMaster | None = None
_thread: threading.Thread | None = None
_addon: ContextSpyAddon | None = None
_bound: bool = False  # True only after mitmproxy successfully binds the port

# Reverse-proxy masters (one per [[reverse_targets]] entry)
_reverse_masters: list[DumpMaster] = []
_reverse_threads: list[threading.Thread] = []


def _make_master(
    options: Options,
    addon: ContextSpyAddon,
    bind_log_port: int,
    on_bound: "callable[[], None] | None" = None,
) -> tuple[DumpMaster, threading.Thread]:
    """Build and return a (DumpMaster, Thread) pair without starting the thread."""

    def _run() -> None:
        import asyncio
        import logging as _logging

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        master = DumpMaster(options, loop=loop, with_termlog=False, with_dumper=False)
        for a in list(master.addons.chain):
            if isinstance(a, _errorcheck.ErrorCheck):
                master.addons.remove(a)
                break

        class _BindWatcher(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                msg = record.getMessage()
                if "listening at" in msg:
                    logger.info("Proxy bound (port %d): %s", bind_log_port, msg)
                    if on_bound:
                        on_bound()
                elif "failed to listen" in msg or "error while attempting to bind" in msg:
                    logger.error(
                        "Proxy FAILED to bind on port %d — is another process using it?",
                        bind_log_port,
                    )

        watcher = _BindWatcher()
        _logging.getLogger("mitmproxy").addHandler(watcher)
        master.addons.add(addon)
        try:
            loop.run_until_complete(master.run())
        except Exception as exc:
            logger.info("mitmproxy stopped (port %d): %s", bind_log_port, exc)
        finally:
            _logging.getLogger("mitmproxy").removeHandler(watcher)
            loop.close()

    thread = threading.Thread(target=_run, name=f"mitmproxy-{bind_log_port}", daemon=True)
    # We keep a reference to the master only after the loop sets it inside _run.
    # Return a sentinel; callers that need the master should call start_proxy /
    # start_local_proxies which manage _master / _reverse_masters directly.
    return thread  # type: ignore[return-value]


def start_proxy(settings: "Settings", ws_manager: "ConnectionManager | None" = None) -> None:
    global _master, _thread, _addon, _bound

    if _master is not None:
        return  # already running

    _addon = ContextSpyAddon()
    _addon.ws_manager = ws_manager

    options = Options(
        listen_host=settings.proxy.bind_addr,
        listen_port=settings.proxy.port,
        ssl_insecure=False,
    )

    def _run() -> None:
        global _master, _bound
        import asyncio
        import logging as _logging

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        master = DumpMaster(options, loop=loop, with_termlog=False, with_dumper=False)
        for a in list(master.addons.chain):
            if isinstance(a, _errorcheck.ErrorCheck):
                master.addons.remove(a)
                break

        class _BindWatcher(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                global _bound
                msg = record.getMessage()
                if "listening at" in msg:
                    _bound = True
                    logger.info("Proxy bound: %s", msg)
                elif "failed to listen" in msg or "error while attempting to bind" in msg:
                    logger.error(
                        "Proxy FAILED to bind on port %d — is another process using it? "
                        "Set a different port with --proxy-port or edit ~/.contextspy/config.toml",
                        settings.proxy.port,
                    )

        class _MitmForwarder(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                logger.debug("mitmproxy[%s]: %s", record.levelname, record.getMessage())

        watcher = _BindWatcher()
        mitm_logger = _logging.getLogger("mitmproxy")
        mitm_logger.addHandler(watcher)
        mitm_logger.addHandler(_MitmForwarder())
        mitm_logger.setLevel(_logging.DEBUG)
        master.addons.add(_addon)
        _master = master
        try:
            loop.run_until_complete(master.run())
        except Exception as exc:
            logger.info("mitmproxy stopped: %s", exc)
        finally:
            _master = None
            _bound = False
            _logging.getLogger("mitmproxy").removeHandler(watcher)
            loop.close()

    _thread = threading.Thread(target=_run, name="mitmproxy", daemon=True)
    _thread.start()
    logger.info("Proxy started on %s:%d", settings.proxy.bind_addr, settings.proxy.port)


def start_local_proxies(
    settings: "Settings",
    ws_manager: "ConnectionManager | None" = None,
) -> None:
    """Start one reverse-mode mitmproxy instance per [[reverse_targets]] entry."""
    global _reverse_masters, _reverse_threads

    if not settings.reverse_targets:
        logger.warning("start_local_proxies called but no [[reverse_targets]] are configured.")
        return

    for target in settings.reverse_targets:
        addon = ContextSpyAddon(provider_override=target.provider)
        addon.ws_manager = ws_manager

        options = Options(
            listen_host=settings.proxy.bind_addr,
            listen_port=target.listen_port,
            mode=[f"reverse:{target.target_url}"],
            ssl_insecure=True,  # target is local HTTP — no cert needed
        )

        master_ref: list[DumpMaster] = []

        def _run(
            _options: Options = options,
            _addon: ContextSpyAddon = addon,
            _target: "ReverseTarget" = target,
            _master_ref: list[DumpMaster] = master_ref,
        ) -> None:
            import asyncio
            import logging as _logging

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            master = DumpMaster(_options, loop=loop, with_termlog=False, with_dumper=False)
            for a in list(master.addons.chain):
                if isinstance(a, _errorcheck.ErrorCheck):
                    master.addons.remove(a)
                    break

            class _BindWatcher(_logging.Handler):
                def emit(self, record: _logging.LogRecord) -> None:
                    msg = record.getMessage()
                    if "listening at" in msg:
                        logger.info(
                            "Reverse proxy [%s] bound on port %d → %s",
                            _target.name, _target.listen_port, _target.target_url,
                        )
                    elif "failed to listen" in msg or "error while attempting to bind" in msg:
                        logger.error(
                            "Reverse proxy [%s] FAILED to bind on port %d",
                            _target.name, _target.listen_port,
                        )

            watcher = _BindWatcher()
            _logging.getLogger("mitmproxy").addHandler(watcher)
            master.addons.add(_addon)
            _master_ref.append(master)
            _reverse_masters.append(master)
            try:
                loop.run_until_complete(master.run())
            except Exception as exc:
                logger.info("Reverse proxy [%s] stopped: %s", _target.name, exc)
            finally:
                _logging.getLogger("mitmproxy").removeHandler(watcher)
                loop.close()

        t = threading.Thread(
            target=_run,
            name=f"mitmproxy-reverse-{target.name}",
            daemon=True,
        )
        _reverse_threads.append(t)
        t.start()
        logger.info(
            "Reverse proxy [%s] starting: localhost:%d → %s (provider=%s)",
            target.name, target.listen_port, target.target_url, target.provider,
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


def stop_local_proxies() -> None:
    global _reverse_masters, _reverse_threads
    for master in _reverse_masters:
        try:
            master.shutdown()
        except Exception as exc:
            logger.debug("Reverse proxy shutdown error: %s", exc)
    for t in _reverse_threads:
        t.join(timeout=3)
    _reverse_masters = []
    _reverse_threads = []
    logger.info("All reverse proxies stopped.")


def is_running() -> bool:
    return _bound and _thread is not None and _thread.is_alive()


def local_proxies_running() -> list[str]:
    """Return names of reverse proxy threads that are still alive."""
    return [t.name for t in _reverse_threads if t.is_alive()]
