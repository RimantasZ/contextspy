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

import json
import gzip
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

from mitmproxy import http

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import AnalyzedRequest
from contextspy.analysis.classifier import CategoryBreakdown, classify, per_tool_tokens
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
    # GitHub Copilot — covers both the legacy proxy host and the current API domain
    # (*.githubcopilot.com catches api.githubcopilot.com, telemetry.githubcopilot.com, etc.)
    ("copilot-proxy.githubusercontent.com", "copilot"),
    ("githubcopilot.com", "copilot"),
    # opencode's "zen" gateway relays to upstream models (e.g. Claude) over the
    # Anthropic/OpenAI wire format. Dispatch is endpoint-based, so the gateway path
    # (/zen/v1/messages, /zen/v1/chat/completions) is parsed by the right parser.
    ("opencode.ai", "opencode_zen"),
    # Codex CLI authenticated via a ChatGPT plan (rather than an OPENAI_API_KEY)
    # sends its actual completions to the undocumented chatgpt.com/backend-api/codex/responses
    # endpoint instead of api.openai.com. Host mapping is broad (chatgpt.com serves lots of
    # non-LLM traffic — analytics-events, wham/usage, otlp/metrics) but that's fine: the
    # endpoint-pattern gate in _save_request/get_adapter still filters those out.
    ("chatgpt.com", "openai_chatgpt"),
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
    def __init__(self, provider_override: str | None = None) -> None:
        self.ws_manager: ConnectionManager | None = None
        # When set, skip host-based detection and always use this provider.
        # Used by reverse-proxy mode where the upstream is a known local server.
        self._provider_override = provider_override

    def _get_provider(self, host: str, port: int) -> str | None:
        if self._provider_override is not None:
            return self._provider_override
        return _detect_provider(host, port)

    def request(self, flow: http.HTTPFlow) -> None:
        flow.metadata["ts_start"] = time.monotonic()
        logger.debug("HOOK request: %s %s", flow.request.pretty_host, flow.request.path[:60])

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        ct = flow.response.headers.get("content-type", "").lower()
        if "text/event-stream" not in ct:
            return
        # SSE streaming response — buffer all chunks, process when stream ends
        host = flow.request.pretty_host
        port = flow.request.port
        if self._get_provider(host, port) is None:
            return  # not an LLM host — skip overhead

        sse_chunks: list[bytes] = []
        addon = self

        def _collect(data: bytes) -> bytes:
            if data:
                if "ts_first_chunk" not in flow.metadata:
                    flow.metadata["ts_first_chunk"] = time.monotonic()
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
        provider = self._get_provider(host, port)
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

        ttft_ms: int | None = None
        if "ts_start" in flow.metadata and "ts_first_chunk" in flow.metadata:
            ttft_ms = int((flow.metadata["ts_first_chunk"] - flow.metadata["ts_start"]) * 1000)

        adapter = get_adapter(endpoint)
        analyzed: AnalyzedRequest | None = None
        if adapter is not None:
            try:
                input_blocks, tool_call_map = adapter.parse_request(req_body)
                output_blocks, usage = adapter.parse_sse(raw_sse)
                analyzed = AnalyzedRequest(
                    model=req_body.get("model"),
                    input_blocks=input_blocks,
                    output_blocks=output_blocks,
                    usage=usage,
                    tool_call_map=tool_call_map,
                )
            except Exception as exc:
                logger.warning("Adapter parse error (sse): %s", exc, exc_info=True)

        # Store a clean synthetic JSON response (not raw SSE with all data: lines)
        raw_resp_text = self._synthetic_response_text(
            analyzed, raw_sse.decode("utf-8", errors="replace")
        )

        self._save_request(flow, provider, agent, endpoint, req_body, analyzed,
                           duration_ms, raw_resp_text, ttft_ms=ttft_ms)

    @staticmethod
    def _synthetic_response_text(analyzed: AnalyzedRequest | None, fallback: str) -> str:
        """Build a clean synthetic JSON response from parsed SSE output (not raw
        SSE with all ``data:`` lines), falling back to the raw text if parsing failed."""
        if analyzed is None:
            return fallback
        synthetic: dict = {
            "choices": [{
                "message": {"role": "assistant", "content": analyzed.response_text},
                "finish_reason": "stop",
            }],
        }
        if analyzed.model:
            synthetic["model"] = analyzed.model
        if analyzed.usage.input_tokens is not None or analyzed.usage.output_tokens is not None:
            synthetic["usage"] = {
                "prompt_tokens": analyzed.usage.input_tokens,
                "completion_tokens": analyzed.usage.output_tokens,
            }
        return json.dumps(synthetic, ensure_ascii=False)

    def _handle_response(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        host = flow.request.pretty_host
        port = flow.request.port
        provider = self._get_provider(host, port)
        if provider is None:
            return

        endpoint = flow.request.path
        user_agent = flow.request.headers.get("user-agent", "")
        agent = _detect_agent(user_agent)

        try:
            req_body = json.loads(flow.request.get_text() or "{}")
        except json.JSONDecodeError:
            req_body = {}

        resp_text = flow.response.get_text() or ""
        # Some providers (e.g. Codex's chatgpt.com/backend-api/codex backend) send
        # SSE-formatted bodies without a recognizable "text/event-stream" content-type,
        # so responseheaders() never routes them through the streaming buffer path —
        # falling back to json.loads() here would silently drop all output/usage data.
        resp_head = resp_text.lstrip()
        is_sse = resp_head.startswith("data:") or resp_head.startswith("event:")
        resp_body: dict = {}
        if not is_sse:
            try:
                resp_body = json.loads(resp_text or "{}")
            except json.JSONDecodeError:
                resp_body = {}

        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        adapter = get_adapter(endpoint)
        analyzed: AnalyzedRequest | None = None
        if adapter is not None:
            try:
                input_blocks, tool_call_map = adapter.parse_request(req_body)
                if is_sse:
                    output_blocks, usage = adapter.parse_sse(resp_text.encode("utf-8"))
                else:
                    output_blocks, usage = adapter.parse_response(resp_body)
                analyzed = AnalyzedRequest(
                    model=req_body.get("model"),
                    input_blocks=input_blocks,
                    output_blocks=output_blocks,
                    usage=usage,
                    tool_call_map=tool_call_map,
                )
            except Exception as exc:
                logger.warning("Adapter parse error: %s", exc, exc_info=True)

        raw_resp_text = (
            self._synthetic_response_text(analyzed, resp_text) if is_sse else resp_text
        )
        self._save_request(flow, provider, agent, endpoint, req_body, analyzed,
                           duration_ms, raw_resp_text)

    def _save_request(self, flow: http.HTTPFlow, provider: str, agent: str,
                      endpoint: str, req_body: dict, analyzed: AnalyzedRequest | None,
                      duration_ms: int | None, raw_resp_text: str | None,
                      ttft_ms: int | None = None) -> None:
        # Skip non-LLM endpoints (telemetry, auth, health checks, etc.)
        # Only persist requests that we could actually parse OR that look like
        # known LLM API paths so telemetry traffic is not stored as empty rows.
        _LLM_PATHS = ("/chat/completions", "/completions", "/messages", "/responses",
                      "/api/chat", "/api/generate")
        if analyzed is None and not any(p in endpoint for p in _LLM_PATHS):
            logger.debug("Skipping non-LLM endpoint: %s %s", provider, endpoint)
            return

        if analyzed is not None:
            breakdown = classify(analyzed)
            model = analyzed.model
            usage = analyzed.usage
            provider_input = usage.input_tokens
            provider_output = usage.output_tokens
            provider_reasoning = usage.reasoning_tokens
            cache_read = usage.cache_read_tokens
            cache_creation = usage.cache_creation_tokens
            usage_extra = json.dumps(usage.extra) if usage.extra else None
        else:
            breakdown = CategoryBreakdown()
            model = req_body.get("model")
            provider_input = None
            provider_output = None
            provider_reasoning = None
            cache_read = None
            cache_creation = None
            usage_extra = None

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
                "ttft_ms": ttft_ms,
                "status_code": flow.response.status_code if flow.response else None,
                "provider_input_tokens": provider_input,
                "provider_output_tokens": provider_output,
                "provider_reasoning_tokens": provider_reasoning,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "usage_extra": usage_extra,
                "raw_request_body": flow.request.get_text(),
                "raw_response_body": raw_resp_text,
            }
            data.update(breakdown.to_db_fields())
            req_record = crud.create_request(db, data)

            if analyzed is not None:
                all_blocks = analyzed.input_blocks + analyzed.output_blocks
                if all_blocks:
                    crud.insert_blocks(db, req_record.id, all_blocks)

                tool_rows = per_tool_tokens(analyzed)
                if tool_rows:
                    crud.upsert_tool_stats(db, req_record.id, tool_rows)

            # Serialise while the session is still open to avoid detached-instance errors
            ws_payload = req_record.to_dict(include_raw=False)

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
                        {"event": "new_request", "data": ws_payload}
                    ),
                    self.ws_manager.loop,
                )
            except Exception as exc:
                logger.debug("WebSocket broadcast error: %s", exc)
