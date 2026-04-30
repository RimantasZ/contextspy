from __future__ import annotations

import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base (universal approximation)."""
    if not text:
        return 0
    return len(_encoder.encode(text, disallowed_special=()))
