"""T4 - 各模型成功請求的延遲百分位"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_CONFIG = {"models": {"sonnet": {"input": 2.0, "output": 10.0}}}


def rec(ts=None, model="sonnet", status="success", latency=1000, **kw):
    t = (ts or _BASE).isoformat()
    base = {
        "timestamp": t,
        "request_id": "req-001",
        "attempt": 1,
        "key_name": "key-eng",
        "key_department": "Engineering",
        "model": model,
        "status": status,
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": latency,
    }
    base.update(kw)
    return json.dumps(base)


def run(*lines, cfg=None):
    return analyze(lines, cfg or _CONFIG)


# ---------------------------------------------------------------------------
# only success attempts counted
# ---------------------------------------------------------------------------

def test_latency_counts_only_success():
    r = run(
        rec(status="success", latency=1000),
        rec(status="error",   latency=9999),
        rec(status="timeout", latency=50000),
    )
    lat = r["latency"]["sonnet"]
    assert lat["count"] == 1
    assert lat["p50"] == 1000
    assert lat["p95"] == 1000


def test_failed_attempts_not_in_latency():
    r = run(rec(status="error", latency=5000))
    assert "sonnet" not in r["latency"]


# ---------------------------------------------------------------------------
# nearest-rank P50 / P95 small samples
# ---------------------------------------------------------------------------

def test_p50_nearest_rank_odd_count():
    # n=5: rank ceil(0.5*5)=3, so median is sorted_values[2]
    vals = [100, 200, 300, 400, 500]
    r = run(*[rec(latency=v, request_id=f"r{v}") for v in vals])
    assert r["latency"]["sonnet"]["p50"] == 300


def test_p95_nearest_rank_small_sample():
    # n=4: rank ceil(0.95*4)=4, so p95 is sorted_values[3] = max
    vals = [100, 200, 300, 400]
    r = run(*[rec(latency=v, request_id=f"r{v}") for v in vals])
    assert r["latency"]["sonnet"]["p95"] == 400


def test_p50_single_value():
    r = run(rec(latency=777))
    assert r["latency"]["sonnet"]["p50"] == 777
    assert r["latency"]["sonnet"]["p95"] == 777


def test_latency_per_model():
    cfg = {"models": {
        "sonnet": {"input": 2.0, "output": 10.0},
        "haiku":  {"input": 0.1, "output": 0.5},
    }}
    r = run(
        rec(model="sonnet", latency=1000, request_id="r1"),
        rec(model="haiku",  latency=500,  request_id="r2"),
        cfg=cfg,
    )
    assert r["latency"]["sonnet"]["p50"] == 1000
    assert r["latency"]["haiku"]["p50"] == 500


# ---------------------------------------------------------------------------
# no average values in report
# ---------------------------------------------------------------------------

def test_no_average_field_in_report():
    r = run(rec())
    report_str = json.dumps(r)
    for bad in ("avg", "mean", "average"):
        assert bad not in report_str.lower()


def test_no_average_field_in_latency_dict():
    r = run(rec(latency=1000))
    lat = r["latency"].get("sonnet", {})
    for key in lat:
        assert "avg" not in key.lower()
        assert "mean" not in key.lower()
        assert "average" not in key.lower()
