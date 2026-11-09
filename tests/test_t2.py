"""T2 - 浪費：非最後一次成功的嘗試所花的費用與 token"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze, render_text

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_PRICING = {"sonnet": {"input": 2.00, "output": 10.00}}
_CONFIG = {"models": _PRICING}


def rec(ts=None, request_id="req-001", attempt=1, status="success",
        prompt=1000, compl=200, **kw):
    base = {
        "ts": ts or _BASE.isoformat(),
        "request_id": request_id,
        "attempt": attempt,
        "key_alias": "key-eng",
        "key_owner_dept": "Engineering",
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
    # input=$2/1M, output=$10/1M — literals, independent of production code
    return prompt * 2e-6 + compl * 10e-6


# ---------------------------------------------------------------------------
# retry waste
# ---------------------------------------------------------------------------

def test_failed_attempt_before_success_is_retry_waste():
    r = run(
        rec(ts=ts(0),  request_id="req-a", attempt=1, status="error",  prompt=1000, compl=0),
        rec(ts=ts(10), request_id="req-a", attempt=2, status="success", prompt=1000, compl=200),
    )
    assert abs(r["retry_waste"]["cost"] - _cost(1000, 0)) < 1e-9
    assert r["retry_waste"]["requests"] == 1
    assert r["dead_loss"]["cost"] == 0.0


def test_all_success_no_waste():
    r = run(rec())
    assert r["retry_waste"] == {"cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                                "requests": 0, "unpriced_attempts": 0}
    assert r["dead_loss"]["requests"] == 0


def test_multiple_failed_attempts_before_success_all_counted():
    r = run(
        rec(ts=ts(0),  request_id="req-b", attempt=1, status="error",   prompt=500, compl=0),
        rec(ts=ts(10), request_id="req-b", attempt=2, status="timeout", prompt=500, compl=50),
        rec(ts=ts(20), request_id="req-b", attempt=3, status="success", prompt=500, compl=200),
    )
    assert abs(r["retry_waste"]["cost"] - (_cost(500, 0) + _cost(500, 50))) < 1e-9


def test_waste_reports_tokens_not_only_cost():
    """題目括號寫的是 token：浪費要能以 token 數呈現。"""
    r = run(
        rec(ts=ts(0),  request_id="req-t", attempt=1, status="timeout", prompt=700, compl=40),
        rec(ts=ts(10), request_id="req-t", attempt=2, status="success", prompt=700, compl=300),
        rec(ts=ts(20), request_id="req-d", attempt=1, status="error",   prompt=900, compl=0),
    )
    assert r["retry_waste"]["prompt_tokens"] == 700
    assert r["retry_waste"]["completion_tokens"] == 40
    assert r["dead_loss"]["prompt_tokens"] == 900
    assert r["dead_loss"]["completion_tokens"] == 0


# ---------------------------------------------------------------------------
# dead loss
# ---------------------------------------------------------------------------

def test_all_failed_request_is_dead_loss():
    r = run(*[rec(ts=ts(i * 10), request_id="req-c", attempt=i + 1, status="error",
                  prompt=1000, compl=0) for i in range(3)])
    assert abs(r["dead_loss"]["cost"] - _cost(1000, 0) * 3) < 1e-9
    assert r["dead_loss"]["requests"] == 1
    assert r["retry_waste"]["cost"] == 0.0


def test_timeout_with_partial_tokens_counted_in_dead_loss():
    r = run(
        rec(ts=ts(0),  request_id="req-d", attempt=1, status="timeout", prompt=5000, compl=300),
        rec(ts=ts(30), request_id="req-d", attempt=2, status="error",   prompt=5000, compl=0),
    )
    assert abs(r["dead_loss"]["cost"] - (_cost(5000, 300) + _cost(5000, 0))) < 1e-9


def test_retry_waste_and_dead_loss_are_independent():
    r = run(
        rec(ts=ts(0),  request_id="req-x", attempt=1, status="error",   prompt=1000, compl=0),
        rec(ts=ts(10), request_id="req-x", attempt=2, status="success", prompt=1000, compl=200),
        rec(ts=ts(20), request_id="req-y", attempt=1, status="error",   prompt=800, compl=0),
        rec(ts=ts(30), request_id="req-y", attempt=2, status="error",   prompt=800, compl=0),
    )
    assert abs(r["retry_waste"]["cost"] - _cost(1000, 0)) < 1e-9
    assert abs(r["dead_loss"]["cost"] - _cost(800, 0) * 2) < 1e-9


# ---------------------------------------------------------------------------
# "last success" is the one kept
# ---------------------------------------------------------------------------

def test_duplicate_success_keeps_last_and_earlier_success_is_waste():
    """兩次成功費用不同：留下最後一次，第一次算浪費（題目：非最後一次成功）。"""
    r = run(
        rec(ts=ts(0),  request_id="req-dup", attempt=1, status="success", prompt=100, compl=10),
        rec(ts=ts(30), request_id="req-dup", attempt=2, status="success", prompt=9000, compl=900),
    )
    assert r["duplicate_success_count"] == 1
    assert abs(r["retry_waste"]["cost"] - _cost(100, 10)) < 1e-9


def test_attempt_after_last_success_is_waste():
    r = run(
        rec(ts=ts(0),  request_id="req-z", attempt=1, status="success", prompt=100, compl=10),
        rec(ts=ts(10), request_id="req-z", attempt=2, status="error",   prompt=300, compl=0),
    )
    assert abs(r["retry_waste"]["cost"] - _cost(300, 0)) < 1e-9


def test_last_success_decided_by_time_not_file_order():
    """多台 Gateway 合併的記錄可能亂序：最後一次成功依 ts 判定，不依檔案順序。"""
    r = run(
        rec(ts=ts(30), request_id="req-o", attempt=2, status="success", prompt=9000, compl=900),
        rec(ts=ts(0),  request_id="req-o", attempt=1, status="success", prompt=100, compl=10),
    )
    assert abs(r["retry_waste"]["cost"] - _cost(100, 10)) < 1e-9


def test_same_ts_falls_back_to_attempt_number():
    r = run(
        rec(ts=ts(0), request_id="req-s", attempt=2, status="success", prompt=9000, compl=900),
        rec(ts=ts(0), request_id="req-s", attempt=1, status="success", prompt=100, compl=10),
    )
    assert abs(r["retry_waste"]["cost"] - _cost(100, 10)) < 1e-9


def test_duplicate_success_warning_says_it_is_already_counted():
    r = run(
        rec(ts=ts(0),  request_id="req-dup2", attempt=1, status="success"),
        rec(ts=ts(30), request_id="req-dup2", attempt=2, status="success"),
    )
    text = render_text(r)
    assert "多次成功" in text
    assert "已計入重試浪費" in text
    assert "偏低" not in text


def test_no_duplicate_success_when_only_one_success():
    assert run(rec())["duplicate_success_count"] == 0


# ---------------------------------------------------------------------------
# unpriced model attempts: no cost, but their tokens are still waste
# ---------------------------------------------------------------------------

def test_unpriced_model_waste_counts_tokens_not_cost():
    r = run(
        rec(ts=ts(0),  request_id="req-u", attempt=1, status="error",   model="unknown-m",
            prompt=400, compl=0),
        rec(ts=ts(10), request_id="req-u", attempt=2, status="success", model="unknown-m"),
    )
    assert r["retry_waste"]["cost"] == 0.0
    assert r["retry_waste"]["prompt_tokens"] == 400
    assert r["retry_waste"]["unpriced_attempts"] == 1
    assert "1 次嘗試是未定價模型" in render_text(r)


def test_priced_waste_has_no_unpriced_note():
    r = run(
        rec(ts=ts(0),  request_id="req-p", attempt=1, status="error"),
        rec(ts=ts(10), request_id="req-p", attempt=2, status="success"),
    )
    assert "未定價模型" not in render_text(r)
