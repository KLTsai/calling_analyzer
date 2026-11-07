# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import sys

if sys.version_info < (3, 11):
    sys.exit(f"需要 Python 3.11 以上。目前版本：{sys.version.split()[0]}")

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

_TZ8 = timezone(timedelta(hours=8))

_MODELS = [
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "gpt-5-4-mini",
    "gemini-3-5-flash-lite",
    "selfhost-qwen-32b",
]
_MODEL_WEIGHTS = [0.40, 0.25, 0.15, 0.10, 0.10]

_KEYS = [
    ("key-eng-1",     "Engineering"),
    ("key-eng-2",     "Engineering"),
    ("key-prod-1",    "Product"),
    ("key-data-1",    "Data"),
    ("key-finance-1", "Finance"),
    ("key-hr-1",      "HR"),
]
_USERS = [
    ("u001", "Engineering"),
    ("u002", "Engineering"),
    ("u003", "Product"),
    ("u004", "Product"),
    ("u005", "Data"),
    ("u006", "Finance"),
    ("u007", "HR"),
]


def _r(ts, rid, attempt, key, dept, uid, udept, model, status, prompt, compl, lat):
    rec = {
        "timestamp": ts.isoformat(),
        "request_id": rid,
        "attempt": attempt,
        "key_name": key,
        "key_department": dept,
        "model": model,
        "status": status,
        "prompt_tokens": prompt,
        "completion_tokens": compl,
        "latency_ms": lat,
    }
    if uid:
        rec["user_id"] = uid
    if udept:
        rec["user_department"] = udept
    return rec


def _base(hour, minute=0, second=0):
    return datetime(2026, 11, 8, hour, minute, second, tzinfo=_TZ8)


def _deterministic_records():
    """Ten injected symptom patterns. None of these depend on the random seed."""
    recs = []

    # Pattern 2: Attribution defects (user_id present, user_department absent)
    for i in range(20):
        ts = _base(10) + timedelta(minutes=i)
        recs.append(_r(ts, f"req-defect-{i:03d}", 1,
                       "key-eng-1", "Engineering", f"u-defect-{i:03d}", "",
                       "claude-haiku-5-5", "success", 500, 100, 800))

    # Pattern 3: Retry-then-success (attempt 1 fails, attempt 2 succeeds)
    for i in range(5):
        t0 = _base(9) + timedelta(minutes=i * 2)
        recs.append(_r(t0, f"req-retry-{i}", 1,
                       "key-eng-1", "Engineering", "", "",
                       "claude-sonnet-5-5", "error", 1000, 0, 500))
        recs.append(_r(t0 + timedelta(seconds=30), f"req-retry-{i}", 2,
                       "key-eng-1", "Engineering", "", "",
                       "claude-sonnet-5-5", "success", 1000, 300, 2000))

    # Pattern 4a: Never succeeds (3 error attempts, pure failure waste)
    for i in range(5):
        t0 = _base(9, 30) + timedelta(minutes=i * 3)
        for a in range(1, 4):
            recs.append(_r(t0 + timedelta(seconds=a * 20), f"req-never-{i}", a,
                           "key-prod-1", "Product", "", "",
                           "claude-haiku-5-5", "error", 800, 0, 300))

    # Pattern 4b: Timeout with nonzero completion_tokens (mid-stream billed)
    for i in range(10):
        ts = _base(12) + timedelta(minutes=i * 2)
        recs.append(_r(ts, f"req-timeout-{i:03d}", 1,
                       "key-data-1", "Data", "", "",
                       "claude-sonnet-5-5", "timeout", 8000, 350, 45000))

    # Pattern 5: Retry storm (4 attempts within 30 seconds)
    for a in range(1, 5):
        ts = _base(13) + timedelta(seconds=(a - 1) * 7)
        recs.append(_r(ts, "req-storm-000", a,
                       "key-eng-2", "Engineering", "", "",
                       "claude-haiku-5-5", "error" if a < 4 else "success",
                       500, 0 if a < 4 else 100, 300))

    # Pattern 6: Cross-alignment-boundary incident
    # key-incident: 20 calls/min from 14:10–14:12 (normal) and 14:17–14:19 (normal),
    # 14:13–14:16 is the incident (45% error rate).
    # Fixed-grid [14:10,14:15) and [14:15,14:20) each see ~18% error — no trigger.
    # Sliding window [14:13,14:17) sees 45% error — triggers.
    # Normal before [14:10, 14:13): 60 calls, 0 errors
    for i in range(60):
        ts = _base(14, 10) + timedelta(seconds=i * 3)
        recs.append(_r(ts, f"req-norm-pre-{i}", 1,
                       "key-incident", "Engineering", "", "",
                       "claude-haiku-5-5", "success", 300, 50, 400))
    # Incident [14:13, 14:17): 80 calls, errors at positions 0–17 and 40–57 = 36 errors (45%)
    for i in range(80):
        ts = _base(14, 13) + timedelta(seconds=i * 3)
        is_err = (0 <= i <= 17) or (40 <= i <= 57)
        recs.append(_r(ts, f"req-inc-{i:03d}", 1,
                       "key-incident", "Engineering", "", "",
                       "claude-haiku-5-5", "error" if is_err else "success",
                       300, 0 if is_err else 50, 300))
    # Normal after [14:17, 14:20): 60 calls, 0 errors
    for i in range(60):
        ts = _base(14, 17) + timedelta(seconds=i * 3)
        recs.append(_r(ts, f"req-norm-post-{i}", 1,
                       "key-incident", "Engineering", "", "",
                       "claude-haiku-5-5", "success", 300, 50, 400))

    # Pattern 7: Low-traffic false-positive decoy
    # key-schedule: 3 calls, 1 error (33.3% rate) — must NOT trigger at min_samples=20
    for i, status in enumerate(["success", "success", "error"]):
        ts = _base(11) + timedelta(minutes=i)
        recs.append(_r(ts, f"req-sched-{i}", 1,
                       "key-schedule", "Data", "", "",
                       "claude-haiku-5-5", status, 200, 0 if status == "error" else 50, 200))

    # Pattern 8: Duplicate success (same request_id appears twice as success)
    for a in [1, 2]:
        ts = _base(15) + timedelta(seconds=(a - 1) * 30)
        recs.append(_r(ts, "req-dup-000", a,
                       "key-eng-1", "Engineering", "", "",
                       "claude-sonnet-5-5", "success", 500, 150, 1200))

    # Pattern 10: unpriced model (always last timestamp for visibility)
    recs.append(_r(_base(23, 0), "req-unpriced-000", 1,
                   "key-eng-1", "Engineering", "", "",
                   "unpriced-model-zzz", "success", 1000, 200, 900))

    return recs


def generate(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    base = _base(8)
    day_s = 16 * 3600

    det = _deterministic_records()
    n_bg = max(0, n - len(det))

    records: list[dict] = list(det)

    for i in range(n_bg):
        offset = rng.uniform(0, day_s)
        ts = base + timedelta(seconds=offset)
        key_name, key_dept = rng.choice(_KEYS)
        model = rng.choices(_MODELS, weights=_MODEL_WEIGHTS, k=1)[0]

        if rng.random() < 0.60:
            uid, udept = "", ""
        else:
            uid, udept = rng.choice(_USERS)

        # prompt: lognormal mean ~3 000, P95 ~11 000
        prompt = min(int(rng.lognormvariate(7.5, 1.1)), 32000)
        # completion: lognormal mean ~550 (Pattern 9: long tail via lognormal)
        compl = min(int(rng.lognormvariate(6.0, 0.8)), 4000)
        base_lat = prompt * 0.05 + compl * 2.5
        lat = max(50, int(base_lat * rng.lognormvariate(0, 0.5)))  # heavy tail on latency

        r = rng.random()
        if r < 0.0035:
            status, compl = "error", 0
        elif r < 0.005:
            status = "timeout"
            compl = int(compl * rng.uniform(0.1, 0.5))
        else:
            status = "success"

        records.append(_r(ts, f"req-{i + 1:06d}", 1,
                          key_name, key_dept, uid, udept,
                          model, status, prompt, compl, lat))

    records.sort(key=lambda r: r["timestamp"])
    return records


# Lines the analyzer should report as unparseable (intentional for the demo)
_BAD_LINES = [
    "# DEMO: 無效 JSON 行（刻意插入，展示工具的錯誤容忍）",
    '{"timestamp":"2026-11-08T23:59:00+08:00","request_id":"bad-partial"}',
]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="generate",
        description="產生 LLM Gateway 模擬 JSON Lines 記錄",
    )
    parser.add_argument("--seed",  type=int, default=42,   help="隨機種子（預設 42）")
    parser.add_argument("-n",      type=int, default=2000, help="記錄筆數（預設 2000）")
    parser.add_argument("-o", "--output", default="-",     help="輸出路徑（預設 stdout）")
    args = parser.parse_args()

    records = generate(args.n, args.seed)

    def _write(f_or_none):
        for rec in records:
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            if f_or_none:
                f_or_none.write(line)
            else:
                sys.stdout.write(line)
        for bad in _BAD_LINES:
            if f_or_none:
                f_or_none.write(bad + "\n")
            else:
                sys.stdout.write(bad + "\n")

    if args.output == "-":
        _write(None)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            _write(f)


if __name__ == "__main__":
    main()
