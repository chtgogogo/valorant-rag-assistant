# 【v3.4】Critic 评审员 A/B 测试：裁决"自审要不要开思考"
# ------------------------------------------------------------
# 用法（backend 目录下）：
#   LLM_CRITIC_THINKING=1 python scripts/eval_critic_judge.py   # 开思考版
#   LLM_CRITIC_THINKING=0 python scripts/eval_critic_judge.py   # 关思考版
# 读取 eval/eval_critic_judge.json（人工标注 10 题），报告判分准确率、
# 两类错误（危险=不足判充足 / 浪费=充足判不足）与耗时，报告落盘 eval/reports/。
# 决策规则：思考版准确率高 ≥10 个百分点 → 保留思考；否则自审默认关思考。
# ------------------------------------------------------------
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(BACKEND_DIR, "eval", "eval_critic_judge.json")


def main():
    thinking = os.getenv("LLM_CRITIC_THINKING", "0") == "1"
    with open(SUITE, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    # 思考开关在 import settings 前已由环境变量决定（本脚本不做 setdefault，完全由外部控制）
    from services.chat_service import _critic_judge
    from schemas.models import SearchResult

    ok_cnt = 0
    danger = 0   # 危险错误：资料不足却判充足（该重检索没重检索）
    wasted = 0   # 浪费错误：资料充足却判不足（白跑重检索）
    latencies = []
    rows = []
    for c in cases:
        results = [SearchResult(content=d, source="标注资料", score=0.65, doc_id=c["id"], dense_score=0.65)
                   for d in c["docs"]]
        t0 = time.time()
        verdict = _critic_judge(c["question"], results)
        elapsed = (time.time() - t0) * 1000
        latencies.append(elapsed)
        predicted = bool(verdict.get("sufficient", True))
        truth = c["label"] == "sufficient"
        ok = predicted == truth
        ok_cnt += 1 if ok else 0
        if not ok:
            if truth and not predicted:
                wasted += 1
            else:
                danger += 1
        rows.append((c["id"], c["question"], truth, predicted, ok, elapsed))

    n = len(cases)
    acc = ok_cnt / n
    lat_sorted = sorted(latencies)
    print(f"\n{'='*62}\nCritic 评审员 A/B  thinking={'ON' if thinking else 'OFF'}  标注={n} 题")
    for rid, q, truth, pred, ok, el in rows:
        mark = "✓" if ok else ("⚠危险" if (truth and not pred) else "△浪费")
        print(f"  [{rid}] {'充足' if truth else '不足'} → 判{'充足' if pred else '不足'} {mark} {el:.0f}ms  {q[:22]}")
    print(f"-"*62)
    print(f"  判分准确率 : {ok_cnt}/{n} = {acc:.0%}")
    print(f"  危险错误   : {danger}（不足判充足 → 该重检索没重检索）")
    print(f"  浪费错误   : {wasted}（充足判不足 → 白跑重检索）")
    print(f"  单题耗时   : P50 {lat_sorted[n//2]:.0f}ms / 最大 {lat_sorted[-1]:.0f}ms")
    print(f"{'='*62}\n")

    # 报告落盘
    import datetime
    rep_dir = os.path.join(BACKEND_DIR, "eval", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    rep = os.path.join(rep_dir, f"critic_judge_{'on' if thinking else 'off'}_{datetime.datetime.now():%Y%m%d_%H%M%S}.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"# Critic 评审员 A/B（thinking={'ON' if thinking else 'OFF'}，{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
        f.write(f"- 判分准确率: {ok_cnt}/{n} = {acc:.0%}\n- 危险错误: {danger}｜浪费错误: {wasted}\n")
        f.write(f"- 单题耗时: P50 {lat_sorted[n//2]:.0f}ms / 最大 {lat_sorted[-1]:.0f}ms\n\n")
        f.write("| id | 标注 | 判定 | 结果 | 耗时ms |\n|---|---|---|---|---|\n")
        for rid, q, truth, pred, ok, el in rows:
            f.write(f"| {rid} | {'充足' if truth else '不足'} | {'充足' if pred else '不足'} | {'✓' if ok else '✗'} | {el:.0f} |\n")
    print(f"  报告已保存: {rep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
