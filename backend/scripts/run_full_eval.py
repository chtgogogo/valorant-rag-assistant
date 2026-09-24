# -*- coding: utf-8 -*-
"""
全量评测总入口（v3.23）：依次跑 主集 / 变体 / L2 / 官方 四个评测集，
skip-llm 与带生成各一轮，报告全部落盘 eval/reports/，最后汇总一份总报告。

设计：
  - 每个评测集用独立子进程跑 evaluate_rag.py（评测开关在 import 时读环境变量，
    子进程隔离，互不污染；也与常驻服务进程完全隔离）
  - 子集失败自动重试一次，仍失败在总报告中如实标注
  - 带生成轮安排在低峰期跑（智谱限流风险低）；本轮会消耗真实 LLM 额度
    （主集 20 题生成 + 忠实度判分 + 拒答判定，变体/L2 类似，总量约百次调用级）

用法（在 backend 目录下）：
  python scripts/run_full_eval.py --skip-llm            # 全量 skip-llm 轮
  python scripts/run_full_eval.py --label lowpeak       # 带生成轮（低峰期）
"""
import argparse
import datetime
import os
import subprocess
import sys

parser = argparse.ArgumentParser(description="全量评测总入口")
parser.add_argument("--skip-llm", action="store_true", help="全链只测检索，不调大模型")
parser.add_argument("--label", default="full", help="本次运行标签（报告文件名用）")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = [
    ("main", "eval_set.json", "主集"),
    ("variants", "eval_set_variants.json", "变体"),
    ("l2", "eval_set_l2.json", "L2"),
    ("official", "eval_set_official.json", "官方"),
]


def run_suite(suite_file: str, python_exe: str) -> tuple[bool, str]:
    """单集评测（独立子进程）；失败重试一次。返回 (是否成功, 报告文件名或错误)"""
    env = dict(os.environ)
    env.setdefault("RAG_CRITIC", "0")   # 评测统一关 Critic（与历史口径一致）
    env.setdefault("LLM_TEMPERATURE", "0")
    env.setdefault("EMBEDDING_DEVICE", os.getenv("EMBEDDING_DEVICE", "cpu"))
    if args.skip_llm:
        pass  # --skip-llm 由命令行参数传给子进程

    for attempt in (1, 2):
        cmd = [python_exe, os.path.join(BACKEND_DIR, "scripts", "evaluate_rag.py"),
               "--mode", "hybrid", "--suite", os.path.join("eval", suite_file)]
        if args.skip_llm:
            cmd.append("--skip-llm")
        proc = subprocess.run(cmd, cwd=BACKEND_DIR, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            # 从子进程输出提取报告文件名
            report = ""
            for line in proc.stdout.splitlines():
                if "报告已保存" in line:
                    report = line.split(":")[-1].strip()
            return True, report or "(报告名未解析)"
        print(f"    第{attempt}次失败(退出码 {proc.returncode})：{proc.stderr[-200:] if proc.stderr else proc.stdout[-200:]}")
    return False, f"重试 1 次后仍失败"


def main():
    global args
    args = parser.parse_args()
    python_exe = sys.executable

    tag = "skipllm" if args.skip_llm else "gen"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"{'=' * 62}\n全量评测（{'skip-llm' if args.skip_llm else '带生成·低峰'}）  标签={args.label}\n{'=' * 62}")

    results = []
    for key, suite_file, cname in SUITES:
        print(f"\n--- [{cname}] {suite_file} ---")
        ok, report = run_suite(suite_file, python_exe)
        status = "✓" if ok else "✗"
        print(f"  [{cname}] {status} 报告: {report}")
        results.append({"suite": cname, "file": suite_file, "ok": ok, "report": report})

    # 汇总总报告
    rep_dir = os.path.join(BACKEND_DIR, "eval", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    summary = os.path.join(rep_dir, f"report_full_{tag}_{ts}.md")
    with open(summary, "w", encoding="utf-8") as f:
        f.write(f"# 全量评测总报告 · {args.label}（{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
        f.write(f"- 模式：{'skip-llm（纯检索口径）' if args.skip_llm else '带生成（含关键词覆盖/忠实度，低峰执行）'}\n")
        f.write(f"- 四集合计 52 题（主集 20 + 变体 8 + L2 18 + 官方 6）；单集明细见各子报告\n\n")
        f.write("| 评测集 | 状态 | 子报告 |\n|---|---|---|\n")
        for r in results:
            f.write(f"| {r['suite']}（{r['file']}） | {'✓ 成功' if r['ok'] else '✗ 失败（重试 1 次后仍失败）'} | {r['report']} |\n")
        failed = [r for r in results if not r["ok"]]
        if failed:
            f.write(f"\n⚠️ 失败子集：{', '.join(r['suite'] for r in failed)}——需人工复核后补跑。\n")
    print(f"\n{'=' * 62}\n总报告已落盘: {summary}\n{'=' * 62}")


if __name__ == "__main__":
    main()
