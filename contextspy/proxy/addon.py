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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

from mitmproxy import http

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import AnalyzedRequest, Usage
from contextspy.analysis.capture import decode_ndjson, decode_sse
from contextspy.analysis.classifier import CategoryBreakdown, classify, per_tool_tokens
from contextspy.analysis.context_reconstruction import (
    reconcile_unresolved_descendants,
    reconstruct_context,
)
from contextspy.analysis.conversation import (
    ContextMutation,
    InvocationIdentity,
    get_conversation_adapter,
)
from contextspy.db import crud
from contextspy.db.database import get_db
from contextspy.proxy.ws_protocols import CompletedExchange, WsSession, get_ws_protocol

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
    ("codex-tui", "codex"),
    ("codex desktop", "codex"),
    ("codex_cli_rs", "codex"),
    ("claude-code", "claude_code"),
    ("claude-cli", "claude_code")
]


def _detect_agent(user_agent: str) -> str:
    ua_lower = user_agent.lower()
    for pattern, agent in _UA_AGENTS:
        if pattern in ua_lower:
            return agent
    logger.debug("unmatched user-agent: %r", ua_lower)
    return "unknown"


def _add_capture_error(
    current: dict | None, stage: str, error: Exception | str,
) -> dict:
    """Accumulate independent capture/reconstruction/analysis failures."""
    issue = {"stage": stage, "message": str(error)}
    if current is None:
        return issue
    current.setdefault("additional", []).append(issue)
    return current


def _captured_request_text(flow) -> tuple[str | None, str | None]:
    """Return the request-hook snapshot, with a safe late-capture fallback."""
    raw = flow.metadata.get("contextspy_request_body")
    error = flow.metadata.get("contextspy_request_capture_error")
    if raw is None and error is None:
        try:
            raw = flow.request.get_text()
        except Exception as exc:
            error = str(exc)
    return raw, error


# ---------------------------------------------------------------------------
# Addon
# ---------------------------------------------------------------------------

@dataclass
class _WsFlowState:
    """Per-connection state for the lifetime of one WebSocket flow."""

    session: WsSession
    provider: str
    agent: str
    endpoint: str


class ContextSpyAddon:
    def __init__(self, provider_override: str | None = None) -> None:
        self.ws_manager: ConnectionManager | None = None
        # When set, skip host-based detection and always use this provider.
        # Used by reverse-proxy mode where the upstream is a known local server.
        self._provider_override = provider_override
        # Keyed by flow.id — hooks run on the addon's own DumpMaster event loop
        # (single-threaded), so no locking is needed around this dict.
        self._ws_flows: dict[str, _WsFlowState] = {}

    def _get_provider(self, host: str, port: int) -> str | None:
        if self._provider_override is not None:
            return self._provider_override
        return _detect_provider(host, port)

    def request(self, flow: http.HTTPFlow) -> None:
        flow.metadata["ts_start"] = time.monotonic()
        try:
            flow.metadata["contextspy_request_body"] = flow.request.get_text()
        except Exception as exc:
            flow.metadata["contextspy_request_capture_error"] = str(exc)
        logger.debug("HOOK request: %s %s", flow.request.pretty_host, flow.request.path[:60])

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        ct = flow.response.headers.get("content-type", "").lower()
        logger.debug(
            "HOOK responseheaders: %s %s status=%s content-type=%r",
            flow.request.pretty_host, flow.request.path[:60],
            flow.response.status_code, ct,
        )
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
        if flow.websocket is not None:
            return  # the 101 upgrade response itself — real traffic goes through the ws_* hooks
        if flow.metadata.get("is_sse"):
            return  # handled by the SSE stream callback
        if flow.response is None:
            return  # no response to process (e.g., connection error)
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

        raw_request_body, request_capture_error = _captured_request_text(flow)
        request_decode_error: Exception | str | None = None
        try:
            decoded_request = json.loads(raw_request_body or "{}")
            if not isinstance(decoded_request, dict):
                request_decode_error = "JSON request is not an object"
                req_body = {}
            else:
                req_body = decoded_request
        except json.JSONDecodeError as exc:
            req_body = {}
            request_decode_error = exc

        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        ttft_ms: int | None = None
        if "ts_start" in flow.metadata and "ts_first_chunk" in flow.metadata:
            ttft_ms = int((flow.metadata["ts_first_chunk"] - flow.metadata["ts_start"]) * 1000)

        adapter = get_adapter(endpoint)
        analyzed: AnalyzedRequest | None = None
        response_events: str | None = None
        response_reconstructed = False
        response_complete = True
        capture_error: dict | None = None
        if request_capture_error is not None:
            capture_error = _add_capture_error(
                capture_error, "request_capture", request_capture_error,
            )
        if request_decode_error is not None:
            capture_error = _add_capture_error(
                capture_error, "request_decode", request_decode_error,
            )
        raw_resp_text = raw_sse.decode("utf-8", errors="replace")
        events = decode_sse(raw_sse)
        if events:
            response_events = json.dumps(
                [event.to_dict() for event in events], ensure_ascii=False,
            )
        canonical_payload: dict | None = None
        if adapter is not None:
            try:
                canonical = adapter.reconstruct_response(events, transport="sse")
                canonical_payload = canonical.payload
                raw_resp_text = json.dumps(canonical.payload, ensure_ascii=False)
                response_reconstructed = canonical.reconstructed
                response_complete = canonical.complete
            except Exception as exc:
                logger.warning("Adapter reconstruction error (sse): %s", exc, exc_info=True)
                response_complete = False
                capture_error = _add_capture_error(capture_error, "sse_reconstruction", exc)

            input_blocks = []
            tool_call_map: dict[str, str] = {}
            output_blocks = []
            usage = Usage()
            try:
                input_blocks, tool_call_map = adapter.parse_request(req_body)
            except Exception as exc:
                logger.warning("Adapter request parse error (sse): %s", exc, exc_info=True)
                capture_error = _add_capture_error(capture_error, "request_analysis", exc)
            if canonical_payload is not None:
                try:
                    output_blocks, usage = adapter.parse_response(canonical_payload)
                except Exception as exc:
                    logger.warning("Adapter response parse error (sse): %s", exc, exc_info=True)
                    capture_error = _add_capture_error(capture_error, "response_analysis", exc)
            analyzed = AnalyzedRequest(
                model=req_body.get("model"),
                input_blocks=input_blocks,
                output_blocks=output_blocks,
                usage=usage,
                tool_call_map=tool_call_map,
            )

        self._save_request(
            provider=provider, agent=agent, endpoint=endpoint, req_body=req_body,
            analyzed=analyzed, duration_ms=duration_ms, raw_resp_text=raw_resp_text,
            status_code=flow.response.status_code if flow.response else None,
            raw_request_body=raw_request_body, ttft_ms=ttft_ms,
            response_transport="sse", response_reconstructed=response_reconstructed,
            response_complete=response_complete, response_events=response_events,
            capture_error=capture_error,
        )
        flow.metadata["contextspy_saved"] = True

    def _handle_response(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return
        host = flow.request.pretty_host
        port = flow.request.port
        provider = self._get_provider(host, port)
        logger.debug(
            "HOOK response: %s %s status=%s provider=%s",
            host, flow.request.path[:60], flow.response.status_code, provider,
        )
        if provider is None:
            return

        endpoint = flow.request.path
        user_agent = flow.request.headers.get("user-agent", "")
        agent = _detect_agent(user_agent)

        raw_request_body, request_capture_error = _captured_request_text(flow)
        request_decode_error: Exception | str | None = None
        try:
            decoded_request = json.loads(raw_request_body or "{}")
            if not isinstance(decoded_request, dict):
                request_decode_error = "JSON request is not an object"
                req_body = {}
            else:
                req_body = decoded_request
        except json.JSONDecodeError as exc:
            req_body = {}
            request_decode_error = exc

        resp_text = flow.response.get_text() or ""
        # Some providers (e.g. Codex's chatgpt.com/backend-api/codex backend) send
        # SSE-formatted bodies without a recognizable "text/event-stream" content-type,
        # so responseheaders() never routes them through the streaming buffer path —
        # falling back to json.loads() here would silently drop all output/usage data.
        resp_head = resp_text.lstrip()
        is_sse = resp_head.startswith("data:") or resp_head.startswith("event:")
        resp_body: dict | None = None
        response_is_json = False
        if not is_sse and resp_text:
            try:
                decoded_response = json.loads(resp_text or "{}")
                response_is_json = True
                if isinstance(decoded_response, dict):
                    resp_body = decoded_response
            except json.JSONDecodeError:
                pass

        duration_ms: int | None = None
        if "ts_start" in flow.metadata:
            duration_ms = int((time.monotonic() - flow.metadata["ts_start"]) * 1000)

        adapter = get_adapter(endpoint)
        content_type = flow.response.headers.get("content-type", "").lower()
        is_ndjson = bool(
            adapter is not None
            and adapter.stream_format == "ndjson"
            and (
                "\n" in resp_text.strip()
                or "application/x-ndjson" in content_type
                or "application/jsonl" in content_type
            )
        )
        logger.debug(
            "response body: len=%d is_sse=%s adapter=%s",
            len(resp_text), is_sse, type(adapter).__name__ if adapter else None,
        )
        analyzed: AnalyzedRequest | None = None
        response_events: str | None = None
        response_reconstructed = False
        response_complete = True
        response_transport = (
            "sse" if is_sse else "ndjson" if is_ndjson else "json" if response_is_json else "text"
        )
        capture_error: dict | None = None
        if request_capture_error is not None:
            capture_error = _add_capture_error(
                capture_error, "request_capture", request_capture_error,
            )
        if request_decode_error is not None:
            capture_error = _add_capture_error(
                capture_error, "request_decode", request_decode_error,
            )
        raw_resp_text = resp_text
        canonical_payload = resp_body
        if is_sse or is_ndjson:
            events = decode_sse(resp_text.encode("utf-8")) if is_sse else decode_ndjson(
                resp_text.encode("utf-8")
            )
            if events:
                response_events = json.dumps(
                    [event.to_dict() for event in events], ensure_ascii=False,
                )
            if adapter is not None:
                try:
                    canonical = adapter.reconstruct_response(
                        events, transport="sse" if is_sse else "ndjson",
                    )
                    canonical_payload = canonical.payload
                    raw_resp_text = json.dumps(canonical.payload, ensure_ascii=False)
                    response_reconstructed = canonical.reconstructed
                    response_complete = canonical.complete
                except Exception as exc:
                    logger.warning("Adapter response reconstruction error: %s", exc, exc_info=True)
                    canonical_payload = None
                    response_complete = False
                    capture_error = _add_capture_error(
                        capture_error, "response_reconstruction", exc,
                    )
        if adapter is not None:
            input_blocks = []
            tool_call_map: dict[str, str] = {}
            output_blocks = []
            usage = Usage()
            try:
                input_blocks, tool_call_map = adapter.parse_request(req_body)
            except Exception as exc:
                logger.warning("Adapter request parse error: %s", exc, exc_info=True)
                capture_error = _add_capture_error(capture_error, "request_analysis", exc)
            if canonical_payload is not None:
                try:
                    output_blocks, usage = adapter.parse_response(canonical_payload)
                except Exception as exc:
                    logger.warning("Adapter response parse error: %s", exc, exc_info=True)
                    capture_error = _add_capture_error(capture_error, "response_analysis", exc)
            elif response_is_json:
                capture_error = _add_capture_error(
                    capture_error,
                    "response_shape",
                    "JSON response is not an object",
                )
            analyzed = AnalyzedRequest(
                model=req_body.get("model"),
                input_blocks=input_blocks,
                output_blocks=output_blocks,
                usage=usage,
                tool_call_map=tool_call_map,
            )

        self._save_request(
            provider=provider, agent=agent, endpoint=endpoint, req_body=req_body,
            analyzed=analyzed, duration_ms=duration_ms, raw_resp_text=raw_resp_text,
            status_code=flow.response.status_code if flow.response else None,
            raw_request_body=raw_request_body,
            response_transport=response_transport,
            response_reconstructed=response_reconstructed,
            response_complete=response_complete,
            response_events=response_events,
            capture_error=capture_error,
        )
        flow.metadata["contextspy_saved"] = True

    def _save_request(self, *, provider: str, agent: str, endpoint: str, req_body: dict,
                      analyzed: AnalyzedRequest | None, duration_ms: int | None,
                      raw_resp_text: str | None, status_code: int | None,
                      raw_request_body: str | None, ttft_ms: int | None = None,
                      transport: str = "http", response_transport: str = "json",
                      response_reconstructed: bool = False,
                      response_complete: bool = True,
                      response_events: str | None = None,
                      capture_error: dict | None = None) -> None:
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

        response_body: dict | None = None
        if raw_resp_text:
            try:
                decoded_response = json.loads(raw_resp_text)
                if isinstance(decoded_response, dict):
                    response_body = decoded_response
            except (TypeError, json.JSONDecodeError):
                pass

        conversation_adapter = get_conversation_adapter(
            provider=provider, endpoint=endpoint, transport=transport,
            request_body=req_body,
        )
        identity = (
            conversation_adapter.identify(
                provider=provider, agent=agent, request_body=req_body,
                response_body=response_body,
            )
            if conversation_adapter is not None else InvocationIdentity(
                agent_id=agent, confidence="singleton",
            )
        )
        mutation = (
            conversation_adapter.context_mutation(
                request_body=req_body, identity=identity,
            )
            if conversation_adapter is not None else ContextMutation()
        )

        with get_db() as db:
            active_session = crud.get_active_session(db)
            session_id = active_session.id if active_session else None

            request_id = str(uuid.uuid4())
            captured_at = datetime.now(timezone.utc)
            predecessor = None
            if identity.previous_provider_request_id:
                predecessor = crud.get_request_by_provider_id(
                    db, provider, identity.previous_provider_request_id,
                )
            elif identity.provider_conversation_id and mutation.inherit_previous:
                predecessor = crud.get_latest_conversation_request(
                    db, provider, identity.provider_conversation_id,
                )

            logical = None
            logical_key = (
                conversation_adapter.logical_request_key(
                    provider=provider, identity=identity,
                )
                if conversation_adapter is not None else None
            )
            if logical_key is not None:
                scoped_logical_key = json.dumps(
                    [logical_key.serialize(), session_id], separators=(",", ":"),
                )
                logical = crud.get_logical_request_by_key(db, scoped_logical_key)
            else:
                scoped_logical_key = None
            if (
                logical is None
                and logical_key is None
                and predecessor is not None
                and predecessor.session_id == session_id
                and predecessor.logical_request_id
            ):
                logical = crud.get_logical_request(db, predecessor.logical_request_id)

            if logical is None:
                grouping_key = (
                    scoped_logical_key if scoped_logical_key is not None
                    else f"singleton:{request_id}"
                )
                logical = crud.create_logical_request(db, {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "grouping_key": grouping_key,
                    "provider": provider,
                    "agent": identity.agent_id or agent,
                    "model": model,
                    "endpoint": endpoint,
                    "transport": transport,
                    "provider_conversation_id": identity.provider_conversation_id,
                    "logical_turn_id": identity.logical_turn_id,
                    "started_at": captured_at,
                    "updated_at": captured_at,
                    "state": "complete" if response_complete else "incomplete",
                    "grouping_confidence": (
                        identity.confidence if logical_key is not None else "singleton"
                    ),
                    "grouping_metadata": (
                        json.dumps(identity.metadata) if identity.metadata else None
                    ),
                })
            if (
                identity.parent_turn_id
                and identity.provider_conversation_id
                and logical.parent_logical_request_id is None
            ):
                parent = crud.get_logical_request_by_turn(
                    db,
                    provider=provider,
                    conversation_id=identity.provider_conversation_id,
                    turn_id=identity.parent_turn_id,
                    session_id=session_id,
                )
                if parent is not None and parent.id != logical.id:
                    logical.parent_logical_request_id = parent.id

            data: dict = {
                "id": request_id,
                "session_id": session_id,
                "logical_request_id": logical.id,
                "timestamp": captured_at,
                "provider": provider,
                "model": model,
                "agent": agent,
                "endpoint": endpoint,
                "duration_ms": duration_ms,
                "ttft_ms": ttft_ms,
                "status_code": status_code,
                "transport": transport,
                "response_transport": response_transport,
                "response_reconstructed": int(response_reconstructed),
                "response_complete": int(response_complete),
                "capture_error": json.dumps(capture_error) if capture_error else None,
                "provider_input_tokens": provider_input,
                "provider_output_tokens": provider_output,
                "provider_reasoning_tokens": provider_reasoning,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "usage_extra": usage_extra,
                "provider_request_id": identity.provider_request_id,
                "previous_provider_request_id": identity.previous_provider_request_id,
                "provider_conversation_id": identity.provider_conversation_id,
                "logical_turn_id": identity.logical_turn_id,
                "invocation_seq": crud.next_invocation_seq(db, logical.id),
                "lineage_status": "standalone",
                "identity_metadata": json.dumps({
                    "adapter": (
                        conversation_adapter.adapter_id
                        if conversation_adapter is not None else None
                    ),
                    **identity.metadata,
                }),
                "observed_input_tokens": breakdown.total_input,
                "raw_request_body": raw_request_body,
                "raw_response_body": raw_resp_text,
                "response_events": response_events,
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

                reconstruct_context(
                    db,
                    request=req_record,
                    analyzed=analyzed,
                    identity=identity,
                    mutation=mutation,
                    predecessor=predecessor,
                )

                if identity.previous_provider_request_id:
                    crud.mark_forked_lineage(
                        db, provider, identity.previous_provider_request_id,
                    )

                if identity.provider_request_id:
                    reconcile_unresolved_descendants(db, req_record)

            logical = crud.refresh_logical_request(db, logical.id)

            # Serialise while the session is still open to avoid detached-instance errors
            ws_payload = req_record.to_dict(include_raw=False)
            logical_payload = logical.to_dict()

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
                asyncio.run_coroutine_threadsafe(
                    self.ws_manager.broadcast(
                        {"event": "logical_request_updated", "data": logical_payload}
                    ),
                    self.ws_manager.loop,
                )
            except Exception as exc:
                logger.debug("WebSocket broadcast error: %s", exc)

    # -------------------------------------------------------------------
    # WebSocket transport (e.g. Codex CLI over chatgpt.com)
    # -------------------------------------------------------------------

    def websocket_start(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        port = flow.request.port
        provider = self._get_provider(host, port)
        if provider is None:
            return  # not an LLM host

        protocol = get_ws_protocol(host, flow.request.path)
        if protocol is None:
            logger.info(
                "WS connection to known provider %s has no registered WS protocol: %s%s",
                provider, host, flow.request.path,
            )
            return

        user_agent = flow.request.headers.get("user-agent", "")
        originator = flow.request.headers.get("originator", "")
        agent = _detect_agent(f"{user_agent} {originator}".strip())
        self._ws_flows[flow.id] = _WsFlowState(
            session=protocol.new_session(), provider=provider, agent=agent,
            endpoint=flow.request.path,
        )
        logger.debug(
            "HOOK websocket_start: %s %s provider=%s agent=%s protocol=%s",
            host, flow.request.path[:60], provider, agent, protocol.protocol_id,
        )

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        state = self._ws_flows.get(flow.id)
        if state is None or flow.websocket is None or not flow.websocket.messages:
            return

        message = flow.websocket.messages[-1]
        try:
            exchanges = state.session.on_message(
                from_client=message.from_client,
                content=message.content,
                is_text=message.is_text,
                timestamp=message.timestamp,
            )
        except Exception as exc:
            logger.warning("WS session.on_message error: %s", exc, exc_info=True)
            exchanges = []

        # Bound memory on long-lived, pooled connections — forwarding already
        # happened via a local variable inside mitmproxy's websocket layer, so
        # trimming the flow's own message history here is safe.
        del flow.websocket.messages[:-1]

        for exchange in exchanges:
            try:
                self._handle_ws_exchange(state, exchange)
            except Exception as exc:
                logger.warning("WS exchange handling error: %s", exc, exc_info=True)

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        state = self._ws_flows.pop(flow.id, None)
        if state is None:
            return
        try:
            exchanges = state.session.on_close()
        except Exception as exc:
            logger.warning("WS session.on_close error: %s", exc, exc_info=True)
            exchanges = []
        for exchange in exchanges:
            try:
                self._handle_ws_exchange(state, exchange)
            except Exception as exc:
                logger.warning("WS exchange handling error: %s", exc, exc_info=True)

    def error(self, flow: http.HTTPFlow) -> None:
        # Belt-and-braces: mitmproxy errors (e.g. connection reset mid-turn) don't
        # always fire websocket_end for a tracked flow — flush any dangling exchange.
        if flow.id in self._ws_flows:
            self.websocket_end(flow)
            return
        if flow.metadata.get("contextspy_saved"):
            return

        provider = self._get_provider(flow.request.pretty_host, flow.request.port)
        if provider is None:
            return
        endpoint = flow.request.path
        raw_request_body, request_capture_error = _captured_request_text(flow)
        capture_error = {
            "stage": "transport",
            "message": str(flow.error) if flow.error else "Upstream request failed",
        }
        try:
            decoded_request = json.loads(raw_request_body or "{}")
            if isinstance(decoded_request, dict):
                req_body = decoded_request
            else:
                req_body = {}
                capture_error = _add_capture_error(
                    capture_error, "request_decode", "JSON request is not an object",
                )
        except json.JSONDecodeError as exc:
            req_body = {}
            capture_error = _add_capture_error(capture_error, "request_decode", exc)

        analyzed: AnalyzedRequest | None = None
        adapter = get_adapter(endpoint)
        if request_capture_error:
            capture_error["request_capture"] = request_capture_error
        if adapter is not None:
            try:
                input_blocks, tool_call_map = adapter.parse_request(req_body)
                analyzed = AnalyzedRequest(
                    model=req_body.get("model"), input_blocks=input_blocks,
                    output_blocks=[], usage=Usage(), tool_call_map=tool_call_map,
                )
            except Exception as exc:
                capture_error["request_analysis"] = str(exc)

        user_agent = flow.request.headers.get("user-agent", "")
        self._save_request(
            provider=provider,
            agent=_detect_agent(user_agent),
            endpoint=endpoint,
            req_body=req_body,
            analyzed=analyzed,
            duration_ms=None,
            raw_resp_text=None,
            status_code=None,
            raw_request_body=raw_request_body,
            response_transport="none",
            response_complete=False,
            capture_error=capture_error,
        )
        flow.metadata["contextspy_saved"] = True

    def _handle_ws_exchange(self, state: _WsFlowState, ex: CompletedExchange) -> None:
        adapter = get_adapter(state.endpoint)
        analyzed: AnalyzedRequest | None = None
        response_events: str | None = None
        response_reconstructed = False
        response_complete = ex.complete
        capture_error: dict | None = None
        captured_events = ex.events
        raw_resp_text = json.dumps(
            [event.to_dict() for event in captured_events], ensure_ascii=False,
        )
        if captured_events:
            response_events = json.dumps(
                [event.to_dict() for event in captured_events], ensure_ascii=False,
            )

        if adapter is not None:
            try:
                input_blocks, tool_call_map = adapter.parse_request(ex.request_body)
            except Exception as exc:
                logger.warning("WS adapter parse_request error: %s", exc, exc_info=True)
                input_blocks, tool_call_map = [], {}
                capture_error = _add_capture_error(capture_error, "request_analysis", exc)

            canonical_payload: dict | None = None
            try:
                canonical = adapter.reconstruct_response(
                    captured_events, transport="websocket",
                )
                canonical.complete = ex.complete
                if ex.error and canonical.error is None:
                    canonical.error = ex.error
                canonical_payload = canonical.payload
                raw_resp_text = json.dumps(canonical.payload, ensure_ascii=False)
                response_reconstructed = canonical.reconstructed
                response_complete = canonical.complete
            except Exception as exc:
                logger.warning("WS response reconstruction error: %s", exc, exc_info=True)
                response_complete = False
                output_blocks, usage = [], Usage()
                capture_error = _add_capture_error(
                    capture_error, "websocket_reconstruction", exc,
                )

            if canonical_payload is not None:
                try:
                    output_blocks, usage = adapter.parse_response(canonical_payload)
                except Exception as exc:
                    logger.warning("WS response parse error: %s", exc, exc_info=True)
                    output_blocks, usage = [], Usage()
                    capture_error = _add_capture_error(
                        capture_error, "response_analysis", exc,
                    )
            else:
                output_blocks, usage = [], Usage()

            if ex.error:
                usage.extra["ws_error"] = ex.error
            if not ex.complete:
                usage.extra["ws_incomplete"] = True

            analyzed = AnalyzedRequest(
                model=ex.request_body.get("model"),
                input_blocks=input_blocks,
                output_blocks=output_blocks,
                usage=usage,
                tool_call_map=tool_call_map,
            )

        duration_ms: int | None = None
        if ex.request_ts is not None and ex.last_event_ts is not None:
            duration_ms = int((ex.last_event_ts - ex.request_ts) * 1000)

        ttft_ms: int | None = None
        if ex.request_ts is not None and ex.first_event_ts is not None:
            ttft_ms = int((ex.first_event_ts - ex.request_ts) * 1000)

        self._save_request(
            provider=state.provider,
            agent=state.agent,
            endpoint=state.endpoint,
            req_body=ex.request_body,
            analyzed=analyzed,
            duration_ms=duration_ms,
            raw_resp_text=raw_resp_text,
            status_code=(ex.error or {}).get("status"),
            raw_request_body=ex.raw_request_text,
            ttft_ms=ttft_ms,
            transport="websocket",
            response_transport="websocket",
            response_reconstructed=response_reconstructed,
            response_complete=response_complete,
            response_events=response_events,
            capture_error=capture_error,
        )
