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
"""Codex CLI (ChatGPT-plan auth) WebSocket transport.

Client -> server: one TEXT frame per turn, a standard Responses API request
JSON plus a top-level ``"type": "response.create"``. Server -> client: TEXT
frames, each one bare ``response.*`` event object identical to an SSE ``data:``
payload; a turn ends at ``response.completed``. Codex-only extras to tolerate:
idle ``codex.rate_limits`` frames and an error envelope
``{"type": "error", "status": <int>, "error": {code, message}}``.
Connections are pooled and reused across turns, so a session may see many
sequential exchanges.
"""
from __future__ import annotations

import json
import logging

from contextspy.proxy.ws_protocols.base import CompletedExchange, WsProtocol, WsSession

logger = logging.getLogger(__name__)


class CodexResponsesSession(WsSession):
    def __init__(self) -> None:
        self._pending: CompletedExchange | None = None

    def on_message(
        self, *, from_client: bool, content: bytes, is_text: bool, timestamp: float,
    ) -> list[CompletedExchange]:
        if not is_text:
            logger.debug("codex ws: ignoring binary frame (from_client=%s)", from_client)
            return []
        text = content.decode("utf-8", errors="replace")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("codex ws: ignoring unparseable frame (from_client=%s)", from_client)
            return []
        if not isinstance(obj, dict):
            logger.debug("codex ws: ignoring non-dict frame (from_client=%s)", from_client)
            return []

        if from_client:
            return self._on_client_frame(obj, text, timestamp)
        return self._on_server_frame(obj, timestamp)

    def _on_client_frame(self, obj: dict, text: str, timestamp: float) -> list[CompletedExchange]:
        if obj.get("type") != "response.create":
            return []
        flushed: list[CompletedExchange] = []
        if self._pending is not None:
            self._pending.complete = False
            flushed.append(self._pending)
        self._pending = CompletedExchange(
            request_body=obj, raw_request_text=text, request_ts=timestamp,
        )
        return flushed

    def _on_server_frame(self, obj: dict, timestamp: float) -> list[CompletedExchange]:
        pending = self._pending
        if pending is None:
            return []  # idle server frame (e.g. codex.rate_limits) with no turn in flight

        if pending.first_event_ts is None:
            pending.first_event_ts = timestamp
        pending.last_event_ts = timestamp

        etype = obj.get("type")
        if etype == "codex.rate_limits":
            return []
        if etype == "error":
            error_obj = obj.get("error") or {}
            pending.error = {
                "status": obj.get("status"),
                "code": error_obj.get("code"),
                "message": error_obj.get("message"),
            }
            pending.complete = True
            self._pending = None
            return [pending]

        pending.events.append(obj)
        if etype == "response.completed":
            pending.complete = True
            self._pending = None
            return [pending]
        return []

    def on_close(self) -> list[CompletedExchange]:
        if self._pending is None:
            return []
        pending = self._pending
        pending.complete = False
        self._pending = None
        return [pending]


class CodexResponsesProtocol(WsProtocol):
    protocol_id = "codex_responses"
    host_patterns = ("chatgpt.com",)
    path_patterns = ("/backend-api/codex/responses",)

    def new_session(self) -> WsSession:
        return CodexResponsesSession()
