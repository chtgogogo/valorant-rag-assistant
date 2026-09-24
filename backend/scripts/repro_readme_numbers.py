# -*- coding: utf-8 -*-
"""
最小复现落档（v3.24）：README 两个无落档数字的实测脚本。

① 限流最坏反馈耗时（v3.11 写的"2 分钟 → 2.3 秒"）：
   mock LLM 每次调用都抛 429（限流），计时 _call_llm_with_retry 从调用到返回
   兜底文案的真实耗时 = 重试退避(1.5s) + 主模型失败时间 + 兜底模型尝试时间。
   全程 mock 不触网、不花额度。

② 检索链路延迟（v3.3 写的"GPU 900ms → 110ms"）：
   预热后连跑 N 次完整检索（改写关闭→纯召回+重排），输出 P50/P95。
   当前环境为 CPU 口径；GPU 口径需真机执行：
     EMBEDDING_DEVICE 自动探测（空闲显存>1536MB 时走 GPU）
     python scripts/repro_readme_numbers.py --retrieval-only --runs 10

用法（在 backend 目录下）：
  python scripts/repro_readme_numbers.py               # 两项都跑
  python scripts/repro_readme_numbers.py --retrieval-only   # 只跑检索延迟
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
BACKEND_DIR = Path(__file__).resolve().parent.parent


def measure_ratelimit_worst():
    """mock 全限流，测限流最坏反馈耗时"""
    import services.chat_service as cs
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda

    def always_429(_):
        raise Exception("Error code: 429")

    cs.LLM_CONFIG["fallback_model"] = ""  # 关兜底模型：最坏路径=重试1次+退避后快速失败
    cs.get_llm = lambda: RunnableLambda(always_429)
    # 真实退避保留（README 的"2.3 秒"含 1.5s 真退避；mock 全程不触网不花额度）

    prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
    t0 = time.time()
    answer = cs._call_llm_with_retry(prompt, {"question": "限流复现"})
    worst_ms = (time.time() - t0) * 1000
    assert answer.startswith("抱歉"), "限流后必须返回兜底文案"
    return worst_ms


def measure_retrieval_latency(runs: int = 10):
    """预热后连跑 N 次检索（完整三级管线），输出 P50/P95"""
    from services.chat_service import _retrieve, get_llm_rewrite  # noqa: F401
    from config.settings import RAG_CONFIG
    import os

    # 改写会调 LLM，检索延迟口径关闭改写（与 v3.3 记录的 900ms→110ms 同口径：纯召回+重排）
    os.environ["RAG_QUERY_REWRITE"] = "0"
    RAG_CONFIG["enable_query_rewrite"] = False

    _retrieve("预热：无畏契约英雄和武器介绍", [], "valorant")  # 冷启动不进统计
    qs = ["暴徒和幻影有什么区别", "怎么上分", "经济局怎么买枪", "捷风怎么玩",
          "爆头线是什么", "控图什么意思", "手枪局买什么", "烟位怎么放",
          "决斗位有哪些", "怎么练压枪"] * 3
    ms = []
    for q in qs[:runs]:
        t0 = time.time()
        _retrieve(q, [], "valorant")
        ms.append((time.time() - t0) * 1000)
    ms.sort()
    from services.device_manager import get_device
    return get_device(), ms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()

    lines = [f"# README 数字落档复现（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n"]
    if not args.retrieval_only:
        worst = measure_ratelimit_worst()
        line = f"- **限流最坏反馈耗时**（mock 全 429，重试1次+1.5s退避+快速失败）：**{worst / 1000:.2f}s**"
        print(line)
        lines.append(line + "（v3.11 README 口径「2 分钟 → 2.3 秒」的复现值）\n")

    device, ms = measure_retrieval_latency(args.runs)
    p50 = ms[len(ms) // 2]
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    line = (f"- **检索链路延迟**（{device} 口径，关闭改写，预热后 {len(ms)} 次）："
            f"P50={p50:.0f}ms / P95={p95:.0f}ms / mean={statistics.mean(ms):.0f}ms")
    print(line)
    lines.append(line)
    if "cpu" in device.lower():
        lines.append("- ⚠️ GPU 口径需在真机跑（本环境 CUDA 受限）："
                     "`python scripts/repro_readme_numbers.py --retrieval-only`（ unset EMBEDDING_DEVICE 让其自动探测 GPU）")

    rep_dir = BACKEND_DIR / "eval" / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    out = rep_dir / f"repro_readme_numbers_{time.strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已落盘: {out}")


if __name__ == "__main__":
    main()
