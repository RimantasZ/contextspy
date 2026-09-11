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

# The encoder every count is made with, and the identifier stamped on each
# Request so counts made under different encoders stay distinguishable.
#
# o200k_base is native to every OpenAI model from GPT-4o onwards (the GPT-5.x
# line, GPT-4.1, the o-series); cl100k_base, used until 0.3.4, is native only to
# GPT-4 and GPT-3.5-turbo. The two agree to within ~0.0% on agent traffic
# (English prose, code, tool JSON), so this changes which models the counts are
# exact for, not the counts themselves. Neither matches Anthropic's tokenizer —
# see docs/development.md for the error bands.
ENCODING_NAME = "o200k_base"
TOKENIZER_ID = f"tiktoken/{ENCODING_NAME}"

# Proxy env vars that tiktoken inherits when downloading its BPE data file.
_PROXY_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def _get_encoder() -> tiktoken.Encoding:
    """Return the shared encoder, downloading it if necessary.

    Proxy env vars are stripped for the duration of the download so that
    tiktoken can reach openaipublic.blob.core.windows.net directly, even
    when HTTPS_PROXY is already set to point at our own (not-yet-started)
    forward proxy.  The vars are restored before returning.
    """
    global _encoder
    if _encoder is None:
        saved = {k: os.environ.pop(k) for k in _PROXY_VARS if k in os.environ}
        try:
            _encoder = tiktoken.get_encoding(ENCODING_NAME)
        finally:
            os.environ.update(saved)
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken o200k_base (universal approximation)."""
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


def get_token_text_segments(text: str, max_tokens: int = 8_000) -> list[tuple[str, int]]:
    """Return Unicode-safe text segments and the tokens represented by each.

    A BPE token can end in the middle of a UTF-8 sequence. Rendering the decoded
    tokens independently would insert replacement characters and make it
    impossible for the browser to map the result back to the source. Adjacent
    token bytes are therefore combined only when necessary to produce valid
    Unicode. For ordinary prose and code, each segment still represents one
    token.
    """
    if not text or max_tokens <= 0:
        return []

    encoder = _get_encoder()
    token_ids = encoder.encode(text[:200_000], disallowed_special=())[:max_tokens]
    segments: list[tuple[str, int]] = []
    pending = bytearray()
    pending_tokens = 0

    for token_id in token_ids:
        pending.extend(encoder.decode_single_token_bytes(token_id))
        pending_tokens += 1
        try:
            decoded = pending.decode("utf-8")
        except UnicodeDecodeError:
            continue
        segments.append((decoded, pending_tokens))
        pending.clear()
        pending_tokens = 0

    # An 8,000-token boundary may split a multi-byte character. The incomplete
    # bytes are deliberately omitted; the returned window ends at the last
    # source position that can be represented losslessly.
    return segments
