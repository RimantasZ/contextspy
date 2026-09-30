import logging
from unittest.mock import MagicMock

from contextspy.proxy import capture_state
from contextspy.proxy.addon import ContextSpyAddon


def _flow():
    flow = MagicMock()
    flow.metadata = {}
    flow.request.pretty_host = "api.anthropic.com"
    flow.request.port = 443
    flow.request.method = "POST"
    flow.request.path = "/v1/messages"
    return flow


def test_paused_request_is_ignored_and_logged(caplog):
    capture_state.set_paused(True)
    try:
        flow = _flow()
        addon = ContextSpyAddon()
        with caplog.at_level(logging.INFO, logger="contextspy.proxy.addon"):
            addon.request(flow)
        assert flow.metadata["contextspy_paused_ignore"] is True
        assert "capture is paused" in caplog.text

        flow.websocket = None
        addon._handle_response = MagicMock()
        addon.response(flow)
        addon._handle_response.assert_not_called()
    finally:
        capture_state.set_paused(False)


def test_pause_endpoints():
    from fastapi.testclient import TestClient
    from contextspy.api.main import create_app

    client = TestClient(create_app())
    assert client.post("/api/proxy/pause").json() == {"paused": True}
    assert client.get("/api/proxy/status").json()["paused"] is True
    assert client.post("/api/proxy/resume").json() == {"paused": False}
    assert client.get("/api/proxy/status").json()["paused"] is False


def test_cli_pause_and_resume(monkeypatch):
    import httpx
    from typer.testing import CliRunner
    from contextspy import cli

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["pause"]).exit_code == 0
    assert runner.invoke(cli.app, ["resume"]).exit_code == 0
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["pause", "resume"]
