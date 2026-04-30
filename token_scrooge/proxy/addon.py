from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

from mitmproxy import http

from token_scrooge.analysis.classifier import classify
from token_scrooge.analysis.providers import parse_request
from token_scrooge.db import crud
from token_scrooge.db.database import get_db

if TYPE_CHECKING:
    from token_scrooge.api.websocket import ConnectionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Host → provider mapping
# ---------------------------------------------------------------------------

_HOST_PROVIDER: list[tuple[str, str]] = [
    ("api.openai.com", "openai"),
    ("openai.azure.com", "openai_azure"),
    ("api.anthropic.com", "anthropic"),
    ("copilot-proxy.githubusercontent.com", "copilot"),
]
_OLLAMA_PORTS = {11434}


def _detect_provider(host: str, port: int) -> str | None:
    if port in _OLLAMA_PORTS:
        return "ollama"
    for pattern, provider in _HOST_PROVIDER:
        if host == pattern or host.endswith("." + pattern):
            return provider
    return None


# ---------------------------------------------------------------------------
# User-Agent → agent mapping
# ---------------------------------------------------------------------------

_UA_AGENTS: list[tuple[str, str]] = [
    ("githubcopilot", "github_copilot"),
    ("github-copilot", "github_copilot"),
    ("anthropic-python", "claude_sdk"),
    ("openai-python", "openai_sdk"),
    ("opencode", "opencode"),
    ("cursor", "cursor"),
]


def _detect_agent(user_agent: str) -> str:
    ua_lower = user_agent.lower()
    for pattern, agent in _UA_AGENTS:
        if pattern in ua_lower:
            return agent
    return "unknown"


# ---------------------------------------------------------------------------
# Addon
# ---------------------------------------------------------------------------

class TokenScroogeAddon:
    def __init__(self) -> None:
        self.ws_manager: ConnectionManager | None = None

    def request(self, flow: http.HTTPFlow) -> None:
        flow.metadata["ts_start"] = time.monotonic()

    def response(self, flow: http.HTTPFlow) -> None:
        try:
            self._handle_response(flow)
        except Exception as exc:
            logger.warning("TokenScroogeAddon error: %s", exc, exc_info=True)

    def _handle_response(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        port = flow.request.pretty_port
        provider = _detect_provider(host, port)
        if provider is None:
            return  # not an LLM host

        endpoint = flow.request.path
        user_agent = flow.request.headers.get("user-agent", "")
        agent = _detect_agent(user_agent)

        # Parse bodies
        try:
            req_body = json.loads(flow.request.get_text() or "{}")
        except json.JSONDecodeError:
            req_body = {}
        try:
            resp_body = json.loads(flow.response.get_text() or "{}")
        except json.JSONDecodeError:
            resp_body = {}

        # Duration
        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        parsed = parse_request(provider, endpoint, req_body, resp_body)
        if parsed is not None:
            breakdown = classify(parsed)
            model = parsed.model
            provider_input = parsed.provider_input_tokens
            provider_output = parsed.provider_output_tokens
        else:
            from token_scrooge.analysis.classifier import CategoryBreakdown
            breakdown = CategoryBreakdown()
            model = req_body.get("model")
            provider_input = None
            provider_output = None

        with get_db() as db:
            active_session = crud.get_active_session(db)
            session_id = active_session.id if active_session else None

            data: dict = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc),
                "provider": provider,
                "model": model,
                "agent": agent,
                "endpoint": endpoint,
                "duration_ms": duration_ms,
                "status_code": flow.response.status_code,
                "provider_input_tokens": provider_input,
                "provider_output_tokens": provider_output,
                "raw_request_body": flow.request.get_text(),
                "raw_response_body": flow.response.get_text(),
            }
            data.update(breakdown.to_db_fields())
            req_record = crud.create_request(db, data)

        if self.ws_manager is not None:
            try:
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    self.ws_manager.broadcast(
                        {"event": "new_request", "data": req_record.to_dict(include_raw=False)}
                    ),
                    self.ws_manager.loop,
                )
            except Exception as exc:
                logger.debug("WebSocket broadcast error: %s", exc)
