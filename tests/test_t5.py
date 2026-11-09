"""T5 - 髒資料與韌性"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_CONFIG = {"models": {"sonnet": {"input": 2.0, "output": 10.0}}}


def rec(**kw):
    base = {
        "ts": _BASE.isoformat(),
        "request_id": "req-001",
        "attempt": 1,
        "key_alias": "key-eng",
        "key_owner_dept": "Engineering",
        "model": "sonnet",
        "status": "success",
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": 1000,
    }
    base.update(kw)
    return json.dumps(base)


def run(*lines, cfg=None):
    return analyze(lines, cfg or _CONFIG)


# ---------------------------------------------------------------------------
# unparseable lines
# ---------------------------------------------------------------------------

def test_invalid_json_counted_not_crashed():
    r = run("{{not valid json}}", rec())
    assert r["unparseable_count"] == 1
    assert r["total_cost"] > 0


def test_missing_required_field_counted():
    bad = json.dumps({"ts": _BASE.isoformat(), "request_id": "r1"})
    r = run(bad, rec())
    assert r["unparseable_count"] == 1


def test_bad_timestamp_counted():
    bad = json.dumps({
        "ts": "not-a-timestamp",
        "request_id": "r1", "attempt": 1,
        "key_alias": "k", "key_owner_dept": "d",
        "model": "sonnet", "status": "success",
        "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1,
    })
    r = run(bad, rec())
    assert r["unparseable_count"] == 1


def test_empty_lines_not_counted():
    r = run("", "   ", "\t", rec())
    assert r["unparseable_count"] == 0
    assert r["total_cost"] > 0


def test_multiple_bad_lines_counted():
    r = run("bad1", "bad2", "bad3", rec())
    assert r["unparseable_count"] == 3


# ---------------------------------------------------------------------------
# unknown model
# ---------------------------------------------------------------------------

def test_unknown_model_listed():
    r = run(rec(model="ghost-model"))
    assert [u["model"] for u in r["unpriced_usage"]] == ["ghost-model"]


def test_unknown_model_cost_not_in_total():
    r = run(rec(model="ghost-model"))
    assert r["total_cost"] == 0.0


def test_unknown_model_zero_department_cost():
    r = run(rec(model="ghost-model"))
    assert r["departments"] == []


def test_known_plus_unknown_total_excludes_unknown():
    cost_known = (1000 * 2.0 + 200 * 10.0) / 1_000_000
    r = run(rec(model="sonnet"), rec(model="ghost-model", request_id="r2"))
    assert abs(r["total_cost"] - cost_known) < 1e-9
    assert [u["model"] for u in r["unpriced_usage"]] == ["ghost-model"]


# ---------------------------------------------------------------------------
# warnings are present (not silently dropped)
# ---------------------------------------------------------------------------

def test_unknown_model_warning_in_text_output():
    from analyzer import render_text
    r = run(rec(model="spooky-model"))
    text = render_text(r)
    assert "spooky-model" in text
    assert "未定價" in text


# ---------------------------------------------------------------------------
# unpriced model: token counts tallied, cost cell shows 未定價
# ---------------------------------------------------------------------------

def test_unpriced_usage_tokens_tallied():
    r = run(
        rec(model="ghost-model", prompt_tokens=1234, completion_tokens=567),
        rec(model="ghost-model", prompt_tokens=100,  completion_tokens=50, request_id="r2"),
    )
    rows = {u["model"]: u for u in r["unpriced_usage"]}
    assert rows["ghost-model"]["prompt_tokens"] == 1334
    assert rows["ghost-model"]["completion_tokens"] == 617
    assert rows["ghost-model"]["attempts"] == 2


def test_unpriced_cost_cell_shows_literal_string():
    from analyzer import render_text
    r = run(rec(model="ghost-model", prompt_tokens=1000, completion_tokens=200))
    text = render_text(r)
    # The cost cell for the unpriced row must be the literal string "未定價",
    # never a number or "$0".
    assert "未定價" in text
    # And the row must carry the tokens so a reader sees the exposure.
    assert "1,000" in text
    assert "200" in text


def test_unpriced_usage_sorted_by_total_tokens_descending():
    r = run(
        rec(model="small-unpriced", prompt_tokens=10,   completion_tokens=5,  request_id="r1"),
        rec(model="big-unpriced",   prompt_tokens=5000, completion_tokens=1000, request_id="r2"),
    )
    assert r["unpriced_usage"][0]["model"] == "big-unpriced"


def test_priced_model_not_in_unpriced_usage():
    r = run(rec(model="sonnet"))
    assert r["unpriced_usage"] == []


def test_unparseable_warning_in_text_output():
    from analyzer import render_text
    r = run("bad json", rec())
    text = render_text(r)
    assert "無法解析" in text


# ---------------------------------------------------------------------------
# unparseable lines say why, so a wrong schema is obvious
# ---------------------------------------------------------------------------

def test_wrong_schema_reports_missing_field_names():
    """用不同欄位名稱（例如 timestamp / key_name）的資料，報表要指出缺哪些欄位，
    而不是安靜地輸出一份全是 0 的報表。"""
    old = json.loads(rec())
    old["timestamp"] = old.pop("ts")
    old["key_name"] = old.pop("key_alias")
    r = run(json.dumps(old), json.dumps(old))
    assert r["unparseable_count"] == 2
    assert r["unparseable_reasons"] == {"missing_fields": 2}
    assert r["missing_fields"] == {"ts": 2, "key_alias": 2}
    from analyzer import render_text
    text = render_text(r)
    assert "缺少必要欄位" in text and "ts（2）" in text


def test_null_required_field_counts_as_missing():
    r = run(rec(model=None), rec())
    assert r["missing_fields"] == {"model": 1}


def test_unknown_status_rejected():
    r = run(rec(status="ok"), rec())
    assert r["unparseable_reasons"] == {"invalid_value": 1}


def test_non_object_json_rejected():
    r = run("[1, 2]", "42", rec())
    assert r["unparseable_reasons"] == {"invalid_json": 2}


def test_naive_timestamp_treated_as_utc():
    r = run(rec(ts="2026-11-08T02:00:00"), rec(ts="2026-11-08T10:00:00+08:00", request_id="r2"))
    assert r["unparseable_count"] == 0
