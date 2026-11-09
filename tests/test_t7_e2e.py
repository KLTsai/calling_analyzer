"""T7 端對端測試 - 子行程執行，斷言範例資料頭條數字"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_ANALYZER = _ROOT / "analyzer.py"
_GENERATE = _ROOT / "generate.py"
_SAMPLE = _ROOT / "sample_data.jsonl"


def _run_json(*extra_args):
    result = subprocess.run(
        [sys.executable, str(_ANALYZER), str(_SAMPLE), "--json", *extra_args],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_sample_data_headline_numbers():
    """Headline figures from sample_data.jsonl (seed=42) must match README."""
    r = _run_json()
    # Total cost from 2 000 records
    assert abs(r["total_cost"] - 11.9041) < 0.0001
    # Attribution: >60% unattributable (direct calls are majority)
    assert r["unattributable_fraction"] > 0.60
    # Exactly 20 attribution defects (injected deterministically)
    assert r["attribution_defects"] == 20
    # The injected batch agent (10 calls, no end_user) is split out from direct calls
    assert abs(r["unattributable_breakdown"]["agent_without_end_user"] - 0.20) < 1e-9
    # Waste: 5 retried-then-succeeded + 1 storm + 1 duplicate success
    assert r["retry_waste"]["requests"] == 7
    assert abs(r["retry_waste"]["cost"] - 0.01265) < 1e-9
    # Exactly 1 duplicate success (injected deterministically)
    assert r["duplicate_success_count"] == 1
    # Exactly 2 unparseable lines (the bad lines appended by generate.py)
    assert r["unparseable_reasons"] == {"invalid_json": 1, "missing_fields": 1}
    # And its token usage is reported, not silently dropped
    usage = {u["model"]: u for u in r["unpriced_usage"]}
    assert usage["unpriced-model-zzz"]["attempts"] == 1
    assert usage["unpriced-model-zzz"]["prompt_tokens"] == 1000
    assert usage["unpriced-model-zzz"]["completion_tokens"] == 200


def test_sample_data_anomalies():
    """Deterministic anomalies must be present in sample_data.jsonl."""
    r = _run_json()
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    # Dedicated retry storm pattern
    assert "req-storm-000" in storm_ids
    # Cross-boundary incident: the only well-sampled flag, at its peak window
    assert [(f["key_alias"], f["errors"], f["sample_count"])
            for f in r["error_rate_flags"]] == [("key-incident", 36, 100)]
    # Injected incidents on low-traffic keys are listed, not silently dropped
    low = [f["key_alias"] for f in r["error_rate_low_sample"]]
    assert low[:3] == ["key-prod-1", "key-data-1", "key-eng-2"]
    assert "key-schedule" in low


def test_different_seed_has_same_deterministic_anomalies():
    """Seed only affects background traffic; injected patterns are seed-independent."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
        tmpfile = f.name
    try:
        subprocess.run(
            [sys.executable, str(_GENERATE), "--seed", "999", "-o", tmpfile],
            check=True,
        )
        result = subprocess.run(
            [sys.executable, str(_ANALYZER), tmpfile, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        r = json.loads(result.stdout)
        storm_ids = {s["request_id"] for s in r["retry_storms"]}
        flagged_keys = {f["key_alias"] for f in r["error_rate_flags"]}
        # Deterministic patterns must survive seed change
        assert "req-storm-000" in storm_ids
        assert "key-incident" in flagged_keys
        assert r["attribution_defects"] == 20
        assert r["duplicate_success_count"] == 1
    finally:
        os.unlink(tmpfile)


def test_readme_sample_output_matches_actual_output():
    """README 承諾貼出的範例輸出跟審查者自己跑出來的一模一樣。"""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("```\n=====") + len("```\n")
    documented = readme[start:readme.index("```", start)].rstrip("\n")
    result = subprocess.run(
        [sys.executable, str(_ANALYZER), str(_SAMPLE)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.rstrip("\n") == documented
