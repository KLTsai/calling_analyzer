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
import tomllib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

_DEFAULT_CONFIG = Path(__file__).parent / "config.toml"


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


def analyze(records: Iterable[str], config: dict) -> dict:
    """
    Single streaming pass over JSON Lines records.
    Returns a report dict; None-valued keys are placeholders for future tickets.
    """
    pricing = config.get("models", {})

    dept_cost: defaultdict[str, float] = defaultdict(float)
    total_cost = 0.0
    unattributable_cost = 0.0
    attribution_defects = 0
    unknown_models: set[str] = set()
    unparseable_count = 0
    time_reversal_count = 0
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
        prompt_tokens = int(rec.get("prompt_tokens") or 0)
        completion_tokens = int(rec.get("completion_tokens") or 0)

        if model in pricing:
            mp = pricing[model]
            cost: float | None = (
                prompt_tokens * mp["input"] + completion_tokens * mp["output"]
            ) / 1_000_000
        else:
            cost = None
            unknown_models.add(model)

        user_dept = (rec.get("user_department") or "").strip()
        user_id = (rec.get("user_id") or "").strip()
        key_dept = rec["key_department"]

        if cost is not None:
            if user_dept:
                dept_cost[user_dept] += cost
            else:
                dept_cost[key_dept] += cost
                unattributable_cost += cost
            total_cost += cost

        # Attribution defect: know the user, don't know their department
        if not user_dept and user_id:
            attribution_defects += 1

    departments = sorted(
        [{"name": k, "cost": v} for k, v in dept_cost.items()],
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
        # T2 (浪費)
        "retry_waste": None,
        "dead_loss": None,
        "duplicate_success_count": 0,
        # T3 (延遲)
        "latency": None,
        # T4 (異常偵測)
        "retry_storms": None,
        "error_rate_flags": None,
        # 資料品質
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
    args = parser.parse_args()

    config = _load_config(Path(args.config))

    with open(args.records, encoding="utf-8") as f:
        report = analyze(f, config)

    if args.json_output:
        print(render_json(report))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
