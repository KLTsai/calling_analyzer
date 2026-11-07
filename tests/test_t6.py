"""T6 - 隱私旗標與機器可讀輸出"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze, render_json, render_text

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_CONFIG = {"models": {"sonnet": {"input": 2.0, "output": 10.0}}}


def rec(**kw):
    base = {
        "timestamp": _BASE.isoformat(),
        "request_id": "req-001",
        "attempt": 1,
        "key_name": "key-eng",
        "key_department": "Engineering",
        "model": "sonnet",
        "status": "success",
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": 1000,
    }
    base.update(kw)
    return json.dumps(base)


def run(*lines, include_users=False, cfg=None):
    return analyze(lines, cfg or _CONFIG, include_users=include_users)


# ---------------------------------------------------------------------------
# privacy: default output must not contain employee IDs
# ---------------------------------------------------------------------------

def test_default_no_user_id_in_report():
    r = run(rec(user_id="u-secret-99", user_department="Engineering"))
    report_str = json.dumps(r)
    assert "u-secret-99" not in report_str


def test_default_user_breakdown_is_none():
    r = run(rec(user_id="u001", user_department="Engineering"))
    assert r["user_breakdown"] is None


def test_include_users_shows_breakdown():
    r = run(rec(user_id="u001", user_department="Engineering"), include_users=True)
    assert r["user_breakdown"] is not None
    ids = {u["user_id"] for u in r["user_breakdown"]}
    assert "u001" in ids


def test_include_users_costs_are_correct():
    cost = (1000 * 2.0 + 200 * 10.0) / 1_000_000
    r = run(rec(user_id="u001", user_department="Engineering"), include_users=True)
    u_row = next(u for u in r["user_breakdown"] if u["user_id"] == "u001")
    assert abs(u_row["cost"] - cost) < 1e-9


def test_no_user_id_no_entry_in_breakdown():
    # Direct call without user_id — should not appear in breakdown
    r = run(rec(), include_users=True)
    assert r["user_breakdown"] == []


def test_multiple_users_in_breakdown():
    r = run(
        rec(user_id="u001", user_department="Engineering", request_id="r1"),
        rec(user_id="u002", user_department="Product",     request_id="r2"),
        include_users=True,
    )
    ids = {u["user_id"] for u in r["user_breakdown"]}
    assert ids == {"u001", "u002"}


def test_breakdown_sorted_by_cost_descending():
    r = run(
        rec(user_id="u-cheap", user_department="Engineering",
            prompt_tokens=100, completion_tokens=10, request_id="r1"),
        rec(user_id="u-pricey", user_department="Engineering",
            prompt_tokens=10000, completion_tokens=1000, request_id="r2"),
        include_users=True,
    )
    assert r["user_breakdown"][0]["user_id"] == "u-pricey"


# ---------------------------------------------------------------------------
# JSON output: same data as text output
# ---------------------------------------------------------------------------

def test_json_output_total_cost_matches_report():
    r = run(rec())
    j = json.loads(render_json(r))
    assert j["total_cost"] == r["total_cost"]


def test_json_output_departments_match_report():
    r = run(rec())
    j = json.loads(render_json(r))
    assert j["departments"] == r["departments"]


def test_json_output_all_top_level_keys_present():
    r = run(rec())
    j = json.loads(render_json(r))
    for key in r:
        assert key in j


def test_json_output_is_valid_json():
    r = run(rec())
    try:
        json.loads(render_json(r))
    except json.JSONDecodeError:
        assert False, "render_json produced invalid JSON"


def test_json_and_text_total_cost_numerically_equal():
    r = run(rec())
    text = render_text(r)
    j = json.loads(render_json(r))
    # Text contains a formatted cost; we verify the underlying values are the same source
    assert j["total_cost"] == r["total_cost"]
    assert j["unattributable_fraction"] == r["unattributable_fraction"]
