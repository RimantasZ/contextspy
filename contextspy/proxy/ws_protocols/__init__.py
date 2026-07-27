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
"""WebSocket protocol registry.

Each protocol turns one provider's WS frame stream (connection lifecycle +
frame sequencing) into ``CompletedExchange`` objects — request/response pairs
in the same shape the HTTP path already produces, so everything downstream
(adapter ``parse_events`` -> classify -> crud -> dashboard broadcast) is
untouched. Adding a new WS-speaking provider means adding one new protocol
module here and registering it below — nothing else in the pipeline changes.
"""
from __future__ import annotations

from contextspy.proxy.ws_protocols.base import (
    WS_REGISTRY,
    CompletedExchange,
    WsProtocol,
    WsSession,
    get_ws_protocol,
    register_ws_protocol,
)
from contextspy.proxy.ws_protocols.codex import CodexResponsesProtocol

register_ws_protocol(CodexResponsesProtocol())

__all__ = [
    "WS_REGISTRY",
    "CompletedExchange",
    "WsProtocol",
    "WsSession",
    "get_ws_protocol",
    "register_ws_protocol",
]
