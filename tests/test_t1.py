"""T1 - 骨架與部門費用（tracer bullet）"""

import json

from analyzer import analyze

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_PRICING = {
    "sonnet": {"input": 2.00, "output": 10.00},
    "haiku":  {"input": 0.10, "output": 0.50},
}
_CONFIG = {"models": _PRICING}


def rec(**kwargs) -> str:
    base = {
        "ts":         "2026-11-08T10:00:00+08:00",
        "request_id":        "req-001",
        "attempt":           1,
        "key_alias":          "key-eng",
        "key_owner_dept":    "Engineering",
        "model":             "sonnet",
        "status":            "success",
        "prompt_tokens":     1000,
        "completion_tokens": 200,
        "latency_ms":        1000,
    }
    base.update(kwargs)
    return json.dumps(base)


def run(*lines: str, cfg=None) -> dict:
    return analyze(lines, cfg or _CONFIG)


# ---------------------------------------------------------------------------
# cost attribution
# ---------------------------------------------------------------------------

def test_cost_goes_to_end_user_dept_when_set():
    r = run(rec(end_user_dept="Product", key_owner_dept="Engineering"))
    names = {d["name"] for d in r["departments"]}
    assert "Product" in names
    assert "Engineering" not in names


def test_cost_goes_to_key_owner_dept_when_no_end_user_dept():
    r = run(rec(key_owner_dept="Engineering"))
    names = {d["name"] for d in r["departments"]}
    assert "Engineering" in names


def test_unattributable_fraction_numerator_and_denominator():
    # 2 direct calls + 1 agent call; direct calls are unattributable
    per = (1000 * 2.00 + 200 * 10.00) / 1_000_000
    r = run(
        rec(key_owner_dept="Engineering"),
        rec(key_owner_dept="Engineering"),
        rec(end_user_dept="Product"),
    )
    assert abs(r["unattributable_cost"] - per * 2) < 1e-9
    assert abs(r["total_cost"]          - per * 3) < 1e-9
    assert abs(r["unattributable_fraction"] - 2 / 3) < 1e-9


def test_attribution_defects_independent_of_unattributable_cost():
    # defect: end_user present, end_user_dept absent
    r = run(
        rec(end_user="u001"),              # defect + unattributable
        rec(key_owner_dept="Engineering"), # unattributable but NOT a defect
    )
    assert r["attribution_defects"] == 1
    assert r["unattributable_fraction"] == 1.0


def test_unattributable_breakdown_uses_agent_id():
    """agent_id 區分三種無法歸屬：直接呼叫（結構性）、Agent 未帶 end_user、有 end_user 但缺部門。"""
    per = (1000 * 2.00 + 200 * 10.00) / 1_000_000
    r = run(
        rec(request_id="r1"),                                        # direct call
        rec(request_id="r2", agent_id=None, end_user=None),          # direct call, explicit nulls
        rec(request_id="r3", agent_id="agent-batch"),                # agent, no end_user
        rec(request_id="r4", agent_id="agent-x", end_user="u001"),   # defect
        rec(request_id="r5", agent_id="agent-x", end_user="u002", end_user_dept="Product"),
    )
    b = r["unattributable_breakdown"]
    assert abs(b["direct"] - per * 2) < 1e-9
    assert abs(b["agent_without_end_user"] - per) < 1e-9
    assert abs(b["end_user_without_dept"] - per) < 1e-9
    assert abs(r["unattributable_cost"] - per * 4) < 1e-9
    assert r["attribution_defects"] == 1


def test_blank_optional_fields_treated_as_empty():
    r = run(rec(agent_id="", end_user="  ", end_user_dept=""))
    assert r["attribution_defects"] == 0
    assert r["unattributable_breakdown"]["direct"] > 0


def test_attribution_defect_zero_when_end_user_dept_present():
    r = run(rec(end_user="u001", end_user_dept="Product"))
    assert r["attribution_defects"] == 0


# ---------------------------------------------------------------------------
# input / output priced separately
# ---------------------------------------------------------------------------

def test_input_and_output_use_separate_rates():
    r_in  = run(rec(model="sonnet", prompt_tokens=1_000_000, completion_tokens=0))
    r_out = run(rec(model="sonnet", prompt_tokens=0, completion_tokens=1_000_000))
    assert abs(r_in["total_cost"]  - 2.00) < 1e-9
    assert abs(r_out["total_cost"] - 10.00) < 1e-9


# ---------------------------------------------------------------------------
# failures still billed
# ---------------------------------------------------------------------------

def test_error_attempt_still_charged():
    r = run(rec(status="error", prompt_tokens=1000, completion_tokens=0))
    assert r["total_cost"] > 0.0


def test_timeout_attempt_still_charged():
    r = run(rec(status="timeout", prompt_tokens=1000, completion_tokens=0))
    assert r["total_cost"] > 0.0


def test_timeout_partial_completion_tokens_charged():
    # mid-stream timeout: some completion tokens produced, all billed
    expected = (2000 * 2.00 + 150 * 10.00) / 1_000_000
    r = run(rec(status="timeout", prompt_tokens=2000, completion_tokens=150))
    assert abs(r["total_cost"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# unknown model
# ---------------------------------------------------------------------------

def test_unknown_model_listed_not_zero():
    r = run(rec(model="ghost-model-9000"))
    assert [u["model"] for u in r["unpriced_usage"]] == ["ghost-model-9000"]
    # unknown cost must not leak into total
    assert r["total_cost"] == 0.0


def test_unknown_model_does_not_appear_in_department_costs():
    r = run(rec(model="ghost-model-9000"))
    assert all(d["cost"] > 0 for d in r["departments"])  # vacuously true (empty list)
    assert r["departments"] == []


def test_known_and_unknown_models_total_excludes_unknown():
    per = (1000 * 2.00 + 200 * 10.00) / 1_000_000
    r = run(rec(model="sonnet"), rec(model="ghost-model-9000"))
    assert abs(r["total_cost"] - per) < 1e-9
    assert [u["model"] for u in r["unpriced_usage"]] == ["ghost-model-9000"]


# ---------------------------------------------------------------------------
# dirty data
# ---------------------------------------------------------------------------

def test_unparseable_line_counted_not_crashed():
    r = run("not json at all", rec())
    assert r["unparseable_count"] == 1
    assert r["total_cost"] > 0.0  # valid record still processed


def test_missing_required_field_counted():
    bad = json.dumps({"ts": "2026-11-08T10:00:00+08:00", "request_id": "r1"})
    r = run(bad, rec())
    assert r["unparseable_count"] == 1


def test_empty_lines_ignored():
    r = run("", "   ", rec())
    assert r["unparseable_count"] == 0
    assert r["total_cost"] > 0.0


# ---------------------------------------------------------------------------
# report structure — no average values anywhere
# ---------------------------------------------------------------------------

def test_no_average_field_in_report():
    r = run(rec())
    for key in r:
        assert "avg" not in key.lower()
        assert "mean" not in key.lower()
        assert "average" not in key.lower()


# ---------------------------------------------------------------------------
# departments sorted by cost descending
# ---------------------------------------------------------------------------

def test_departments_sorted_by_cost_descending():
    # Engineering gets 1 record, Product gets 1 high-cost record
    r = run(
        rec(key_owner_dept="Engineering", prompt_tokens=100, completion_tokens=10),
        rec(key_owner_dept="Product",     prompt_tokens=10000, completion_tokens=1000),
    )
    assert r["departments"][0]["name"] == "Product"
    assert r["departments"][1]["name"] == "Engineering"
