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
    assert abs(r["total_cost"] - 11.72) < 0.05
    # Attribution: >60% unattributable (direct calls are majority)
    assert r["unattributable_fraction"] > 0.60
    # Exactly 20 attribution defects (injected deterministically)
    assert r["attribution_defects"] == 20
    # Exactly 1 duplicate success (injected deterministically)
    assert r["duplicate_success_count"] == 1
    # Exactly 2 unparseable lines (the bad lines appended by generate.py)
    assert r["unparseable_count"] == 2
    # The unpriced model is flagged
    assert "unpriced-model-zzz" in r["unknown_models"]


def test_sample_data_anomalies():
    """Deterministic anomalies must be present in sample_data.jsonl."""
    r = _run_json()
    storm_ids = {s["request_id"] for s in r["retry_storms"]}
    # Dedicated retry storm pattern
    assert "req-storm-000" in storm_ids
    # Cross-boundary incident key flagged for error rate
    flagged_keys = {f["key_name"] for f in r["error_rate_flags"]}
    assert "key-incident" in flagged_keys


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
        flagged_keys = {f["key_name"] for f in r["error_rate_flags"]}
        # Deterministic patterns must survive seed change
        assert "req-storm-000" in storm_ids
        assert "key-incident" in flagged_keys
        assert r["attribution_defects"] == 20
        assert r["duplicate_success_count"] == 1
    finally:
        os.unlink(tmpfile)
