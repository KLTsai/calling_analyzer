"""T2 - 重試浪費與純失敗浪費"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_PRICING = {"sonnet": {"input": 2.00, "output": 10.00}}
_CONFIG = {"models": _PRICING}


def rec(ts=None, request_id="req-001", attempt=1, status="success",
        prompt=1000, compl=200, **kw):
    t = ts or _BASE.isoformat()
    base = {
        "timestamp": t,
        "request_id": request_id,
        "attempt": attempt,
        "key_name": "key-eng",
        "key_department": "Engineering",
        "model": "sonnet",
        "status": status,
        "prompt_tokens": prompt,
        "completion_tokens": compl,
        "latency_ms": 1000,
    }
    base.update(kw)
    return json.dumps(base)


def ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).isoformat()


def run(*lines, cfg=None):
    return analyze(lines, cfg or _CONFIG)


def _cost(prompt, compl):
    return (prompt * 2.0 + compl * 10.0) / 1_000_000


# ---------------------------------------------------------------------------
# retry waste
# ---------------------------------------------------------------------------

def test_failed_attempt_before_success_is_retry_waste():
    r = run(
        rec(ts=ts(0),  request_id="req-a", attempt=1, status="error",  prompt=1000, compl=0),
        rec(ts=ts(10), request_id="req-a", attempt=2, status="success", prompt=1000, compl=200),
    )
    assert abs(r["retry_waste"] - _cost(1000, 0)) < 1e-9
    assert r["dead_loss"] == 0.0


def test_all_success_no_retry_waste():
    r = run(rec())
    assert r["retry_waste"] == 0.0
    assert r["dead_loss"] == 0.0


def test_multiple_failed_attempts_before_success_all_counted():
    r = run(
        rec(ts=ts(0),  request_id="req-b", attempt=1, status="error",   prompt=500, compl=0),
        rec(ts=ts(10), request_id="req-b", attempt=2, status="timeout",  prompt=500, compl=50),
        rec(ts=ts(20), request_id="req-b", attempt=3, status="success",  prompt=500, compl=200),
    )
    expected = _cost(500, 0) + _cost(500, 50)
    assert abs(r["retry_waste"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# dead loss
# ---------------------------------------------------------------------------

def test_all_failed_request_is_dead_loss():
    r = run(
        rec(ts=ts(0),  request_id="req-c", attempt=1, status="error", prompt=1000, compl=0),
        rec(ts=ts(10), request_id="req-c", attempt=2, status="error", prompt=1000, compl=0),
        rec(ts=ts(20), request_id="req-c", attempt=3, status="error", prompt=1000, compl=0),
    )
    expected = _cost(1000, 0) * 3
    assert abs(r["dead_loss"] - expected) < 1e-9
    assert r["retry_waste"] == 0.0


def test_timeout_with_partial_tokens_counted_in_dead_loss():
    r = run(
        rec(ts=ts(0),  request_id="req-d", attempt=1, status="timeout", prompt=5000, compl=300),
        rec(ts=ts(30), request_id="req-d", attempt=2, status="error",   prompt=5000, compl=0),
    )
    expected = _cost(5000, 300) + _cost(5000, 0)
    assert abs(r["dead_loss"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# retry_waste and dead_loss are separate numbers
# ---------------------------------------------------------------------------

def test_retry_waste_and_dead_loss_are_independent():
    r = run(
        # Request X: retried-then-succeeded
        rec(ts=ts(0),  request_id="req-x", attempt=1, status="error",   prompt=1000, compl=0),
        rec(ts=ts(10), request_id="req-x", attempt=2, status="success",  prompt=1000, compl=200),
        # Request Y: never succeeded
        rec(ts=ts(20), request_id="req-y", attempt=1, status="error",    prompt=800, compl=0),
        rec(ts=ts(30), request_id="req-y", attempt=2, status="error",    prompt=800, compl=0),
    )
    assert r["retry_waste"] > 0
    assert r["dead_loss"] > 0
    assert abs(r["retry_waste"] - _cost(1000, 0)) < 1e-9
    assert abs(r["dead_loss"] - _cost(800, 0) * 2) < 1e-9


# ---------------------------------------------------------------------------
# duplicate success
# ---------------------------------------------------------------------------

def test_duplicate_success_uses_first_rest_becomes_retry_waste():
    r = run(
        rec(ts=ts(0),  request_id="req-dup", attempt=1, status="success", prompt=1000, compl=200),
        rec(ts=ts(30), request_id="req-dup", attempt=2, status="success", prompt=1000, compl=200),
    )
    assert r["duplicate_success_count"] == 1
    # Second success counted as retry waste
    assert abs(r["retry_waste"] - _cost(1000, 200)) < 1e-9


def test_duplicate_success_triggers_warning():
    r = run(
        rec(ts=ts(0),  request_id="req-dup2", attempt=1, status="success"),
        rec(ts=ts(30), request_id="req-dup2", attempt=2, status="success"),
    )
    assert r["duplicate_success_count"] >= 1


def test_no_duplicate_success_when_only_one_success():
    r = run(rec())
    assert r["duplicate_success_count"] == 0


# ---------------------------------------------------------------------------
# unknown model attempts are excluded from waste totals
# ---------------------------------------------------------------------------

def test_unknown_model_attempts_not_in_waste():
    r = run(
        rec(ts=ts(0),  request_id="req-u", attempt=1, status="error",   model="unknown-m"),
        rec(ts=ts(10), request_id="req-u", attempt=2, status="success",  model="unknown-m"),
    )
    assert r["retry_waste"] == 0.0  # unknown model cost is None, not counted
