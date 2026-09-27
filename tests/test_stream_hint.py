import hashlib

from contextspy.analysis.stream_hint import extract_stream_hint


def test_extract_codex_response_prompt_cache_key_as_digest():
    source, digest = extract_stream_hint(
        agent="codex", endpoint="/backend-api/codex/responses",
        request={"prompt_cache_key": "cache-route-1"},
    )
    assert source == "openai_prompt_cache_key"
    assert digest == hashlib.sha256(b"cache-route-1").hexdigest()


def test_reject_untrusted_or_unsupported_stream_hints():
    for agent, endpoint, value in (
        ("other", "/v1/responses", "cache-route-1"),
        ("codex", "/v1/chat/completions", "cache-route-1"),
        ("codex", "/v1/responses", "guardian:cache-route-1"),
        ("codex", "/v1/responses", "Guardian:cache-route-1"),
        ("codex", "/v1/responses", " cache-route-1"),
        ("codex", "/v1/responses", "cache\nroute"),
        ("codex", "/v1/responses", "x" * 257),
        ("codex", "/v1/responses", 1),
    ):
        assert extract_stream_hint(
            agent=agent, endpoint=endpoint,
            request={"prompt_cache_key": value},
        ) == (None, None)
