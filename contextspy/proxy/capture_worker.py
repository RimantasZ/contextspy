"""Run capture work (analysis, persistence, broadcast) off the mitmproxy event loop.

mitmproxy calls addon hooks on its single asyncio loop: whatever a hook does, every
other connection waits for it, so no requests are forwarded and no stream chunks are
relayed. The addon's hooks therefore only snapshot what a capture needs and hand the
heavy part to this worker.

One thread, FIFO: captures are persisted in completion order, so a continuation that
resolves its predecessor through the database (provider lineage) sees the earlier
capture, exactly as when everything ran on the loop.

The queue is bounded. Each queued capture holds its request and response bodies (the
whole conversation, megabytes for a long session), so when captures arrive faster than
they can be analyzed, ``submit`` waits for room: the proxy then slows down as it did
before this worker existed, instead of growing memory without limit or dropping captures.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_STOP = object()
# Agents send a few requests per second at most; this only fills under sustained overload.
_MAX_QUEUED = 64


class CaptureWorker:
    def __init__(self, name: str = "contextspy-capture", max_queued: int = _MAX_QUEUED) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=max_queued)
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._started = False
        self._warned_full = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            self._queue.put_nowait((fn, args))
            return
        except queue.Full:
            pass
        if not self._warned_full:
            self._warned_full = True
            logger.warning(
                "Capture backlog is full (%d captures waiting); the proxy waits for the "
                "analysis to catch up", self._queue.maxsize,
            )
        self._queue.put((fn, args))

    def stop(self, timeout: float = 10.0) -> bool:
        """Finish the queued captures, then stop. Returns False if the timeout ran out."""
        if not self._started:
            return True
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            pass
        self._thread.join(timeout)
        drained = not self._thread.is_alive()
        if not drained:
            logger.error(
                "Capture worker did not finish within %.0fs; %d captures not saved",
                timeout, self._queue.qsize(),
            )
        return drained

    def join_pending(self, timeout: float = 10.0) -> bool:
        """Block until everything submitted so far has run (tests, diagnostics)."""
        done = threading.Event()
        self.submit(done.set)
        return done.wait(timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            fn, args = item
            started = time.monotonic()
            try:
                fn(*args)
            except Exception as exc:
                logger.warning("Capture worker task failed: %s", exc, exc_info=True)
            elapsed = time.monotonic() - started
            logger.debug("capture task %s took %.0f ms", getattr(fn, "__name__", fn), elapsed * 1000)
            if self._warned_full and self._queue.empty():
                self._warned_full = False
                logger.info("Capture backlog cleared")
