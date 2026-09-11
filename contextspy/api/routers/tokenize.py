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

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from contextspy.analysis.tokenizer import TOKENIZER_ID, get_token_strings, get_token_text_segments

router = APIRouter(tags=["tokenize"])

_MAX_TEXTS = 200
_MAX_CHARS = 50_000


class TokenizeRequest(BaseModel):
    texts: list[str]

    @field_validator("texts")
    @classmethod
    def limit_texts(cls, v: list[str]) -> list[str]:
        return v[:_MAX_TEXTS]


class TokenizeResponse(BaseModel):
    results: list[list[str]]


class TokenizeWindowRequest(BaseModel):
    text: str
    offset: int = 0


class TokenizeWindowSegment(BaseModel):
    text: str
    start: int
    end: int
    token_count: int


class TokenizeWindowResponse(BaseModel):
    segments: list[TokenizeWindowSegment]
    window_start: int
    window_end: int
    total_length: int
    truncated_before: bool
    truncated_after: bool
    tokenizer: str


@router.post("/tokenize", response_model=TokenizeResponse)
def tokenize(body: TokenizeRequest) -> TokenizeResponse:
    results = [get_token_strings(t[:_MAX_CHARS]) for t in body.texts]
    return TokenizeResponse(results=results)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _python_index_at_utf16_offset(value: str, requested_offset: int) -> tuple[int, int]:
    """Return a Python index and its exact UTF-16 offset.

    JavaScript indexes strings in UTF-16 code units. If a caller supplies the
    middle of a surrogate pair, the position is clamped to the start of that
    Unicode character so the returned text always starts at a valid boundary.
    """
    target = max(0, requested_offset)
    utf16_offset = 0
    for index, char in enumerate(value):
        units = 2 if ord(char) > 0xFFFF else 1
        if utf16_offset + units > target:
            return index, utf16_offset
        utf16_offset += units
        if utf16_offset == target:
            return index + 1, utf16_offset
    return len(value), utf16_offset


@router.post("/tokenize/window", response_model=TokenizeWindowResponse)
def tokenize_window(body: TokenizeWindowRequest) -> TokenizeWindowResponse:
    total_length = _utf16_length(body.text)
    python_start, window_start = _python_index_at_utf16_offset(body.text, body.offset)
    candidate = body.text[python_start:python_start + _MAX_CHARS]
    token_segments = get_token_text_segments(candidate)

    segments: list[TokenizeWindowSegment] = []
    cursor = window_start
    for text, token_count in token_segments:
        end = cursor + _utf16_length(text)
        segments.append(TokenizeWindowSegment(
            text=text,
            start=cursor,
            end=end,
            token_count=token_count,
        ))
        cursor = end

    return TokenizeWindowResponse(
        segments=segments,
        window_start=window_start,
        window_end=cursor,
        total_length=total_length,
        truncated_before=window_start > 0,
        truncated_after=cursor < total_length,
        tokenizer=TOKENIZER_ID,
    )
