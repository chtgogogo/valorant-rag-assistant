# -*- coding: utf-8 -*-
"""
审计汇总报表（v3.22）：读审计 jsonl 出报表，终端打印 + 落盘 md 到 eval/reports/

统计口径：
  - 总请求数 / 拒答数与拒答率（error_class=refusal 计拒答）
  - error_class 分布（rate_limit/timeout/empty_answer/generation_error/refusal/none）
  - P50 / P95 端到端延迟
  - token 总量与每轮均值（仅统计有 token_usage 的记录；估算口径标注占比）
  - 按日期分列（请求量 / 拒答 / 错误 / 平均延迟 / token）
  - 哈希链校验：断链即告警（旧格式无哈希的记录自动跳过）

用法：
  python scripts/audit_report.py                 # 读全部审计文件
  python scripts/audit_report.py --days 7        # 只看最近 7 天
  python scripts/audit_report.py --file audit_202609.jsonl   # 指定文件
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.audit import AUDIT_DIR, verify_chain  # noqa: E402


def _pct(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def load_records(days: int = None, filename: str = None) -> list[dict]:
    """读审计 jsonl（默认全部；days 限最近 N 天；filename 指定单文件）"""
    records = []
    if not os.path.isdir(AUDIT_DIR):
        return records
    cutoff = time.time() - days * 86400 if days else 0
    names = [filename] if filename else sorted(
        n for n in os.listdir(AUDIT_DIR) if n.endswith(".jsonl"))
    for name in names:
        path = os.path.join(AUDIT_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 半截行（写一半崩溃的遗留）直接跳过
                    if cutoff:
                        try:
                            ts = time.mktime(time.strptime(rec["time"], "%Y-%m-%d %H:%M:%S"))
                            if ts < cutoff:
                                continue
                        except (KeyError, ValueError):
                            pass
                    records.append(rec)
        except OSError:
            continue
    return records


def build_report(records: list[dict]) -> dict:
    """纯统计（无 IO），供单测直接断言"""
    total = len(records)
    latencies = sorted(r["latency_ms"] for r in records if isinstance(r.get("latency_ms"), (int, float)))
    error_dist = Counter(r.get("error_class", "none") for r in records)
    refusals = error_dist.get("refusal", 0)

    token_total = token_prompt = token_completion = 0
    token_records = estimated_records = 0
    for r in records:
        tu = r.get("token_usage") or {}
        if tu.get("total_tokens"):
            token_records += 1
            token_total += tu.get("total_tokens", 0)
            token_prompt += tu.get("prompt_tokens", 0)
            token_completion += tu.get("completion_tokens", 0)
            if tu.get("estimated"):
                estimated_records += 1

    by_date: dict = defaultdict(lambda: {"requests": 0, "refusals": 0, "errors": 0,
                                         "latencies": [], "tokens": 0})
    for r in records:
        day = (r.get("time") or "")[:10] or "未知日期"
        d = by_date[day]
        d["requests"] += 1
        if r.get("error_class") == "refusal":
            d["refusals"] += 1
        if r.get("error_class") not in (None, "none"):
            d["errors"] += 1
        if isinstance(r.get("latency_ms"), (int, float)):
            d["latencies"].append(r["latency_ms"])
        d["tokens"] += (r.get("token_usage") or {}).get("total_tokens", 0)

    breaks = verify_chain(records)

    return {
        "total": total,
        "refusals": refusals,
        "refusal_rate": round(refusals / total, 4) if total else 0.0,
        "error_dist": dict(error_dist),
        "latency_p50": _pct(latencies, 0.50),
        "latency_p95": _pct(latencies, 0.95),
        "latency_mean": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "token_records": token_records,
        "estimated_records": estimated_records,
        "token_total": token_total,
        "token_prompt": token_prompt,
        "token_completion": token_completion,
        "token_per_turn": round(token_total / token_records, 1) if token_records else 0.0,
        "by_date": {day: {
            "requests": d["requests"],
            "refusals": d["refusals"],
            "errors": d["errors"],
            "p50_ms": _pct(sorted(d["latencies"]), 0.50),
            "tokens": d["tokens"],
        } for day, d in sorted(by_date.items())},
        "chain_breaks": breaks,
    }


def render_markdown(stats: dict) -> str:
    lines = [
        "# 审计汇总报表（audit_report）",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总请求数：**{stats['total']}**",
        f"- 拒答：{stats['refusals']} 次（拒答率 **{stats['refusal_rate'] * 100:.1f}%**）",
        f"- error_class 分布：{json.dumps(stats['error_dist'], ensure_ascii=False)}",
        f"- 延迟：P50={stats['latency_p50']}ms / P95={stats['latency_p95']}ms / "
        f"mean={stats['latency_mean']}ms",
        f"- Token：总量 {stats['token_total']}（prompt {stats['token_prompt']} + "
        f"completion {stats['token_completion']}），每轮均值 {stats['token_per_turn']}"
        f"（{stats['token_records']} 条有用量，其中 {stats['estimated_records']} 条为估算口径）",
        f"- 哈希链校验：{'✅ 全链完整' if not stats['chain_breaks'] else '⚠️ ' + '; '.join(stats['chain_breaks'][:5])}",
        "",
        "## 按日期分列",
        "",
        "| 日期 | 请求数 | 拒答 | 异常 | P50(ms) | Token |",
        "|---|---|---|---|---|---|",
    ]
    for day, d in stats["by_date"].items():
        lines.append(f"| {day} | {d['requests']} | {d['refusals']} | {d['errors']} "
                     f"| {d['p50_ms']} | {d['tokens']} |")
    lines.append("")
    lines.append("- 口径：拒答=error_class 为 refusal（低置信兜底/敏感词拦截）；"
                 "延迟为 /api 问答端到端 latency_ms；token 仅统计带 token_usage 的记录"
                 "（estimated=True 的条目为长度估算，非智谱真实返回）。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="审计汇总报表")
    parser.add_argument("--days", type=int, default=None, help="只统计最近 N 天")
    parser.add_argument("--file", default=None, help="指定审计文件名（如 audit_202609.jsonl）")
    args = parser.parse_args()

    records = load_records(days=args.days, filename=args.file)
    stats = build_report(records)
    md = render_markdown(stats)

    print(md)
    reports = Path(__file__).resolve().parent.parent / "eval" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / f"audit_report_{time.strftime('%Y%m%d_%H%M%S')}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(f"\n[审计报表] 共 {stats['total']} 条记录，报告已落盘: {out}")


if __name__ == "__main__":
    main()
