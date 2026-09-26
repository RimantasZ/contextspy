from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from contextspy.db import crud
from contextspy.db.models import Base, Request


def _request(request_id: str, *, provider_input_tokens, cache_read_tokens, cache_creation_tokens=0):
    return Request(
        id=request_id,
        timestamp=datetime.now(timezone.utc),
        provider="anthropic",
        endpoint="/v1/messages",
        tokens_total_input=provider_input_tokens or 0,
        provider_input_tokens=provider_input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )


def test_cache_stats_average_vs_token_weighted_overall():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add_all([
            _request("big", provider_input_tokens=100_000, cache_read_tokens=95_000),
            _request("small-1", provider_input_tokens=10_000, cache_read_tokens=5_000),
            _request("small-2", provider_input_tokens=10_000, cache_read_tokens=5_000),
        ])
        db.commit()

        stats = crud.get_stats(db)

    cache = stats["cache"]
    assert cache["reporting_request_count"] == 3
    # (95 + 50 + 50) / 3 = 65.0 — each request weighted equally.
    assert cache["avg_pct"] == 65.0
    # (95000 + 5000 + 5000) / (100000 + 10000 + 10000) = 87.5 — token-weighted.
    assert cache["overall_pct"] == 87.5


def test_cache_stats_excludes_requests_without_cache_reporting():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add_all([
            _request("reports", provider_input_tokens=1_000, cache_read_tokens=500),
            Request(
                id="no-report",
                timestamp=datetime.now(timezone.utc),
                provider="openai_chat",
                endpoint="/v1/chat/completions",
                tokens_total_input=1_000,
                provider_input_tokens=1_000,
                cache_read_tokens=None,
                cache_creation_tokens=None,
            ),
        ])
        db.commit()

        stats = crud.get_stats(db)

    assert stats["cache"] == {"avg_pct": 50.0, "overall_pct": 50.0, "reporting_request_count": 1}


def test_cache_stats_empty_when_no_requests():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        stats = crud.get_stats(db)

    assert stats["cache"] == {"avg_pct": None, "overall_pct": None, "reporting_request_count": 0}
