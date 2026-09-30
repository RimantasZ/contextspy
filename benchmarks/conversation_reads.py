"""Privacy-safe local read-path benchmark; run with `.venv/bin/python benchmarks/conversation_reads.py`.

Numbers are exploratory, not CI thresholds. This generates an exact chain in a fresh
in-memory database, so also profile repeated-context and fork-heavy cases before
declaring a production p95 for a particular machine.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import time
import tracemalloc
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession

from contextspy.db import crud
from contextspy.db.models import Base, BlockRecord, Request, Session


def benchmark(size: int, blocks_per_request: int, mode: str, profile: bool = False,
              trace_memory: bool = True) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    with OrmSession(engine) as db:
        db.add(Session(id="benchmark", name="synthetic", started_at=now, is_active=1))
        db.flush()
        db.add_all(Request(
            id=f"r-{i}", session_id="benchmark", session_seq=i,
            timestamp=now + timedelta(seconds=i), provider="openai",
            endpoint="/v1/responses", agent="synthetic",
            provider_response_id=f"response-{i}",
            predecessor_response_id=(f"response-{i-1}" if i > 1 else None)
                if mode == "exact" else None,
            tokens_total_input=blocks_per_request * 10, tokens_total_output=10,
        ) for i in range(1, size + 1))
        db.flush()
        db.add_all(BlockRecord(
            request_id=f"r-{i}", direction="input", position=position,
            block_type="user_message", content_hash=f"shared-{position}", token_count=10,
        ) for i in range(1, size + 1) for position in range(blocks_per_request))
        db.flush()

        selects = 0
        def record(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal selects
            if statement.lstrip().upper().startswith("SELECT"):
                selects += 1
        event.listen(engine, "before_cursor_execute", record)
        if trace_memory:
            tracemalloc.start()
        timings = {}
        profiler = cProfile.Profile() if profile else None
        try:
            for name, operation in (
                ("cold_dashboard", lambda: crud.get_dashboard_live(db)),
                ("warm_dashboard", lambda: crud.get_dashboard_live(db)),
                ("revision", lambda: crud.get_session_lineage_revision(db, "benchmark")),
                ("cached_diagnostics", lambda: crud.get_session_lineage_graph(db, "benchmark")),
            ):
                start = time.perf_counter()
                if name == "cold_dashboard" and profiler:
                    profiler.enable()
                result = operation()
                if name == "cold_dashboard" and profiler:
                    profiler.disable()
                timings[name] = (round((time.perf_counter() - start) * 1000), selects)
                if name == "cached_diagnostics":
                    payload_bytes = len(json.dumps(result, separators=(",", ":")).encode())
            peak = tracemalloc.get_traced_memory()[1] if trace_memory else None
        finally:
            if trace_memory:
                tracemalloc.stop()
            event.remove(engine, "before_cursor_execute", record)
        print(json.dumps({
            "requests": size, "blocks_per_request": blocks_per_request, "mode": mode,
            "timings_ms_and_cumulative_selects": timings,
            "diagnostics_payload_bytes": payload_bytes,
            "peak_traced_bytes": peak,
        }))
        if profiler:
            pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[100, 1000, 2000])
    parser.add_argument("--blocks", type=int, default=4)
    parser.add_argument("--mode", choices=["exact", "repeated"], default="exact")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--no-trace", action="store_true", help="time without allocation-tracing overhead")
    args = parser.parse_args()
    for requested_size in args.sizes:
        benchmark(requested_size, args.blocks, args.mode, args.profile,
                  trace_memory=not args.no_trace)
