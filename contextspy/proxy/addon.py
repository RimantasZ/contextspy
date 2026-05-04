from __future__ import annotations

import json
import gzip
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

from mitmproxy import http

from contextspy.analysis.classifier import classify, per_tool_tokens
from contextspy.analysis.providers import ParsedRequest, parse_request, parse_sse_request
from contextspy.db import crud
from contextspy.db.database import get_db

if TYPE_CHECKING:
    from contextspy.api.websocket import ConnectionManager

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

class ContextSpyAddon:
    def __init__(self) -> None:
        self.ws_manager: ConnectionManager | None = None

    def request(self, flow: http.HTTPFlow) -> None:
        flow.metadata["ts_start"] = time.monotonic()
        logger.debug("HOOK request: %s %s", flow.request.pretty_host, flow.request.path[:60])

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        ct = flow.response.headers.get("content-type", "")
        if "text/event-stream" not in ct:
            return
        # SSE streaming response — buffer all chunks, process when stream ends
        host = flow.request.pretty_host
        port = flow.request.port
        if _detect_provider(host, port) is None:
            return  # not an LLM host — skip overhead

        sse_chunks: list[bytes] = []
        addon = self

        def _collect(data: bytes) -> bytes:
            if data:
                sse_chunks.append(data)
            else:
                # Empty bytes signals end of stream
                raw = b"".join(sse_chunks)
                try:
                    addon._handle_sse_response(flow, raw)
                except Exception as exc:
                    logger.warning("SSE handler error: %s", exc, exc_info=True)
            return data

        flow.metadata["is_sse"] = True
        flow.response.stream = _collect

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("is_sse"):
            return  # handled by the SSE stream callback
        try:
            self._handle_response(flow)
        except Exception as exc:
            logger.warning("ContextSpyAddon error: %s", exc, exc_info=True)

    def _handle_sse_response(self, flow: http.HTTPFlow, raw_sse: bytes) -> None:
        # Decompress if the response was content-encoded
        if flow.response:
            encoding = flow.response.headers.get("content-encoding", "").lower()
            if encoding == "gzip":
                try:
                    raw_sse = gzip.decompress(raw_sse)
                except Exception:
                    pass
            elif encoding in ("deflate", "zlib"):
                import zlib
                try:
                    raw_sse = zlib.decompress(raw_sse)
                except Exception:
                    try:
                        raw_sse = zlib.decompress(raw_sse, -zlib.MAX_WBITS)
                    except Exception:
                        pass
            elif encoding == "br":
                try:
                    import brotli  # type: ignore
                    raw_sse = brotli.decompress(raw_sse)
                except Exception:
                    pass

        host = flow.request.pretty_host
        port = flow.request.port
        provider = _detect_provider(host, port)
        if provider is None:
            return

        endpoint = flow.request.path
        user_agent = flow.request.headers.get("user-agent", "")
        agent = _detect_agent(user_agent)

        try:
            req_body = json.loads(flow.request.get_text() or "{}")
        except json.JSONDecodeError:
            req_body = {}

        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        parsed = parse_sse_request(provider, endpoint, req_body, raw_sse)
        self._save_request(flow, provider, agent, endpoint, req_body, parsed,
                           duration_ms, raw_sse.decode("utf-8", errors="replace"))

    def _handle_response(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        host = flow.request.pretty_host
        port = flow.request.port
        provider = _detect_provider(host, port)
        if provider is None:
            return

        endpoint = flow.request.path
        user_agent = flow.request.headers.get("user-agent", "")
        agent = _detect_agent(user_agent)

        try:
            req_body = json.loads(flow.request.get_text() or "{}")
        except json.JSONDecodeError:
            req_body = {}
        try:
            resp_body = json.loads(flow.response.get_text() or "{}")
        except json.JSONDecodeError:
            resp_body = {}

        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        parsed = parse_request(provider, endpoint, req_body, resp_body)
        self._save_request(flow, provider, agent, endpoint, req_body, parsed,
                           duration_ms, flow.response.get_text() if flow.response else None)

    def _save_request(self, flow: http.HTTPFlow, provider: str, agent: str,
                      endpoint: str, req_body: dict, parsed: ParsedRequest | None,
                      duration_ms: int | None, raw_resp_text: str | None) -> None:
        if parsed is not None:
            breakdown = classify(parsed)
            model = parsed.model
            provider_input = parsed.provider_input_tokens
            provider_output = parsed.provider_output_tokens
        else:
            from contextspy.analysis.classifier import CategoryBreakdown
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
                "status_code": flow.response.status_code if flow.response else None,
                "provider_input_tokens": provider_input,
                "provider_output_tokens": provider_output,
                "raw_request_body": flow.request.get_text(),
                "raw_response_body": raw_resp_text,
            }
            data.update(breakdown.to_db_fields())
            req_record = crud.create_request(db, data)

            # Per-tool breakdown
            if parsed is not None:
                tool_rows = per_tool_tokens(parsed)
                if tool_rows:
                    crud.upsert_tool_stats(db, req_record.id, tool_rows)

        ts_str = data["timestamp"].strftime("%H:%M:%S")
        logger.info(
            "[%s] %s › %s | model=%s | in=%d out=%d tokens | %s",
            ts_str,
            provider,
            agent,
            model or "?",
            data.get("tokens_total_input", 0),
            data.get("tokens_total_output", 0),
            f"{duration_ms}ms" if duration_ms is not None else "?ms",
        )

        if self.ws_manager is not None and self.ws_manager.loop is not None:
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
