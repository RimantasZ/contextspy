from __future__ import annotations

import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base (universal approximation)."""
    if not text:
        return 0
    return len(_encoder.encode(text, disallowed_special=()))


def get_token_strings(text: str, max_tokens: int = 8_000) -> list[str]:
    """Return the string representation of each token (truncated to max_tokens)."""
    if not text:
        return []
    ids = _encoder.encode(text[:200_000], disallowed_special=())
    if len(ids) > max_tokens:
        ids = ids[:max_tokens]
    return [_encoder.decode([t]) for t in ids]
