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
from abc import ABC, abstractmethod
from typing import Any

from contextspy.analysis.blocks import Block, Usage


class WireFormatAdapter(ABC):
    """One provider wire format: request/response JSON <-> Block/Usage.

    ``endpoint_patterns`` are substrings matched against the request path —
    dispatch is endpoint-based (not host-based) so a gateway that relays one
    provider's traffic in another provider's wire format (e.g. Copilot -> Claude,
    opencode's zen gateway) is still parsed correctly.
    """

    format_id: str
    endpoint_patterns: tuple[str, ...]

    @abstractmethod
    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        """Return (input_blocks, tool_call_map) for the request body."""

    @abstractmethod
    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        """Return (output_blocks, usage) for a buffered (non-streaming) response body."""

    @abstractmethod
    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        """Return (output_blocks, usage) reconstructed from a raw streaming response."""


REGISTRY: list[WireFormatAdapter] = []


def register(adapter: WireFormatAdapter) -> None:
    REGISTRY.append(adapter)


def get_adapter(endpoint: str) -> WireFormatAdapter | None:
    for adapter in REGISTRY:
        if any(pattern in endpoint for pattern in adapter.endpoint_patterns):
            return adapter
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def flatten_content(content: Any) -> str:
    """Flatten a provider content value (str, or list of content-part dicts) to text.

    Used for content that stays a single block even though it may itself be
    a nested list — e.g. a tool_result's inner content array, or a plain
    multimodal message with no text/tool parts worth splitting out.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") in ("output_text", "input_text"):
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(flatten_content(block.get("content", "")))
                elif "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return json.dumps(content) if content is not None else ""
