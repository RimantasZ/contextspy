"""Capture work runs on the capture worker, never on mitmproxy's event loop."""
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from contextspy.proxy import addon as addon_module
from contextspy.proxy.addon import ContextSpyAddon, _WsFlowState, _flow_duration_ms
from contextspy.proxy.capture_worker import CaptureWorker


@pytest.fixture
def worker():
    w = CaptureWorker(name="test-capture")
    w.start()
    yield w
    w.stop(timeout=5)


@pytest.fixture
def gate(worker):
    """Keep the worker busy until the test releases it, as a long capture would."""
    release = threading.Event()
    worker.submit(release.wait, 5)
    yield release
    release.set()


def test_worker_runs_tasks_in_order_on_its_own_thread(worker):
    seen = []
    for i in range(20):
        worker.submit(lambda i=i: seen.append((i, threading.current_thread().name)))
    assert worker.join_pending(5)
    assert [i for i, _ in seen] == list(range(20))
    assert {name for _, name in seen} == {"test-capture"}


def test_worker_survives_a_failing_task(worker):
    seen = []
    worker.submit(lambda: 1 / 0)
    worker.submit(seen.append, "after")
    assert worker.join_pending(5)
    assert seen == ["after"]


def test_stop_drains_queued_tasks():
    w = CaptureWorker()
    w.start()
    release = threading.Event()
    seen = []
    w.submit(release.wait, 5)
    for i in range(5):
        w.submit(seen.append, i)
    threading.Timer(0.05, release.set).start()
    assert w.stop(timeout=5) is True
    assert seen == [0, 1, 2, 3, 4]


def _http_flow(host="api.anthropic.com", content_type="application/json"):
    flow = MagicMock()
    flow.metadata = {}
    flow.websocket = None
    flow.request.pretty_host = host
    flow.request.port = 443
    flow.request.path = "/v1/messages"
    flow.response.headers = {"content-type": content_type}
    return flow


def test_response_hook_returns_before_analysis(worker, gate):
    addon = ContextSpyAddon(capture_worker=worker)
    addon._handle_response = MagicMock()
    flow = _http_flow()
    flow.metadata["ts_start"] = 0.0

    addon.response(flow)
    # The hook recorded completion timing itself and queued the work.
    assert "ts_end" in flow.metadata
    assert isinstance(flow.metadata["contextspy_completed_at"], datetime)
    addon._handle_response.assert_not_called()

    gate.set()
    assert worker.join_pending(5)
    addon._handle_response.assert_called_once_with(flow)


def test_non_llm_responses_are_not_queued(worker):
    addon = ContextSpyAddon(capture_worker=worker)
    addon._handle_response = MagicMock()
    addon.response(_http_flow(host="registry.npmjs.org"))
    assert worker.join_pending(5)
    addon._handle_response.assert_not_called()


def test_sse_end_of_stream_is_queued_with_all_chunks(worker, gate):
    addon = ContextSpyAddon(capture_worker=worker)
    addon._handle_sse_response = MagicMock()
    flow = _http_flow(content_type="text/event-stream")
    addon.responseheaders(flow)
    collect = flow.response.stream

    for chunk in (b"event: a\n", b"data: {}\n\n", b""):
        assert collect(chunk) == chunk
    addon._handle_sse_response.assert_not_called()

    gate.set()
    assert worker.join_pending(5)
    addon._handle_sse_response.assert_called_once_with(flow, b"event: a\ndata: {}\n\n")


def test_error_after_a_queued_response_is_not_saved_twice(worker):
    addon = ContextSpyAddon(capture_worker=worker)
    addon._handle_response = MagicMock()
    addon._handle_error = MagicMock()
    flow = _http_flow()
    addon.response(flow)
    addon.error(flow)
    assert worker.join_pending(5)
    addon._handle_response.assert_called_once()
    addon._handle_error.assert_not_called()


def test_failed_request_is_queued(worker, gate):
    addon = ContextSpyAddon(capture_worker=worker)
    addon._handle_error = MagicMock()
    flow = _http_flow()
    flow.response = None
    addon.error(flow)
    addon._handle_error.assert_not_called()
    gate.set()
    assert worker.join_pending(5)
    addon._handle_error.assert_called_once_with(flow, "anthropic")


def test_websocket_messages_are_processed_on_the_worker(worker, gate):
    addon = ContextSpyAddon(capture_worker=worker)
    session = MagicMock()
    session.on_message.return_value = []
    session.on_close.return_value = []
    addon._ws_flows["ws1"] = _WsFlowState(
        session=session, provider="openai_chatgpt", agent="codex", endpoint="/backend-api/codex/responses",
    )
    flow = MagicMock()
    flow.id = "ws1"
    message = MagicMock(from_client=False)
    flow.websocket.messages = [MagicMock(), message]

    addon.websocket_message(flow)
    assert flow.websocket.messages == [message]  # trimmed on the loop
    addon.websocket_end(flow)
    assert "ws1" not in addon._ws_flows
    session.on_message.assert_not_called()
    session.on_close.assert_not_called()

    gate.set()
    assert worker.join_pending(5)
    assert session.on_message.call_args.kwargs["content"] is message.content
    session.on_close.assert_called_once()


def test_duration_ends_when_the_exchange_completed_not_when_it_was_analyzed():
    flow = MagicMock()
    flow.metadata = {"ts_start": 100.0, "ts_end": 101.5}
    assert _flow_duration_ms(flow) == 1500
    assert _flow_duration_ms(MagicMock(metadata={})) is None


def test_saved_timestamp_is_the_completion_time(monkeypatch):
    saved = []

    def _persist(envelope):
        saved.append(envelope)
        return None

    monkeypatch.setattr(addon_module, "persist_capture", _persist)
    completed_at = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    ContextSpyAddon()._save_request(
        provider="anthropic", agent="claude_code", endpoint="/v1/messages", req_body={},
        analyzed=None, duration_ms=10, raw_resp_text=None, status_code=200,
        raw_request_body="{}", completed_at=completed_at, session_id=None,
    )
    assert saved[0].data["timestamp"] == completed_at


def test_full_queue_applies_backpressure_instead_of_growing():
    w = CaptureWorker(max_queued=2)
    w.start()
    release = threading.Event()
    seen = []
    w.submit(release.wait, 5)          # running
    w.submit(seen.append, 1)           # queued
    w.submit(seen.append, 2)           # queued: the queue is now full
    blocked = threading.Thread(target=w.submit, args=(seen.append, 3))
    blocked.start()
    blocked.join(0.2)
    assert blocked.is_alive()          # waits for room rather than dropping or growing
    release.set()
    blocked.join(5)
    assert w.stop(timeout=5)
    assert seen == [1, 2, 3]
