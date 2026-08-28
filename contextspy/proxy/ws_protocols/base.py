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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from contextspy.analysis.capture import CapturedEvent


@dataclass
class CompletedExchange:
    """One provider invocation assembled from a WS connection's frame stream."""

    request_body: dict          # request JSON (may be synthesized by a protocol)
    raw_request_text: str       # verbatim client frame -> raw_request_body
    # Complete application frames associated with this turn, in arrival order.
    # Provider reconstruction consumes server JSON events; unknown text, binary,
    # and auxiliary client frames remain available in the normalized capture.
    events: list[CapturedEvent] = field(default_factory=list)
    request_ts: float | None = None
    first_event_ts: float | None = None
    last_event_ts: float | None = None
    error: dict | None = None   # {"status", "code", "message"}
    complete: bool = True       # False when flushed early (close / superseded by a new turn)
    outcome: str = "unknown"   # completed / failed / incomplete / unknown


class WsSession(ABC):
    """Stateful per-connection assembler: frames in, observed invocations out."""

    @abstractmethod
    def on_message(
        self, *, from_client: bool, content: bytes, is_text: bool, timestamp: float,
    ) -> list[CompletedExchange]:
        """Feed one frame; return any exchanges that just completed (usually 0 or 1)."""

    @abstractmethod
    def on_close(self) -> list[CompletedExchange]:
        """Connection closed/errored; flush any dangling exchange (marked incomplete)."""


class WsProtocol(ABC):
    """One WS-speaking provider: matches connections, hands out a session per one."""

    protocol_id: str
    host_patterns: tuple[str, ...]   # exact-or-suffix match (same rule as _detect_provider); () = any host
    path_patterns: tuple[str, ...]   # substring match, like adapter endpoint_patterns

    @abstractmethod
    def new_session(self) -> WsSession:
        """Create a fresh session for one new WebSocket connection."""


WS_REGISTRY: list[WsProtocol] = []


def register_ws_protocol(protocol: WsProtocol) -> None:
    WS_REGISTRY.append(protocol)


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    return any(host == p or host.endswith("." + p) for p in patterns)


def get_ws_protocol(host: str, path: str) -> WsProtocol | None:
    for protocol in WS_REGISTRY:
        if not _host_matches(host, protocol.host_patterns):
            continue
        if any(p in path for p in protocol.path_patterns):
            return protocol
    return None
