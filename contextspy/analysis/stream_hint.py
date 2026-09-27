# Copyright 2026 Rimantas Zukaitis
"""Durable, non-secret affinity hints for conversation projection.

These are cache-routing hints, not provider conversation IDs. The raw key is
never persisted separately from the request body (which follows retention).
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping


OPENAI_PROMPT_CACHE_KEY = "openai_prompt_cache_key"


def extract_stream_hint(
    *, agent: str | None, endpoint: str, request: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return (source, SHA-256 digest) for a supported Codex request hint."""
    if agent != "codex" or endpoint.rstrip("/").split("?")[0].split("/")[-1] != "responses":
        return None, None
    if not isinstance(request, Mapping):
        return None, None
    value = request.get("prompt_cache_key")
    if not isinstance(value, str) or not value or len(value) > 256:
        return None, None
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None, None
    # Codex guardian/probe calls can use a related prefixed key but are not a
    # separate conversation on that basis.
    if value.casefold().startswith("guardian:"):
        return None, None
    return OPENAI_PROMPT_CACHE_KEY, hashlib.sha256(value.encode("utf-8")).hexdigest()
