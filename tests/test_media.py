"""Images, files and other binary parts (analysis/media.py, plans/104-image-token-estimates.md)."""
from __future__ import annotations

import base64
import struct

import pytest

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.classifier import classify_blocks
from contextspy.analysis.media import estimate_image_tokens, image_dimensions
from contextspy.analysis.tokenizer import count_tokens


def png(width: int, height: int, salt: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x02" + salt


def jpeg(width: int, height: int, metadata: int = 0) -> bytes:
    app = b"\xff\xe1" + struct.pack(">H", metadata + 2) + b"\x00" * metadata if metadata else b""
    return (b"\xff\xd8" + app * (2 if metadata else 0) + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width)
            + b"\x00" * 12)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{b64(data)}"


def anthropic_image(data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(data)}}


@pytest.mark.parametrize("data", [
    png(640, 480),
    jpeg(640, 480),
    b"GIF89a" + struct.pack("<HH", 640, 480),
    b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x00\x00\x00\x00"
    + (639).to_bytes(3, "little") + (479).to_bytes(3, "little"),
    b"RIFF\x00\x00\x00\x00WEBPVP8L\x00\x00\x00\x00\x2f" + (639 | 479 << 14).to_bytes(4, "little"),
    b"RIFF\x00\x00\x00\x00WEBPVP8 \x00\x00\x00\x00\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", 640, 480),
])
def test_dimensions(data):
    assert image_dimensions(data) == (640, 480)


@pytest.mark.parametrize("data", [b"", b"\x89PNG\r\n\x1a\n", jpeg(640, 480)[:5], b"GIF89a\x01", b"%PDF-1.7"])
def test_dimensions_of_unreadable_data(data):
    assert image_dimensions(data) is None


# Worked examples from the Anthropic vision docs: (width, height, standard tier, high resolution).
@pytest.mark.parametrize("width,height,standard,high", [
    (200, 200, 64, 64),
    (1000, 1000, 1296, 1296),
    (1092, 1092, 1521, 1521),
    (1920, 1080, 1560, 2691),
    (2000, 1500, 1564, 3888),
    (3840, 2160, 1560, 4784),
])
def test_anthropic_rule_matches_documented_examples(width, height, standard, high):
    assert estimate_image_tokens("claude-sonnet-4-6", width, height) == (standard, "published")
    assert estimate_image_tokens("claude-sonnet-5-5", width, height) == (high, "published")
    assert estimate_image_tokens("claude-sonnet-5-5", height, width)[0] == high


@pytest.mark.parametrize("model,high", [
    ("claude-opus-4-7", True), ("claude-opus-4.8", True), ("claude-haiku-5-5", True),
    ("claude-mythos-preview", True), ("anthropic.claude-sonnet-5-v1:0", True),
    ("claude-haiku-4-5-20251001", False), ("claude-sonnet-4-20250514", False),
    ("claude-sonnet-4.5", False), ("claude-3-5-sonnet-20241022", False),
])
def test_anthropic_resolution_tier(model, high):
    assert estimate_image_tokens(model, 1920, 1080)[0] == (2691 if high else 1560)


@pytest.mark.parametrize("model,size,detail,tokens", [
    # Worked examples from the OpenAI images guide.
    ("gpt-6-astra", (1024, 1024), "high", 1229),
    ("gpt-6-astra", (2048, 2048), "high", 3000),
    ("gpt-6-astra", (4096, 512), "high", 2458),
    # auto: original from 5.5, high for 5.4.
    ("gpt-5.5", (2048, 2048), None, 4916),
    ("gpt-5.4", (2048, 2048), None, 3000),
    ("openai/gpt-5.4", (2048, 2048), "auto", 3000),
    ("gpt-5.6-sol", (4000, 4000), "low", 308),
    # Tile models.
    ("gpt-4o", (1024, 1024), None, 765),
    ("gpt-4o", (2048, 4096), "high", 1105),
    ("gpt-4o", (4000, 4000), "low", 85),
    ("gpt-4o-mini", (512, 512), None, 8500),
    ("gpt-5.1-codex", (1024, 1024), None, 630),
])
def test_openai_rules(model, size, detail, tokens):
    assert estimate_image_tokens(model, *size, detail) == (tokens, "published")


def test_models_without_a_published_rule():
    assert estimate_image_tokens("gemma3:4b", 2000, 1000) == (256, "published")
    assert estimate_image_tokens("google/gemma-3-27b-it", 10, 10) == (256, "published")
    assert estimate_image_tokens("qwen2.5-vl", 640, 480) == (300, "rough")
    assert estimate_image_tokens(None, 640, 480) == (300, "rough")


def test_anthropic_pasted_image_is_part_of_the_user_message():
    body = {"model": "claude-sonnet-5-5", "messages": [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [anthropic_image(png(2000, 1224))]},
    ]}
    blocks, _ = get_adapter("/v1/messages").parse_request(body)
    classify_blocks(blocks)
    image = blocks[-1]
    assert (image.block_type, image.category, image.token_count) == (
        "user_message", "current_user_message", 3168,
    )
    assert image.content.startswith("[image png 2000×1224 sha256:")
    assert image.attrs["media_only"] is True and image.attrs["token_estimate"] == "published"
    assert image.attrs["media"][0]["width"] == 2000
    assert blocks[0].category == "conversation_history"  # the image is now the current message


def test_image_identity_follows_its_bytes():
    def parse(data):
        return get_adapter("/v1/messages").parse_request(
            {"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": [anthropic_image(data)]}]},
        )[0][0].content_hash

    assert parse(png(10, 10, b"a")) == parse(png(10, 10, b"a"))
    assert parse(png(10, 10, b"a")) != parse(png(10, 10, b"b"))


def test_tool_result_counts_text_plus_every_image():
    body = {"model": "claude-sonnet-5-5", "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [
            {"type": "text", "text": "two screenshots"},
            anthropic_image(png(280, 280, b"1")),
            anthropic_image(png(560, 280, b"2")),
        ]}]},
    ]}
    result = get_adapter("/v1/messages").parse_request(body)[0][-1]
    assert result.block_type == "tool_result" and result.tool_name == "Read"
    assert result.token_count == count_tokens("two screenshots") + 100 + 200
    assert result.attrs["media_tokens"] == 300 and result.attrs["media_only"] is False
    assert len(result.attrs["media"]) == 2


def test_chat_and_responses_images():
    url = data_url(png(1024, 1024))
    chat, _ = get_adapter("/v1/chat/completions").parse_request({"model": "gpt-4o", "messages": [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url, "detail": "low"}}]},
    ]})
    assert (chat[0].block_type, chat[0].token_count) == ("user_message", 85)
    responses, _ = get_adapter("/v1/responses").parse_request({"model": "gpt-4o", "input": [
        {"role": "user", "content": [{"type": "input_image", "image_url": url}]},
    ]})
    assert (responses[0].block_type, responses[0].token_count) == ("user_message", 765)


def test_ollama_images_without_text():
    blocks, _ = get_adapter("/api/chat").parse_request({"model": "gemma3", "messages": [
        {"role": "user", "content": "", "images": [b64(png(10, 10))]},
    ]})
    assert [(b.block_type, b.token_count, b.json_path) for b in blocks] == [
        ("user_message", 256, ("messages", 0, "images", 0)),
    ]


def test_remote_image_is_not_estimated():
    blocks, _ = get_adapter("/v1/messages").parse_request({"model": "claude-sonnet-5-5", "messages": [
        {"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}]},
    ]})
    assert blocks[0].token_count == 0
    assert blocks[0].attrs["token_estimate"] == "unavailable"
    assert "by reference" in blocks[0].content


@pytest.mark.parametrize("endpoint,part", [
    ("/v1/messages", {"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "JVBE" * 5000}}),
    ("/v1/chat/completions", {"type": "file", "file": {
        "filename": "a.pdf", "file_data": "data:application/pdf;base64," + "JVBE" * 5000}}),
    ("/v1/chat/completions", {"type": "input_audio", "input_audio": {"data": "UklG" * 5000, "format": "wav"}}),
    ("/v1/responses", {"type": "input_file", "filename": "a.pdf",
                       "file_data": "data:application/pdf;base64," + "JVBE" * 5000}),
])
def test_files_and_audio_are_marked_and_not_estimated(endpoint, part):
    message = {"role": "user", "content": [part]}
    body = {"input": [message]} if endpoint.endswith("responses") else {"messages": [message]}
    block = get_adapter(endpoint).parse_request(body)[0][0]
    assert (block.block_type, block.token_count) == ("user_message", 0)
    assert block.attrs["token_estimate"] == "unavailable"
    assert block.attrs["media"][0]["bytes"] == 15000
    assert "JVBE" not in block.content and "UklG" not in block.content


def test_text_documents_are_counted_as_text():
    text = "hello world " * 50
    for source in ({"type": "text", "media_type": "text/plain", "data": text},
                   {"type": "content", "content": [{"type": "text", "text": text}]}):
        for content in (
            [{"type": "document", "source": source}],
            [{"type": "tool_result", "tool_use_id": "t",
              "content": [{"type": "document", "source": source}]}],
        ):
            block = get_adapter("/v1/messages").parse_request(
                {"messages": [{"role": "user", "content": content}]},
            )[0][0]
            assert block.content == text
            assert block.token_count == count_tokens(text)
            assert block.attrs["document_text"] is True


def test_unrecognised_items_never_tokenize_binary_data():
    payload = "AbCd" * 25_000
    adapter = get_adapter("/v1/responses")
    item = {"type": "computer_call_output", "output": {"image_url": "data:image/png;base64," + payload},
            "raw": payload, "encrypted_content": payload[:2000]}
    for blocks in (adapter.parse_request({"input": [item]})[0], adapter.parse_response({"output": [item]})[0]):
        assert payload[:3000] not in blocks[0].content
        assert payload[:2000] in blocks[0].content  # opaque model context is left alone
        assert blocks[0].attrs["binary_scrubbed"] is True


def test_generated_images():
    adapter = get_adapter("/v1/responses")
    item = {"type": "image_generation_call", "id": "ig_1", "result": b64(png(1024, 1024, b"x" * 60_000)),
            "revised_prompt": "a cat"}
    sent = adapter.parse_request({"model": "gpt-4o", "input": [item]})[0][0]
    assert sent.block_type == "other"
    assert sent.token_count == 765 + count_tokens("a cat")
    received = adapter.parse_response({"output": [item]})[0][0]
    assert received.token_count == count_tokens("a cat")
    assert received.attrs["token_estimate"] == "unavailable"
    assert len(received.content) < 100
