# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import sys

if sys.version_info < (3, 11):
    sys.exit(
        f"需要 Python 3.11 以上（本工具使用標準庫 tomllib）。"
        f"目前版本：{sys.version.split()[0]}"
    )

import argparse
import json
import math
import tomllib
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

_DEFAULT_CONFIG = Path(__file__).parent / "config.toml"


class _SlidingWindow:
    """Per-key sliding window counter. O(1) amortized per push."""

    def __init__(self, duration_s: float) -> None:
        self._dur = timedelta(seconds=duration_s)
        self._dqs: dict[str, deque] = {}
        self._errs: dict[str, int] = {}

    def push(self, key: str, ts: datetime, is_error: bool = False) -> tuple[int, int]:
        """Push event; return (total, error_count) within the window."""
        if key not in self._dqs:
            self._dqs[key] = deque()
            self._errs[key] = 0
        dq = self._dqs[key]
        err = self._errs[key]
        dq.append((ts, is_error))
        if is_error:
            err += 1
        cutoff = ts - self._dur
        while dq and dq[0][0] < cutoff:
            _, old_err = dq.popleft()
            if old_err:
                err -= 1
        self._errs[key] = err
        return len(dq), err


def _load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_line(line: str) -> dict | None:
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    required = {
        "timestamp", "request_id", "attempt", "key_name",
        "key_department", "model", "status",
        "prompt_tokens", "completion_tokens", "latency_ms",
    }
    if not required.issubset(rec):
        return None
    return rec


def _nearest_rank(sorted_vals: list[int], p: float) -> int:
    n = len(sorted_vals)
    if n == 0:
        return 0
    return sorted_vals[math.ceil(p * n) - 1]


def analyze(
    records: Iterable[str],
    config: dict,
    *,
    include_users: bool = False,
) -> dict:
    """Single streaming pass. Returns a report dict."""
    pricing = config.get("models", {})
    min_samples = config.get("detection", {}).get("min_samples", 20)

    # T1: department costs
    dept_cost: defaultdict[str, float] = defaultdict(float)
    total_cost = 0.0
    unattributable_cost = 0.0
    attribution_defects = 0
    unknown_models: set[str] = set()
    # T2: per-request buffer for waste computation
    req_buf: defaultdict[str, list] = defaultdict(list)
    # T3: anomaly detection windows
    retry_win = _SlidingWindow(60)
    error_win = _SlidingWindow(300)
    storm_set: dict[str, int] = {}           # request_id -> max window count
    flag_set: dict[str, tuple[float, int]] = {}  # key_name -> (rate, sample_count)
    # T4: latency per model (success only)
    lat_buf: defaultdict[str, list[int]] = defaultdict(list)
    # T6: per-user costs
    user_costs: defaultdict[str, float] | None = (
        defaultdict(float) if include_users else None
    )
    # Data quality
    unparseable_count = 0
    time_reversal_count = 0
    duplicate_success_count = 0
    prev_ts: datetime | None = None

    for raw in records:
        line = raw.strip()
        if not line:
            continue

        rec = _parse_line(line)
        if rec is None:
            unparseable_count += 1
            continue

        try:
            ts = datetime.fromisoformat(rec["timestamp"])
        except (ValueError, TypeError):
            unparseable_count += 1
            continue

        if prev_ts is not None and ts < prev_ts:
            time_reversal_count += 1
        prev_ts = ts

        model = rec["model"]
        status = rec["status"]
        prompt_tokens = int(rec.get("prompt_tokens") or 0)
        completion_tokens = int(rec.get("completion_tokens") or 0)
        latency_ms = int(rec.get("latency_ms") or 0)
        request_id = rec["request_id"]
        key_name = rec["key_name"]
        key_dept = rec["key_department"]
        user_dept = (rec.get("user_department") or "").strip()
        user_id = (rec.get("user_id") or "").strip()

        # T1: cost
        if model in pricing:
            mp = pricing[model]
            cost: float | None = (
                prompt_tokens * mp["input"] + completion_tokens * mp["output"]
            ) / 1_000_000
        else:
            cost = None
            unknown_models.add(model)

        if cost is not None:
            if user_dept:
                dept_cost[user_dept] += cost
            else:
                dept_cost[key_dept] += cost
                unattributable_cost += cost
            total_cost += cost

        if not user_dept and user_id:
            attribution_defects += 1

        # T2: buffer attempt for waste
        req_buf[request_id].append((ts, status, cost))

        # T3: anomaly detection
        total, _ = retry_win.push(request_id, ts)
        if total >= 3:
            storm_set[request_id] = max(storm_set.get(request_id, 0), total)

        is_err = status in ("error", "timeout")
        total_w, err_w = error_win.push(key_name, ts, is_err)
        if total_w >= min_samples and err_w / total_w > 0.20:
            flag_set[key_name] = (err_w / total_w, total_w)

        # T4: latency (success only)
        if status == "success":
            lat_buf[model].append(latency_ms)

        # T6: per-user cost
        if include_users and user_id and cost is not None:
            user_costs[user_id] += cost  # type: ignore[index]

    # T2: post-pass waste computation
    retry_waste = 0.0
    dead_loss = 0.0
    for request_id, attempts in req_buf.items():
        attempts.sort(key=lambda a: a[0])  # sort by ts
        success_idxs = [i for i, (_, s, _) in enumerate(attempts) if s == "success"]
        if len(success_idxs) > 1:
            duplicate_success_count += 1
        if success_idxs:
            keep = success_idxs[0]
            for i, (_, _, c) in enumerate(attempts):
                if i != keep and c is not None:
                    retry_waste += c
        else:
            for _, _, c in attempts:
                if c is not None:
                    dead_loss += c

    # T3: build sorted anomaly lists
    retry_storms = sorted(
        [{"request_id": k, "attempt_count": v} for k, v in storm_set.items()],
        key=lambda x: (-x["attempt_count"], x["request_id"]),
    )
    error_rate_flags = sorted(
        [{"key_name": k, "error_rate": r, "sample_count": n}
         for k, (r, n) in flag_set.items()],
        key=lambda x: (-x["error_rate"], x["key_name"]),
    )

    # T4: nearest-rank percentiles
    latency = {}
    for model, vals in lat_buf.items():
        vals.sort()
        latency[model] = {
            "p50": _nearest_rank(vals, 0.50),
            "p95": _nearest_rank(vals, 0.95),
            "count": len(vals),
        }

    departments = sorted(
        [{"name": k, "cost": v} for k, v in dept_cost.items()],
        key=lambda x: x["cost"],
        reverse=True,
    )

    user_breakdown = None
    if include_users:
        user_breakdown = sorted(
            [{"user_id": k, "cost": v} for k, v in (user_costs or {}).items()],
            key=lambda x: x["cost"],
            reverse=True,
        )

    return {
        "departments": departments,
        "total_cost": total_cost,
        "unattributable_cost": unattributable_cost,
        "unattributable_fraction": (
            unattributable_cost / total_cost if total_cost else 0.0
        ),
        "attribution_defects": attribution_defects,
        "unknown_models": sorted(unknown_models),
        "retry_waste": retry_waste,
        "dead_loss": dead_loss,
        "duplicate_success_count": duplicate_success_count,
        "retry_storms": retry_storms,
        "error_rate_flags": error_rate_flags,
        "latency": latency,
        "user_breakdown": user_breakdown,
        "unparseable_count": unparseable_count,
        "time_reversal_count": time_reversal_count,
    }


def _fmt_cost(cost: float) -> str:
    return f"${cost:,.4f}" if cost < 1 else f"${cost:,.2f}"


def render_text(report: dict) -> str:
    W = 60
    out: list[str] = []

    out.append("=" * W)
    out.append("LLM Gateway 用量分析報表")
    out.append("=" * W)
    out.append("")

    # 部門費用
    out.append("── 部門費用 ──")
    out.append("")
    if report["departments"]:
        for d in report["departments"]:
            out.append(f"  {d['name']:<24}  {_fmt_cost(d['cost']):>12}")
    else:
        out.append("  （無已定價記錄）")
    out.append("")
    out.append(f"  {'總費用':<24}  {_fmt_cost(report['total_cost']):>12}")
    out.append("")
    unattr = report["unattributable_cost"]
    frac = report["unattributable_fraction"]
    out.append(f"  ⚠  無法歸屬費用：{_fmt_cost(unattr)}（佔總費用 {frac:.1%}）")
    if report["attribution_defects"]:
        n = report["attribution_defects"]
        out.append(f"     歸屬缺陷：{n} 筆（有使用者識別碼但查無部門資訊）")
    out.append("")

    # 浪費
    out.append("── 浪費 ──")
    out.append("")
    out.append(f"  重試浪費    {_fmt_cost(report['retry_waste']):>12}  （重試但最終成功的請求裡，失敗嘗試的費用）")
    out.append(f"  純失敗浪費  {_fmt_cost(report['dead_loss']):>12}  （從未成功的請求的全部費用）")
    if report["duplicate_success_count"]:
        n = report["duplicate_success_count"]
        out.append(f"")
        out.append(f"  ⚠  {n} 個請求出現多次成功——Gateway 可能重複送出，浪費數字可能偏低")
    out.append("")

    # 異常偵測
    out.append("── 異常偵測 ──")
    out.append("")
    storms = report["retry_storms"] or []
    flags = report["error_rate_flags"] or []
    if storms:
        out.append(f"  重試風暴（60 秒內 ≥ 3 次）：{len(storms)} 個請求")
        for s in storms[:10]:
            out.append(f"    {s['request_id']:<36}  {s['attempt_count']} 次")
        if len(storms) > 10:
            out.append(f"    … 共 {len(storms)} 個")
    else:
        out.append("  重試風暴：無")
    out.append("")
    if flags:
        out.append(f"  錯誤率異常（5 分鐘窗 > 20%，樣本 ≥ {report.get('_min_samples', 20)}）：{len(flags)} 把鑰匙")
        for f in flags:
            out.append(
                f"    {f['key_name']:<32}  {f['error_rate']:.1%}  樣本 {f['sample_count']}"
            )
    else:
        out.append("  錯誤率異常：無")
    out.append("")

    # 延遲
    out.append("── 各模型延遲（成功請求） ──")
    out.append("")
    lat = report["latency"] or {}
    if lat:
        out.append(f"  {'模型':<30}  {'P50':>8}  {'P95':>8}  {'筆數':>6}")
        out.append(f"  {'-'*30}  {'-'*8}  {'-'*8}  {'-'*6}")
        for model, v in sorted(lat.items(), key=lambda x: -x[1]["count"]):
            out.append(
                f"  {model:<30}  {v['p50']:>7}ms  {v['p95']:>7}ms  {v['count']:>6}"
            )
    else:
        out.append("  （無成功記錄）")
    out.append("")

    # 逐人明細
    if report.get("user_breakdown") is not None:
        out.append("── 逐人明細（--include-users） ──")
        out.append("")
        for u in report["user_breakdown"]:
            out.append(f"  {u['user_id']:<30}  {_fmt_cost(u['cost']):>12}")
        out.append("")

    # 資料品質警告
    warnings: list[str] = []
    if report["unknown_models"]:
        models_str = "、".join(report["unknown_models"])
        warnings.append(f"未定價模型（費用未計入總額）：{models_str}")
    if report["unparseable_count"]:
        warnings.append(f"{report['unparseable_count']} 行無法解析，已略過")
    if report["time_reversal_count"]:
        warnings.append(
            f"偵測到 {report['time_reversal_count']} 次時間倒置，滑動窗結果可能失準"
        )
    if warnings:
        out.append("── 資料品質 ──")
        out.append("")
        for w in warnings:
            out.append(f"  ⚠  {w}")
        out.append("")

    return "\n".join(out)


def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="analyzer",
        description="分析 LLM Gateway JSON Lines 用量記錄",
    )
    parser.add_argument("records", help="記錄檔路徑（JSON Lines）")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG),
        help="設定檔路徑（預設：config.toml）",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="輸出機器可讀 JSON",
    )
    parser.add_argument(
        "--include-users",
        action="store_true",
        help="輸出逐人費用明細（預設關閉，含員工識別碼）",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=None,
        metavar="N",
        help="覆蓋設定檔的 min_samples（用於展示門檻效果）",
    )
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    if args.min_samples is not None:
        config.setdefault("detection", {})["min_samples"] = args.min_samples

    with open(args.records, encoding="utf-8") as f:
        report = analyze(f, config, include_users=args.include_users)

    if args.json_output:
        print(render_json(report))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
