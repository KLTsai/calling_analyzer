"""T3 - 異常偵測：滑動窗、兩條規則、最小樣本門檻"""

import json
from datetime import datetime, timedelta, timezone

from analyzer import analyze

_TZ8 = timezone(timedelta(hours=8))
_BASE = datetime(2026, 11, 8, 10, 0, 0, tzinfo=_TZ8)
_PRICING = {"sonnet": {"input": 2.00, "output": 10.00}}
_CFG_DEFAULT = {"models": _PRICING, "detection": {"min_samples": 20}}
_CFG_ZERO = {"models": _PRICING, "detection": {"min_samples": 0}}


def rec(ts=None, request_id="req-001", key_alias="key-a", status="success", **kw):
    t = ts or _BASE.isoformat()
    base = {
        "ts": t,
        "request_id": request_id,
        "attempt": 1,
        "key_alias": key_alias,
        "key_owner_dept": "Engineering",
        "model": "sonnet",
        "status": status,
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": 1000,
    }
    base.update(kw)
    return json.dumps(base)


def ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).isoformat()


def run(*lines, cfg=None):
    return analyze(lines, cfg or _CFG_DEFAULT)


# ---------------------------------------------------------------------------
# retry storm
# ---------------------------------------------------------------------------

def test_retry_storm_triggers_at_3_within_60s():
    r = run(
        rec(ts=ts(0),  request_id="req-s", status="error"),
        rec(ts=ts(20), request_id="req-s", status="error"),
        rec(ts=ts(40), request_id="req-s", status="success"),
    )
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    assert "req-s" in storm_ids


def test_retry_storm_does_not_trigger_when_third_attempt_outside_60s():
    r = run(
        rec(ts=ts(0),  request_id="req-ok", status="error"),
        rec(ts=ts(30), request_id="req-ok", status="error"),
        rec(ts=ts(61), request_id="req-ok", status="success"),
    )
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    assert "req-ok" not in storm_ids


def test_retry_storm_two_attempts_does_not_trigger():
    r = run(
        rec(ts=ts(0),  request_id="req-two", status="error"),
        rec(ts=ts(10), request_id="req-two", status="success"),
    )
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    assert "req-two" not in storm_ids


def test_different_request_ids_do_not_cross_contaminate():
    # req-a and req-b each have 2 attempts within 60s — neither triggers
    r = run(
        rec(ts=ts(0),  request_id="req-a", status="error"),
        rec(ts=ts(5),  request_id="req-b", status="error"),
        rec(ts=ts(10), request_id="req-a", status="success"),
        rec(ts=ts(15), request_id="req-b", status="success"),
    )
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    assert "req-a" not in storm_ids
    assert "req-b" not in storm_ids


# ---------------------------------------------------------------------------
# error rate anomaly — sliding window is required, not aligned-clock fixed grid
# ---------------------------------------------------------------------------

def test_sliding_window_catches_cross_boundary_incident():
    """
    Incident on key-incident from 14:13 to 14:16:57, error rate 45%.
    Flanked by normal traffic [14:10,14:13) and [14:17,14:20).

    Fixed-grid windows [14:10,14:15) and [14:15,14:20) each contain
    ~60 normal calls (0 errors) + ~40 incident calls (18 errors) = 18% — not triggered.

    A sliding window covering the incident window [14:13,14:17) sees
    80 calls with 36 errors = 45% — triggered.

    If the implementation is changed to an aligned fixed grid, this test goes red.
    """
    base_day = datetime(2026, 11, 8, tzinfo=_TZ8)
    t_norm_pre   = base_day.replace(hour=14, minute=10)
    t_incident   = base_day.replace(hour=14, minute=13)
    t_norm_post  = base_day.replace(hour=14, minute=17)

    lines = []
    # Normal before: 60 calls at 3s intervals, all success
    for i in range(60):
        lines.append(rec(
            ts=(t_norm_pre + timedelta(seconds=i * 3)).isoformat(),
            request_id=f"req-pre-{i}",
            key_alias="key-incident",
            status="success",
        ))
    # Incident: 80 calls, errors at positions 0–17 and 40–57 → 36 errors (45%)
    for i in range(80):
        is_err = (0 <= i <= 17) or (40 <= i <= 57)
        lines.append(rec(
            ts=(t_incident + timedelta(seconds=i * 3)).isoformat(),
            request_id=f"req-inc-{i}",
            key_alias="key-incident",
            status="error" if is_err else "success",
        ))
    # Normal after: 60 calls, all success
    for i in range(60):
        lines.append(rec(
            ts=(t_norm_post + timedelta(seconds=i * 3)).isoformat(),
            request_id=f"req-post-{i}",
            key_alias="key-incident",
            status="success",
        ))

    cfg = {"models": _PRICING, "detection": {"min_samples": 20}}
    r = run(*lines, cfg=cfg)
    flagged = {f["key_alias"] for f in r["error_rate_flags"]}
    assert "key-incident" in flagged


def test_error_rate_requires_min_samples_default_not_triggered():
    """key-schedule: 3 calls, 1 error (33.3%) — below min_samples=20, must NOT trigger."""
    t0 = _BASE
    r = run(
        rec(ts=t0.isoformat(),                          key_alias="key-sched", status="success"),
        rec(ts=(t0 + timedelta(minutes=1)).isoformat(), key_alias="key-sched", status="success"),
        rec(ts=(t0 + timedelta(minutes=2)).isoformat(), key_alias="key-sched", status="error"),
        cfg=_CFG_DEFAULT,
    )
    flagged = {f["key_alias"] for f in r["error_rate_flags"]}
    assert "key-sched" not in flagged
    # 不違反題目字面：沒被擋掉，而是列在低樣本區
    low = {f["key_alias"] for f in r["error_rate_low_sample"]}
    assert "key-sched" in low


def test_error_rate_paired_triggers_with_min_samples_zero():
    """Same data as above. With min_samples=0, 33.3% > 20% must trigger."""
    t0 = _BASE
    r = run(
        rec(ts=t0.isoformat(),                          key_alias="key-sched", status="success"),
        rec(ts=(t0 + timedelta(minutes=1)).isoformat(), key_alias="key-sched", status="success"),
        rec(ts=(t0 + timedelta(minutes=2)).isoformat(), key_alias="key-sched", status="error"),
        cfg=_CFG_ZERO,
    )
    flagged = {f["key_alias"] for f in r["error_rate_flags"]}
    assert "key-sched" in flagged


def test_error_rate_exactly_20_pct_does_not_trigger():
    """Rule is strictly greater than 20%.
    4 successes then 1 error; at the final push total=5, errors=1 → exactly 20%.
    min_samples=5 ensures the check runs at the final push only.
    20% is not strictly > 20% → must NOT trigger.
    """
    t0 = _BASE
    cfg = {"models": _PRICING, "detection": {"min_samples": 5}}
    lines = [
        rec(ts=(t0 + timedelta(seconds=i * 10)).isoformat(),
            key_alias="key-exact",
            status="success" if i < 4 else "error")
        for i in range(5)
    ]
    r = run(*lines, cfg=cfg)
    flagged = {f["key_alias"] for f in r["error_rate_flags"]}
    assert "key-exact" not in flagged


def test_error_rate_calls_outside_5min_window_expire():
    """Errors outside the 5-minute window must not inflate the current error rate.

    20 errors at t=0..190s (spaced 10s apart) — the window never reaches min_samples=30
    during the error phase, so flag_set stays empty.  Then 10 successes at t=500..509s:
    all errors have expired (last error at t=190, window start at t=200), so the window
    contains 0 errors / 10 total = 0% error rate → still not triggered.

    Without expiry, the window would contain 30 records with 20 errors = 66.7% → triggered.
    """
    t0 = _BASE
    cfg = {"models": _PRICING, "detection": {"min_samples": 30}}
    lines = []
    for i in range(20):
        lines.append(rec(
            ts=(t0 + timedelta(seconds=i * 10)).isoformat(),
            key_alias="key-expire",
            status="error",
        ))
    for i in range(10):
        lines.append(rec(
            ts=(t0 + timedelta(seconds=500 + i)).isoformat(),
            key_alias="key-expire",
            status="success",
        ))
    r = run(*lines, cfg=cfg)
    flagged = {f["key_alias"] for f in r["error_rate_flags"]}
    assert "key-expire" not in flagged


# ---------------------------------------------------------------------------
# anomalies do not contain employee IDs
# ---------------------------------------------------------------------------

def test_anomaly_section_has_no_end_user():
    r = run(
        rec(ts=ts(0),  request_id="req-priv", status="error",   end_user="u999"),
        rec(ts=ts(10), request_id="req-priv", status="error",   end_user="u999"),
        rec(ts=ts(20), request_id="req-priv", status="success", end_user="u999"),
    )
    report_str = json.dumps(r["retry_storms"])
    assert "u999" not in report_str


# ---------------------------------------------------------------------------
# every possible 5-minute window is checked, not only windows ending on a record
# ---------------------------------------------------------------------------

def _key_events(spec):
    """spec: list of (seconds, status). One request per event."""
    return [rec(ts=ts(t), request_id=f"r-{i}", key_alias="key-w", status=st)
            for i, (t, st) in enumerate(spec)]


def test_window_starting_at_burst_is_checked():
    """100 次成功之後連錯 20 次、然後沒有流量。以每筆記錄為結尾的窗都 ≤ 20%，
    但 [第一次錯誤, +5 分鐘] 是 20/20。"""
    spec = [(i * 0.6, "success") for i in range(100)]
    spec += [(61 + i, "error") for i in range(20)]
    r = run(*_key_events(spec))
    flags = {f["key_alias"]: f for f in r["error_rate_flags"]}
    assert flags["key-w"]["error_rate"] == 1.0
    assert flags["key-w"]["sample_count"] == 20


def test_window_neither_starting_nor_ending_on_burst_is_checked():
    """0 秒 100 次成功、200 秒 20 次錯誤、400 秒 100 次成功。以錯誤為開頭或結尾的窗
    都是 20/120；只有 [50, 350] 這種兩端都不落在記錄上的窗是 20/20。"""
    spec = [(0, "success")] * 100 + [(200, "error")] * 20 + [(400, "success")] * 100
    r = run(*_key_events(spec))
    flags = {f["key_alias"]: f for f in r["error_rate_flags"]}
    assert flags["key-w"]["error_rate"] == 1.0


def test_reported_rate_is_the_peak_window_with_its_time_range():
    """10 次錯誤後 50 次成功：最高的窗（≥ 20 樣本）是前 20 筆 10/20，不是最後一個超標窗。"""
    spec = [(i, "error" if i < 10 else "success") for i in range(60)]
    r = run(*_key_events(spec))
    f = r["error_rate_flags"][0]
    assert (f["errors"], f["sample_count"]) == (10, 20)
    assert f["error_rate"] == 0.5
    assert datetime.fromisoformat(f["window_start"]) == _BASE
    assert datetime.fromisoformat(f["window_end"]) == _BASE + timedelta(seconds=19)


def test_window_times_shown_in_configured_timezone():
    spec = [(i, "error") for i in range(20)]
    cfg = {**_CFG_DEFAULT, "timezone": {"display": "UTC"}}
    r = run(*_key_events(spec), cfg=cfg)
    assert r["error_rate_flags"][0]["window_start"] == "2026-11-08T02:00:00+00:00"


def test_error_rate_independent_of_file_order():
    spec = [(i * 5, "error" if i % 3 == 0 else "success") for i in range(60)]
    lines = _key_events(spec)
    assert run(*lines)["error_rate_flags"] == run(*reversed(lines))["error_rate_flags"]


def test_window_is_closed_five_minutes():
    """[0, 300] 含兩端：0 秒 10 次錯誤與 300 秒 30 次成功同在一個窗（10/40 = 25%）。"""
    spec = [(0, "error")] * 10 + [(300, "success")] * 30
    r = run(*_key_events(spec), cfg={"models": _PRICING, "detection": {"min_samples": 40}})
    assert r["error_rate_flags"][0]["sample_count"] == 40
    spec = [(0, "error")] * 10 + [(300.001, "success")] * 30
    r = run(*_key_events(spec), cfg={"models": _PRICING, "detection": {"min_samples": 40}})
    assert r["error_rate_flags"] == []


# ---------------------------------------------------------------------------
# low-sample tier
# ---------------------------------------------------------------------------

def test_low_sample_tier_ranks_by_error_count():
    """門檻以下單一一次錯誤就是 100%，所以低樣本區先比錯誤次數。"""
    lines = [rec(ts=ts(0), request_id="a", key_alias="key-lone", status="error")]
    lines += [rec(ts=ts(i), request_id=f"b{i}", key_alias="key-burst",
                  status="error" if i < 3 else "success") for i in range(4)]
    r = run(*lines)
    low = r["error_rate_low_sample"]
    assert [f["key_alias"] for f in low] == ["key-burst", "key-lone"]
    assert (low[0]["errors"], low[0]["sample_count"]) == (3, 3)


def test_key_with_enough_samples_not_also_listed_as_low_sample():
    spec = [(i, "error") for i in range(20)]
    r = run(*_key_events(spec))
    assert [f["key_alias"] for f in r["error_rate_flags"]] == ["key-w"]
    assert r["error_rate_low_sample"] == []


def test_min_samples_zero_moves_everything_to_flags():
    lines = [rec(ts=ts(0), request_id="a", key_alias="key-lone", status="error")]
    r = run(*lines, cfg=_CFG_ZERO)
    assert [f["key_alias"] for f in r["error_rate_flags"]] == ["key-lone"]
    assert r["error_rate_low_sample"] == []


def test_key_without_errors_not_listed():
    r = run(*[rec(ts=ts(i), request_id=f"r{i}") for i in range(30)])
    assert r["error_rate_flags"] == [] and r["error_rate_low_sample"] == []


# ---------------------------------------------------------------------------
# retry storm: time order, not file order
# ---------------------------------------------------------------------------

def test_retry_storm_not_triggered_by_out_of_order_records():
    """三次嘗試在 0、100、200 秒，寫進記錄的順序是 200、0、100：不是風暴。"""
    r = run(
        rec(ts=ts(200), request_id="req-o", attempt=3, status="error"),
        rec(ts=ts(0),   request_id="req-o", attempt=1, status="error"),
        rec(ts=ts(100), request_id="req-o", attempt=2, status="error"),
    )
    assert r["retry_storms"] == []


def test_retry_storm_detected_when_out_of_order():
    r = run(
        rec(ts=ts(40), request_id="req-o", attempt=3, status="success"),
        rec(ts=ts(0),  request_id="req-o", attempt=1, status="error"),
        rec(ts=ts(20), request_id="req-o", attempt=2, status="error"),
    )
    assert r["retry_storms"] == [{"request_id": "req-o", "attempt_count": 3}]


def test_retry_storm_60s_boundary_is_inclusive():
    r = run(
        rec(ts=ts(0),  request_id="req-b", status="error"),
        rec(ts=ts(30), request_id="req-b", status="error"),
        rec(ts=ts(60), request_id="req-b", status="success"),
    )
    assert {s["request_id"] for s in r["retry_storms"]} == {"req-b"}


def test_retry_storm_reports_peak_count_in_any_60s():
    r = run(*[rec(ts=ts(t), request_id="req-p", attempt=i + 1, status="error")
              for i, t in enumerate([0, 10, 20, 100, 105, 110, 115])])
    assert r["retry_storms"] == [{"request_id": "req-p", "attempt_count": 4}]
