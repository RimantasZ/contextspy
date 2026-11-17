"""The forward proxy decrypts only LLM API hosts; everything else passes through untouched."""
import re
from unittest.mock import MagicMock

import pytest

from contextspy.config import Settings
from contextspy.proxy import capture_state
from contextspy.proxy.addon import ContextSpyAddon, intercepted_host_patterns
from contextspy.proxy.runner import forward_proxy_options


def _allowed(patterns: list[str], host_port: str) -> bool:
    # The same test mitmproxy's next_layer applies to allow_hosts.
    return any(re.search(rex, host_port, re.IGNORECASE) for rex in patterns)


@pytest.mark.parametrize("host_port", [
    "api.anthropic.com:443",
    "API.Anthropic.com:443",
    "api.openai.com:443",
    "myresource.openai.azure.com:443",
    "api.githubcopilot.com:443",
    "copilot-proxy.githubusercontent.com:443",
    "opencode.ai:443",
    "chatgpt.com:443",
    "localhost:11434",
    "192.168.1.20:11434",
])
def test_llm_hosts_are_intercepted(host_port):
    assert _allowed(intercepted_host_patterns(), host_port)


@pytest.mark.parametrize("host_port", [
    "mcp.figma.com:443",
    "claude.ai:443",
    "statsig.anthropic.com:443",
    "notapi.anthropic.com:443",
    "api.anthropic.com.example.net:443",
    "registry.npmjs.org:443",
    "localhost:114340",
])
def test_other_hosts_pass_through(host_port):
    assert not _allowed(intercepted_host_patterns(), host_port)


def test_extra_hosts_are_intercepted():
    patterns = intercepted_host_patterns(["llm.internal", ""])
    assert _allowed(patterns, "llm.internal:8443")
    assert _allowed(patterns, "eu.llm.internal:443")
    assert not _allowed(patterns, "other.internal:443")


def test_forward_proxy_options_restrict_interception():
    settings = Settings()
    settings.extra_hosts = ["llm.internal"]
    options = forward_proxy_options(settings)
    assert options.listen_port == settings.proxy.port
    assert options.allow_hosts == intercepted_host_patterns(["llm.internal"])
    assert options.ignore_hosts == []


def _flow(host: str, content_type: str = "text/event-stream"):
    flow = MagicMock()
    flow.metadata = {}
    flow.request.pretty_host = host
    flow.request.port = 443
    flow.request.path = "/v1/messages"
    flow.response.headers = {"content-type": content_type}
    flow.response.stream = False
    return flow


def test_request_body_is_only_snapshotted_for_llm_hosts():
    addon = ContextSpyAddon()
    other = _flow("mcp.figma.com")
    addon.request(other)
    other.request.get_text.assert_not_called()
    assert "contextspy_request_body" not in other.metadata


def test_sse_from_other_hosts_is_streamed_not_buffered():
    flow = _flow("mcp.figma.com")
    ContextSpyAddon().responseheaders(flow)
    assert flow.response.stream is True


def test_sse_while_paused_is_streamed_not_buffered():
    capture_state.set_paused(True)
    try:
        flow = _flow("api.anthropic.com")
        addon = ContextSpyAddon()
        addon.request(flow)
        addon.responseheaders(flow)
        assert flow.response.stream is True
    finally:
        capture_state.set_paused(False)


def test_sse_from_llm_hosts_is_captured():
    flow = _flow("api.anthropic.com")
    ContextSpyAddon().responseheaders(flow)
    assert callable(flow.response.stream)
    assert flow.metadata["is_sse"] is True


def test_untracked_websocket_history_is_trimmed():
    flow = MagicMock()
    flow.id = "untracked"
    flow.websocket.messages = ["m1", "m2", "m3"]
    ContextSpyAddon().websocket_message(flow)
    assert flow.websocket.messages == ["m3"]
