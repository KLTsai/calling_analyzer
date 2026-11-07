# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import sys

if sys.version_info < (3, 11):
    sys.exit(
        f"需要 Python 3.11 以上。目前版本：{sys.version.split()[0]}"
    )

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

_TZ_PLUS8 = timezone(timedelta(hours=8))

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

# (user_id, user_department) for agent calls
_USERS = [
    ("u001", "Engineering"),
    ("u002", "Engineering"),
    ("u003", "Product"),
    ("u004", "Product"),
    ("u005", "Data"),
    ("u006", "Finance"),
    ("u007", "HR"),
]


def _rec(
    ts: datetime, request_id: str, attempt: int,
    key_name: str, key_dept: str,
    user_id: str, user_dept: str,
    model: str, status: str,
    prompt_tokens: int, completion_tokens: int, latency_ms: int,
) -> dict:
    r: dict = {
        "timestamp": ts.isoformat(),
        "request_id": request_id,
        "attempt": attempt,
        "key_name": key_name,
        "key_department": key_dept,
        "model": model,
        "status": status,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
    }
    if user_id:
        r["user_id"] = user_id
    if user_dept:
        r["user_department"] = user_dept
    return r


def generate(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    base = datetime(2026, 11, 8, 8, 0, 0, tzinfo=_TZ_PLUS8)
    day_s = 16 * 3600  # 08:00–24:00

    records: list[dict] = []

    # === Deterministic injections (not affected by seed) ===

    # Pattern 2: Attribution defects — user_id present, user_department absent
    for i in range(20):
        ts = base + timedelta(hours=2, minutes=i)
        records.append(_rec(
            ts, f"req-defect-{i:03d}", 1,
            "key-eng-1", "Engineering",
            f"u-defect-{i:03d}", "",
            "claude-haiku-5-5", "success",
            500, 100, 800,
        ))

    # Pattern 4: timeout with nonzero completion_tokens (mid-stream timeout)
    for i in range(10):
        ts = base + timedelta(hours=4, minutes=i * 2)
        records.append(_rec(
            ts, f"req-timeout-{i:03d}", 1,
            "key-data-1", "Data",
            "", "",
            "claude-sonnet-5-5", "timeout",
            8000, 350, 45000,
        ))

    # === Background traffic (seed-driven) ===
    n_bg = max(0, n - len(records))
    for i in range(n_bg):
        offset = rng.uniform(0, day_s)
        ts = base + timedelta(seconds=offset)

        key_name, key_dept = rng.choice(_KEYS)
        model = rng.choices(_MODELS, weights=_MODEL_WEIGHTS, k=1)[0]

        # ~60 % direct calls, ~40 % agent calls with user info
        if rng.random() < 0.60:
            user_id, user_dept = "", ""
        else:
            user_id, user_dept = rng.choice(_USERS)

        # prompt: lognormal mean ~3 000, P95 ~11 000
        prompt_tokens = min(int(rng.lognormvariate(7.5, 1.1)), 32000)
        # completion: lognormal mean ~550
        completion_tokens = min(int(rng.lognormvariate(6.0, 0.8)), 4000)

        base_lat = prompt_tokens * 0.05 + completion_tokens * 2.5
        latency_ms = max(50, int(base_lat * rng.lognormvariate(0, 0.3)))

        r = rng.random()
        if r < 0.0035:
            status, completion_tokens = "error", 0
        elif r < 0.005:
            status = "timeout"
            completion_tokens = int(completion_tokens * rng.uniform(0.1, 0.5))
        else:
            status = "success"

        records.append(_rec(
            ts, f"req-{i + 1:06d}", 1,
            key_name, key_dept,
            user_id, user_dept,
            model, status,
            prompt_tokens, completion_tokens, latency_ms,
        ))

    records.sort(key=lambda r: datetime.fromisoformat(r["timestamp"]))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="generate",
        description="產生 LLM Gateway 模擬 JSON Lines 記錄",
    )
    parser.add_argument("--seed",  type=int, default=42,     help="隨機種子（預設 42）")
    parser.add_argument("-n",      type=int, default=2000,   help="記錄筆數（預設 2000）")
    parser.add_argument("-o", "--output", default="-",       help="輸出路徑（預設 stdout）")
    args = parser.parse_args()

    records = generate(args.n, args.seed)

    if args.output == "-":
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
