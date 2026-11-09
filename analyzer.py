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
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DEFAULT_CONFIG = Path(__file__).parent / "config.toml"
# Field names exactly as the assignment's Gateway schema defines them.
# Without these a line cannot be placed in time, grouped, priced or attributed.
REQUIRED = ("ts", "request_id", "attempt", "key_alias", "key_owner_dept", "model", "status")
# Gateways often log null tokens / latency on failed calls. Dropping those lines
# would hide exactly the errors the error-rate rule needs, so they are kept and counted.
NUMERIC = ("prompt_tokens", "completion_tokens", "latency_ms")
STATUSES = ("success", "error", "timeout")
STORM_WINDOW_S, STORM_MIN_ATTEMPTS = 60, 3
ERROR_WINDOW_S, ERROR_RATE_LIMIT = 300, 0.20
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _opt(rec: dict, field: str) -> str:
    """Optional field: missing, null and blank all mean empty."""
    v = rec.get(field)
    return "" if v is None else str(v).strip()


def _count(rec: dict, field: str) -> int | None:
    """Non-negative integer, or None when the field is empty."""
    if _opt(rec, field) == "":
        return None
    n = int(float(rec[field]))
    if n < 0:
        raise ValueError(f"{field} < 0")
    return n


def _parse(line: str, bad: Counter, missing: Counter) -> dict | None:
    try:
        rec = json.loads(line)
    except ValueError:
        rec = None
    if not isinstance(rec, dict):
        bad["invalid_json"] += 1
        return None
    absent = [f for f in REQUIRED if _opt(rec, f) == ""]
    if absent:
        bad["missing_fields"] += 1
        missing.update(absent)
        return None
    try:
        ts = datetime.fromisoformat(rec["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if rec["status"] not in STATUSES:
            raise ValueError(rec["status"])
        return {
            **{f: _opt(rec, f) for f in ("request_id", "key_alias", "key_owner_dept", "model",
                                         "status", "agent_id", "end_user", "end_user_dept")},
            "t": (ts - _EPOCH) // _US,  # integer µs: exact window arithmetic
            "attempt": int(rec["attempt"]),
            **{f: _count(rec, f) for f in NUMERIC},
        }
    except (ValueError, TypeError, OverflowError):
        bad["invalid_value"] += 1
        return None


def _windows(times: list[int], width_s: int) -> Iterator[tuple[int, int]]:
    """Yield (lo, hi) index ranges for every distinct set of events that a
    window [s, s + width] can cover, over sorted µs `times`.

    The covered set only changes when s crosses an event time t or t - width,
    so trying exactly those start points covers every possible window - not
    just windows that begin or end on an event.
    """
    w = width_s * 1_000_000 + 1  # closed [s, s+width] == half-open [s, s+w) in µs
    lo = hi = 0
    n = len(times)
    for s in sorted({*times, *(t - w for t in times)}):
        while lo < n and times[lo] < s:
            lo += 1
        while hi < n and times[hi] < s + w:
            hi += 1
        if hi > lo:
            yield lo, hi


def _nearest_rank(sorted_vals: list[int], p: float) -> int:
    return sorted_vals[math.ceil(p * len(sorted_vals)) - 1]


def _empty_waste() -> dict:
    return {"cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "requests": 0,
            "unpriced_attempts": 0}


def analyze(records: Iterable[str], config: dict, *, include_users: bool = False) -> dict:
    """Parse every line, then compute all report sections. Returns a report dict."""
    pricing = config.get("models", {})
    min_samples = config.get("detection", {}).get("min_samples", 20)
    tz_name = config.get("timezone", {}).get("display", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz, tz_name = timezone.utc, "UTC"

    dept_cost: defaultdict[str, float] = defaultdict(float)
    unattributed = {"direct": 0.0, "agent_without_end_user": 0.0, "end_user_without_dept": 0.0}
    total_cost = 0.0
    defect_reqs: set[str] = set()
    unpriced: defaultdict[str, dict] = defaultdict(
        lambda: {"prompt_tokens": 0, "completion_tokens": 0, "attempts": 0})
    by_request: defaultdict[str, list] = defaultdict(list)
    by_key: defaultdict[str, list] = defaultdict(list)
    latencies: defaultdict[str, list[int]] = defaultdict(list)
    user_cost: defaultdict[str, float] = defaultdict(float)
    bad: Counter = Counter()
    missing: Counter = Counter()
    null_tokens = missing_latency = 0

    for raw in records:
        line = raw.strip()
        if not line or not (r := _parse(line, bad, missing)):
            continue
        model = r["model"]
        if r["prompt_tokens"] is None or r["completion_tokens"] is None:
            null_tokens += 1
        p, c = r["prompt_tokens"] or 0, r["completion_tokens"] or 0
        cost = None
        if model in pricing:
            cost = (p * pricing[model]["input"] + c * pricing[model]["output"]) / 1_000_000
            total_cost += cost
            dept_cost[r["end_user_dept"] or r["key_owner_dept"]] += cost
            if include_users and r["end_user"]:
                user_cost[r["end_user"]] += cost
        else:
            u = unpriced[model]
            u["prompt_tokens"] += p
            u["completion_tokens"] += c
            u["attempts"] += 1
        if not r["end_user_dept"]:
            if r["end_user"]:
                category = "end_user_without_dept"
                defect_reqs.add(r["request_id"])
            else:
                category = "agent_without_end_user" if r["agent_id"] else "direct"
            unattributed[category] += cost or 0.0
        by_request[r["request_id"]].append((r["t"], r["attempt"], r["status"], cost, p, c))
        by_key[r["key_alias"]].append((r["t"], r["status"] != "success"))
        if r["status"] == "success":
            if r["latency_ms"] is None:
                missing_latency += 1
            else:
                latencies[model].append(r["latency_ms"])

    # Waste: per request, only the last successful attempt was useful.
    retry_waste, dead_loss = _empty_waste(), _empty_waste()
    duplicate_success = 0
    storms = []
    for rid, atts in by_request.items():
        atts.sort(key=lambda a: (a[0], a[1]))  # time order, not file order
        succ = [i for i, a in enumerate(atts) if a[2] == "success"]
        duplicate_success += len(succ) > 1
        wasted = [a for i, a in enumerate(atts) if not succ or i != succ[-1]]
        bucket = retry_waste if succ else dead_loss
        bucket["requests"] += bool(wasted)
        for *_, cost, p, c in wasted:
            bucket["cost"] += cost or 0.0
            bucket["unpriced_attempts"] += cost is None
            bucket["prompt_tokens"] += p
            bucket["completion_tokens"] += c
        if len(atts) >= STORM_MIN_ATTEMPTS:
            peak = max(hi - lo for lo, hi in _windows([a[0] for a in atts], STORM_WINDOW_S))
            if peak >= STORM_MIN_ATTEMPTS:
                storms.append({"request_id": rid, "attempt_count": peak})

    # Error rate: every key's worst window, split by whether it has enough samples.
    # Well-sampled windows rank by rate. Below min_samples a lone error already
    # reads 100%, so low-sample windows rank by error count, then rate.
    def fmt(t: int) -> str:
        return (_EPOCH + t * _US).astimezone(tz).isoformat(timespec="seconds")

    flags, low_sample = [], []
    for key, evs in by_key.items():
        evs.sort()
        prefix = [0]
        for _, is_err in evs:
            prefix.append(prefix[-1] + is_err)
        if not prefix[-1]:
            continue
        times = [t for t, _ in evs]
        best: dict[bool, tuple] = {}
        for lo, hi in _windows(times, ERROR_WINDOW_S):
            n, errs = hi - lo, prefix[hi] - prefix[lo]
            if errs / n > ERROR_RATE_LIMIT:
                tier = n >= min_samples
                score = (errs / n, n) if tier else (errs, errs / n)
                if tier not in best or score > best[tier][0]:
                    best[tier] = (score, errs / n, n, errs, lo, hi)
        if best:
            _, rate, n, errs, lo, hi = best[True] if True in best else best[False]
            (flags if True in best else low_sample).append({
                "key_alias": key, "error_rate": rate, "errors": errs, "sample_count": n,
                "window_start": fmt(times[lo]), "window_end": fmt(times[hi - 1]),
            })

    unattributable_cost = sum(unattributed.values())
    return {
        "departments": [{"name": k, "cost": v} for k, v in
                        sorted(dept_cost.items(), key=lambda x: -x[1])],
        "total_cost": total_cost,
        "unattributable_cost": unattributable_cost,
        "unattributable_fraction": unattributable_cost / total_cost if total_cost else 0.0,
        "unattributable_breakdown": unattributed,
        "attribution_defects": len(defect_reqs),
        "unpriced_usage": sorted(
            [{"model": m, **v} for m, v in unpriced.items()],
            key=lambda x: -(x["prompt_tokens"] + x["completion_tokens"])),
        "retry_waste": retry_waste,
        "dead_loss": dead_loss,
        "duplicate_success_count": duplicate_success,
        "retry_storms": sorted(storms, key=lambda x: (-x["attempt_count"], x["request_id"])),
        "min_samples": min_samples,
        "display_timezone": tz_name,
        "error_rate_flags": sorted(flags, key=lambda x: (-x["error_rate"], x["key_alias"])),
        "error_rate_low_sample": sorted(low_sample, key=lambda x: (
            -x["errors"], -x["error_rate"], x["key_alias"])),
        "latency": {m: {"p50": _nearest_rank(v, 0.50), "p95": _nearest_rank(v, 0.95),
                        "count": len(v)}
                    for m, v in ((m, sorted(v)) for m, v in latencies.items())},
        "user_breakdown": [{"end_user": k, "cost": v} for k, v in sorted(
            user_cost.items(), key=lambda x: (-x[1], x[0]))] if include_users else None,
        "null_token_attempts": null_tokens,
        "success_without_latency": missing_latency,
        "unparseable_count": sum(bad.values()),
        "unparseable_reasons": dict(bad),
        "missing_fields": dict(missing.most_common()),
    }


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _l(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def _r(s: str, n: int) -> str:
    return " " * max(0, n - _width(s)) + s


def _fmt_cost(cost: float) -> str:
    return f"${cost:,.4f}"


def _fmt_window(start: str, end: str) -> str:
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return f"{a:%m-%d %H:%M:%S} ~ {b:%H:%M:%S}" if a.date() == b.date() \
        else f"{a:%m-%d %H:%M:%S} ~ {b:%m-%d %H:%M:%S}"


def render_text(report: dict) -> str:
    out = ["=" * 60, "LLM Gateway 用量分析報表", "=" * 60, "", "── 部門費用 ──", ""]
    for d in report["departments"] or [{"name": "（無已定價記錄）", "cost": 0.0}]:
        out.append(f"  {_l(d['name'], 30)}{_r(_fmt_cost(d['cost']), 13)}")
    out += ["", f"  {_l('總費用', 30)}{_r(_fmt_cost(report['total_cost']), 13)}", ""]
    if report["unattributable_cost"] > 0:
        b = report["unattributable_breakdown"]
        out.append(f"  ⚠  無法歸屬到實際使用者：{_fmt_cost(report['unattributable_cost'])}"
                   f"（佔總費用 {report['unattributable_fraction']:.1%}）")
        for label, k, note in [
            ("直接呼叫（無 agent_id）", "direct", "結構性，無從歸屬"),
            ("Agent 呼叫但未帶 end_user", "agent_without_end_user", "批次工作或 Agent 漏傳"),
            ("有 end_user 但無部門", "end_user_without_dept",
             f"歸屬缺陷，{report['attribution_defects']} 個請求"),
        ]:
            if b[k] or (k == "end_user_without_dept" and report["attribution_defects"]):
                out.append(f"       {_l(label, 26)}{_r(_fmt_cost(b[k]), 13)}  {note}")
        out.append("")

    out += ["── 浪費（非最後一次成功的嘗試） ──", "",
            f"  {_l('', 12)}{_r('費用', 13)}{_r('prompt tok', 13)}{_r('compl tok', 11)}"
            f"{_r('請求', 7)}"]
    for label, w in (("重試浪費", report["retry_waste"]), ("純失敗浪費", report["dead_loss"])):
        out.append(f"  {_l(label, 12)}{_r(_fmt_cost(w['cost']), 13)}"
                   f"{w['prompt_tokens']:>13,}{w['completion_tokens']:>11,}{w['requests']:>7,}")
    out += ["",
            "  重試浪費：最終有成功的請求裡，最後一次成功以外的嘗試",
            "  純失敗浪費：從未成功的請求的全部嘗試"]
    if n := report["retry_waste"]["unpriced_attempts"] + report["dead_loss"]["unpriced_attempts"]:
        out.append(f"  ⚠  其中 {n} 次嘗試是未定價模型：token 已計入，費用沒有計入")
    if n := report["duplicate_success_count"]:
        out.append(f"  ⚠  {n} 個請求出現多次成功 - Gateway 可能重複送出；"
                   "只保留最後一次，其餘已計入重試浪費")
    out.append("")

    out += [f"── 異常偵測（時間以 {report['display_timezone']} 顯示） ──", ""]
    storms = report["retry_storms"]
    out.append(f"  重試風暴（同一請求 60 秒內 ≥ 3 次嘗試）：{len(storms) or '無'}"
               + (" 個請求" if storms else ""))
    for s in storms[:10]:
        out.append(f"    {_l(s['request_id'], 30)}{s['attempt_count']:>3} 次")
    if len(storms) > 10:
        out.append(f"    … 共 {len(storms)} 個")
    ms = report["min_samples"]
    for title, rows, always in (
        (f"錯誤率異常（任一 5 分鐘 > 20%，樣本 ≥ {ms}）", report["error_rate_flags"], True),
        (f"低樣本，僅供參考（> 20% 但窗內樣本 < {ms}）", report["error_rate_low_sample"], False),
    ):
        if not rows and not always:
            continue
        out += ["", f"  {title}：{len(rows) or '無'}" + (" 把鑰匙" if rows else "")]
        for f in rows[:10]:
            out.append(f"    {_l(f['key_alias'], 18)}{f['error_rate']:>7.1%}"
                       f"{f['errors']:>6}/{f['sample_count']:<6} "
                       f"{_fmt_window(f['window_start'], f['window_end'])}")
        if len(rows) > 10:
            out.append(f"    … 共 {len(rows)} 把")
    out.append("")

    out += ["── 各模型延遲（成功請求） ──", ""]
    if report["latency"]:
        out.append(f"  {_l('模型', 26)}{_r('P50', 10)}{_r('P95', 10)}{_r('筆數', 8)}")
        out.append(f"  {'-' * 24}  {'-' * 8}  {'-' * 8}  {'-' * 6}")
        for model, v in sorted(report["latency"].items(), key=lambda x: -x[1]["count"]):
            out.append(f"  {_l(model, 26)}{v['p50']:>8}ms{v['p95']:>8}ms{v['count']:>8,}")
    else:
        out.append("  （無成功記錄）")
    out.append("")

    if report["user_breakdown"] is not None:
        out += ["── 逐人明細（--include-users） ──", ""]
        out += [f"  {_l(u['end_user'], 30)}{_r(_fmt_cost(u['cost']), 13)}"
                for u in report["user_breakdown"]] + [""]

    unpriced, n_bad = report["unpriced_usage"], report["unparseable_count"]
    n_null, n_lat = report["null_token_attempts"], report["success_without_latency"]
    has_quality = bool(unpriced or n_bad or n_null or n_lat)
    if has_quality:
        out += ["── 資料品質 ──", ""]
    if n_null:
        out.append(f"  ⚠  {n_null} 次嘗試的 token 是空值，以 0 計（費用與浪費可能偏低）")
    if n_lat:
        out.append(f"  ⚠  {n_lat} 次成功嘗試沒有 latency_ms，未納入延遲百分位")
    if unpriced:
        out.append("  ⚠  未定價模型（token 數已統計，費用未計入總額）：")
        out.append(f"       {_l('模型', 24)}{_r('prompt tok', 12)}{_r('compl tok', 11)}"
                   f"{_r('筆數', 7)}{_r('費用', 8)}")
        for u in unpriced:
            out.append(f"       {_l(u['model'], 24)}{u['prompt_tokens']:>12,}"
                       f"{u['completion_tokens']:>11,}{u['attempts']:>7,}{_r('未定價', 8)}")
    if n_bad:
        reasons = report["unparseable_reasons"]
        out.append(f"  ⚠  {n_bad} 行無法解析，已略過")
        if reasons.get("invalid_json"):
            out.append(f"       不是 JSON 物件：{reasons['invalid_json']} 行")
        if reasons.get("missing_fields"):
            fields = "、".join(f"{k}（{v}）" for k, v in report["missing_fields"].items())
            out.append(f"       缺少必要欄位：{reasons['missing_fields']} 行 - {fields}")
        if reasons.get("invalid_value"):
            out.append(f"       欄位值不合法（時間、負數或 status）：{reasons['invalid_value']} 行")
    if has_quality:
        out.append("")
    return "\n".join(out)


def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="analyzer", description="分析 LLM Gateway JSON Lines 用量記錄")
    parser.add_argument("records", help="記錄檔路徑（JSON Lines）")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="設定檔路徑（預設：config.toml）")
    parser.add_argument("--json", dest="json_output", action="store_true", help="輸出機器可讀 JSON")
    parser.add_argument("--include-users", action="store_true",
                        help="輸出逐人費用明細（預設關閉，含員工識別碼）")
    parser.add_argument("--min-samples", type=int, metavar="N",
                        help="覆蓋設定檔的 min_samples（錯誤率異常的樣本門檻）")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if args.min_samples is not None:
        config.setdefault("detection", {})["min_samples"] = args.min_samples
    with open(args.records, encoding="utf-8") as f:
        report = analyze(f, config, include_users=args.include_users)
    print(render_json(report) if args.json_output else render_text(report))


if __name__ == "__main__":
    main()
