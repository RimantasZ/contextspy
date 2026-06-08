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

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from contextspy.proxy import runner
from contextspy.proxy.cert import cert_exists, install_cert

router = APIRouter(tags=["proxy"])


@router.get("/proxy/status")
def proxy_status():
    from contextspy.api.main import create_app  # noqa: F401 – to get settings
    import contextspy.api.main as _main
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
    import contextspy.api.main as _main
    settings = getattr(_main, "_cached_settings", None)
    if settings is None:
        from contextspy.config import Settings
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


@router.get("/proxy.pac", response_class=PlainTextResponse)
def proxy_pac(request: Request) -> PlainTextResponse:
    from contextspy.proxy.addon import _HOST_PROVIDER

    settings = request.app.state.settings
    proxy_host_port = f"127.0.0.1:{settings.proxy.port}"

    lines = [
        f'    if (shExpMatch(host, "{host}") || shExpMatch(host, "*.{host}")) '
        f'return "PROXY {proxy_host_port}";'
        for host, _ in _HOST_PROVIDER
    ]
    body = "\n".join(lines)
    content = (
        f'function FindProxyForURL(url, host) {{\n{body}\n    return "DIRECT";\n}}\n'
    )
    return PlainTextResponse(content, media_type="application/x-ns-proxy-autoconfig")
