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

import os

import tiktoken

_encoder: tiktoken.Encoding | None = None

# Proxy env vars that tiktoken inherits when downloading its BPE data file.
_PROXY_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def _get_encoder() -> tiktoken.Encoding:
    """Return the shared cl100k_base encoder, downloading it if necessary.

    Proxy env vars are stripped for the duration of the download so that
    tiktoken can reach openaipublic.blob.core.windows.net directly, even
    when HTTPS_PROXY is already set to point at our own (not-yet-started)
    forward proxy.  The vars are restored before returning.
    """
    global _encoder
    if _encoder is None:
        saved = {k: os.environ.pop(k) for k in _PROXY_VARS if k in os.environ}
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        finally:
            os.environ.update(saved)
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base (universal approximation)."""
    if not text:
        return 0
    return len(_get_encoder().encode(text, disallowed_special=()))


def get_token_strings(text: str, max_tokens: int = 8_000) -> list[str]:
    """Return the string representation of each token (truncated to max_tokens)."""
    if not text:
        return []
    ids = _get_encoder().encode(text[:200_000], disallowed_special=())
    if len(ids) > max_tokens:
        ids = ids[:max_tokens]
    return [_get_encoder().decode([t]) for t in ids]
