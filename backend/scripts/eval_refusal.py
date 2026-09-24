# -*- coding: utf-8 -*-
"""
拒答+误杀双向量评测（v3.23）：跑 backend/eval/eval_set_refusal.json 的 16 题，
输出两个核心数字——拒答正确率（应拒的题被正确拒答的比例）与误杀率（必答的题被
错误拒答的比例）——逐题表格落盘 eval/reports/。

用法（在 backend 目录下）：
  python scripts/eval_refusal.py --skip-llm   # 只测检索/兜底判定，不调大模型
  python scripts/eval_refusal.py              # 带生成（拒答判定含答案话术特征）
"""
import argparse
import json
import os
import sys
import time

parser = argparse.ArgumentParser(description="拒答+误杀双向量评测")
parser.add_argument("--skip-llm", action="store_true", help="跳过答案生成，只测检索与兜底判定")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, BACKEND_DIR)


def main(args):
    from services.chat_service import _retrieve, _build_rag_messages, _call_llm_with_retry, _should_fallback  # noqa: E402
    from schemas.models import ChatMessage  # noqa: E402
    from scripts.evaluate_rag import judge_refusal, REFUSAL_MARKERS  # noqa: E402
    from config.settings import FALLBACK_ANSWER  # noqa: E402

    _ = _should_fallback  # noqa: F841  供 skip-llm 分支使用（同真实管线同一套判定）

    suite_path = os.path.join(BACKEND_DIR, "eval", "eval_set_refusal.json")
    with open(suite_path, "r", encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    # 预热（模型冷启动不进延迟统计）
    _retrieve("预热：无畏契约英雄和武器介绍", [], "valorant")

    print(f"\n{'=' * 62}\n拒答+误杀双向量评测  用例={len(cases)}  生成={'关闭' if args.skip_llm else '开启'}\n{'=' * 62}")

    rows = []
    refuse_total = refuse_ok = 0       # 应拒题：拒答正确率分母/分子
    must_total = must_killed = 0       # 必答题：误杀率分母/被拒数
    ms_list = []
    for case in cases:
        q = case["question"]
        expect_refusal = case.get("expect_refusal", False)
        t0 = time.time()
        _rw, results = _retrieve(q, [], case.get("kb_id", "valorant"))
        retrieve_ms = round((time.time() - t0) * 1000)
        ms_list.append(retrieve_ms)

        if args.skip_llm:
            refused = not results or _should_fallback(results)
            note = ""
        else:
            # 【v3.23 修复】与真实管线同口径：先兜底判定（分数<阈值→兜底拒答），
            # 放行才生成——否则"分数不够却硬生成"会虚增漏拒
            if not results or _should_fallback(results):
                answer = FALLBACK_ANSWER
            else:
                prompt = _build_rag_messages([], results, q)
                answer = _call_llm_with_retry(prompt, {"question": q})
            refused = judge_refusal(answer, results)
            note = answer[:40].replace("\n", " ")

        top1 = f"{results[0].source}@{round(results[0].score, 3)}" if results else "-"
        if expect_refusal:
            refuse_total += 1
            refuse_ok += 1 if refused else 0
            verdict = "✓正确拒答" if refused else "✗漏拒（错误回答）"
        else:
            must_total += 1
            must_killed += 1 if refused else 0
            verdict = "✗误杀（该答未答）" if refused else "✓正常回答"
        rows.append({"id": case["id"], "type": case["type"], "q": q,
                     "expect": "应拒" if expect_refusal else "必答",
                     "refused": refused, "top1": top1, "ms": retrieve_ms,
                     "verdict": verdict, "note": note})
        print(f"  [{case['id']}] {verdict}  top1={top1}  {retrieve_ms}ms  {q[:28]}")

    refuse_rate = refuse_ok / refuse_total if refuse_total else 0.0
    kill_rate = must_killed / must_total if must_total else 0.0
    ms_sorted = sorted(ms_list)
    p50 = ms_sorted[len(ms_sorted) // 2]

    print(f"\n{'-' * 62}\n汇总")
    print(f"  拒答正确率（应拒 8 题被正确拒答）: {refuse_ok}/{refuse_total} = {refuse_rate:.0%}")
    print(f"  误杀率    （必答 8 题被错误拒答）: {must_killed}/{must_total} = {kill_rate:.0%}")
    print(f"  检索延迟 P50: {p50}ms")

    # 落盘报告
    import datetime
    rep_dir = os.path.join(BACKEND_DIR, "eval", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    rep = os.path.join(rep_dir, f"report_refusal_{datetime.datetime.now():%Y%m%d_%H%M%S}.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"# 拒答+误杀双向量评测（{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
        f.write(f"- 生成：{'关（skip-llm，仅检索+兜底判定）' if args.skip_llm else '开（含答案话术特征判定）'}\n")
        f.write(f"- **拒答正确率: {refuse_ok}/{refuse_total} = {refuse_rate:.0%}**（应拒题被正确拒答）\n")
        f.write(f"- **误杀率: {must_killed}/{must_total} = {kill_rate:.0%}**（必答题被错误拒答）\n")
        f.write(f"- 检索延迟 P50: {p50}ms\n\n")
        f.write("| id | 期望 | 判定 | top1来源@分数 | 耗时ms | 问题 | 答案/备注 |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['id']} | {r['expect']} | {r['verdict']} | {r['top1']} | {r['ms']} "
                    f"| {r['q']} | {r['note']} |\n")
        f.write("\n- 口径：skip-llm 时拒答判定=检索为空或兜底阈值（与真实管线同口径）；"
                "带生成时判定=来源为空或答案命中拒答话术特征。X02/X03 为设计上的真实难点"
                "（检索有分数但库内确实无答案），漏拒如实计入拒答正确率。\n")
    print(f"  报告已落盘: {rep}")


if __name__ == "__main__":
    _args = parser.parse_args()
    main(_args)
