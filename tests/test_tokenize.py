from __future__ import annotations

from contextspy.analysis.tokenizer import TOKENIZER_ID, get_token_text_segments
from contextspy.api.routers.tokenize import (
    TokenizeRequest,
    TokenizeWindowRequest,
    tokenize,
    tokenize_window,
)


def test_legacy_tokenize_contract_is_unchanged() -> None:
    response = tokenize(TokenizeRequest(texts=["hello world"]))
    assert len(response.results) == 1
    assert "".join(response.results[0]) == "hello world"


def test_token_window_returns_explicit_boundaries() -> None:
    response = tokenize_window(TokenizeWindowRequest(text="alpha beta", offset=0))
    assert "".join(segment.text for segment in response.segments) == "alpha beta"
    assert response.window_start == 0
    assert response.window_end == len("alpha beta")
    assert response.total_length == len("alpha beta")
    assert response.truncated_before is False
    assert response.truncated_after is False
    assert response.tokenizer == TOKENIZER_ID


def test_token_window_uses_utf16_offsets_and_clamps_surrogate_pair() -> None:
    text = "A😀B"
    inside_emoji = tokenize_window(TokenizeWindowRequest(text=text, offset=2))
    assert inside_emoji.window_start == 1
    assert "".join(segment.text for segment in inside_emoji.segments) == "😀B"
    assert inside_emoji.window_end == 4
    assert inside_emoji.total_length == 4

    after_emoji = tokenize_window(TokenizeWindowRequest(text=text, offset=3))
    assert after_emoji.window_start == 3
    assert "".join(segment.text for segment in after_emoji.segments) == "B"


def test_token_segments_reconstruct_unicode_without_replacement_characters() -> None:
    text = "Hello 👋 世界 — café"
    segments = get_token_text_segments(text)
    reconstructed = "".join(segment for segment, _ in segments)
    assert reconstructed == text
    assert "�" not in reconstructed
    assert sum(token_count for _, token_count in segments) > 0


def test_token_window_clamps_offsets_and_handles_empty_text() -> None:
    before = tokenize_window(TokenizeWindowRequest(text="abc", offset=-20))
    assert before.window_start == 0
    assert "".join(segment.text for segment in before.segments) == "abc"

    after = tokenize_window(TokenizeWindowRequest(text="abc", offset=200))
    assert after.window_start == 3
    assert after.window_end == 3
    assert after.segments == []

    empty = tokenize_window(TokenizeWindowRequest(text="", offset=10))
    assert empty.total_length == 0
    assert empty.window_start == 0
    assert empty.window_end == 0
    assert empty.truncated_before is False
    assert empty.truncated_after is False


def test_token_window_enforces_character_limit() -> None:
    response = tokenize_window(TokenizeWindowRequest(text="a" * 60_000, offset=0))
    assert response.window_end <= 50_000
    assert response.truncated_after is True
