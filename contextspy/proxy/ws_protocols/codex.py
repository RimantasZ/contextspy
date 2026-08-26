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

import base64
import json
import logging
from typing import Any

from contextspy.analysis.capture import CapturedEvent
from contextspy.proxy.ws_protocols.base import CompletedExchange, WsProtocol, WsSession

logger = logging.getLogger(__name__)


class CodexResponsesSession(WsSession):
    def __init__(self) -> None:
        self._pending: CompletedExchange | None = None

    def on_message(
        self, *, from_client: bool, content: bytes, is_text: bool, timestamp: float,
    ) -> list[CompletedExchange]:
        if not is_text:
            if self._pending is not None:
                self._append_event(
                    self._pending,
                    direction="client_to_server" if from_client else "server_to_client",
                    kind="binary",
                    payload={
                        "encoding": "base64",
                        "data": base64.b64encode(content).decode("ascii"),
                    },
                )
                if not from_client:
                    self._mark_server_event(self._pending, timestamp)
            return []
        text = content.decode("utf-8", errors="replace")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            if self._pending is not None:
                self._append_event(
                    self._pending,
                    direction="client_to_server" if from_client else "server_to_client",
                    kind="text",
                    text=text,
                )
                if not from_client:
                    self._mark_server_event(self._pending, timestamp)
            return []
        if not isinstance(obj, dict):
            if self._pending is not None:
                self._append_event(
                    self._pending,
                    direction="client_to_server" if from_client else "server_to_client",
                    kind="json",
                    payload=obj,
                )
                if not from_client:
                    self._mark_server_event(self._pending, timestamp)
            return []

        if from_client:
            return self._on_client_frame(obj, text, timestamp)
        return self._on_server_frame(obj, timestamp)

    def _on_client_frame(self, obj: dict, text: str, timestamp: float) -> list[CompletedExchange]:
        if obj.get("type") != "response.create":
            if self._pending is not None:
                self._append_event(
                    self._pending, direction="client_to_server", kind="json", payload=obj,
                )
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

        self._mark_server_event(pending, timestamp)
        self._append_event(
            pending, direction="server_to_client", kind="json", payload=obj,
        )

        etype = obj.get("type")
        if etype == "codex.rate_limits":
            # It is not used by block analysis, but it is part of the provider
            # payload and must remain visible in the normalized event capture.
            return []
        if etype == "error":
            error_obj = obj.get("error") or {}
            if not isinstance(error_obj, dict):
                error_obj = {"message": str(error_obj)}
            pending.error = {
                "status": obj.get("status"),
                "code": error_obj.get("code"),
                "message": error_obj.get("message"),
            }
            pending.complete = True
            self._pending = None
            return [pending]
        if etype in ("response.completed", "response.failed", "response.incomplete"):
            if etype == "response.failed":
                response = obj.get("response") or {}
                response_error = response.get("error") or {} if isinstance(response, dict) else {}
                if not isinstance(response_error, dict):
                    response_error = {"message": str(response_error)}
                status = obj.get("status")
                pending.error = {
                    "status": status if isinstance(status, int) else None,
                    "code": response_error.get("code"),
                    "message": response_error.get("message"),
                }
            pending.complete = True
            self._pending = None
            return [pending]
        return []

    @staticmethod
    def _append_event(
        pending: CompletedExchange,
        *,
        direction: str,
        kind: str,
        payload: Any = None,
        text: str | None = None,
    ) -> None:
        pending.events.append(CapturedEvent(
            sequence=len(pending.events),
            direction=direction,
            kind=kind,
            payload=payload,
            text=text,
        ))

    @staticmethod
    def _mark_server_event(pending: CompletedExchange, timestamp: float) -> None:
        if pending.first_event_ts is None:
            pending.first_event_ts = timestamp
        pending.last_event_ts = timestamp

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
