# -*- coding: utf-8 -*-
"""
并发压测（v3.25）：20 并发打问答/检索接口，输出 P50 / P95 / 错误率，落盘 eval/reports/。

成本控制（不烧 LLM 额度）：
  - 口径A（默认）：/api/chat/send + 官方直答类问题（确定性回答，零 LLM 调用），
    覆盖完整 FastAPI 链路（历史锁/审计/缓存/前置防护）；
  - 口径B：/api/vector/search（真实 embedding 推理 + Chroma 检索，零 LLM 调用）。
  两种口径都不触大模型生成；"带生成的真实并发"会烧额度且限流本身是变量，
  不在本脚本默认范围（可用 --question 换普通问题自担费用）。

用法（先启动服务）：
  python scripts/stress_test.py --url http://127.0.0.1:8001 --concurrency 20 --total 100
建议先 --concurrency 4 --total 10 小流量试跑确认脚本行为，再上目标并发。
"""
import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent

# 官方直答类问题（确定性回答路径，零 LLM）；每个线程轮换取用模拟问题多样性
OFFICIAL_QUESTIONS = [
    "游戏里现在一共有多少个英雄？",
    "游戏里有哪些地图？",
    "狂徒多少钱？",
    "大狙价格是多少？",
    "标配手枪要钱吗？",
]
RETRIEVAL_QUESTIONS = [
    "暴徒和幻影有什么区别",
    "怎么上分",
    "经济局怎么买枪",
    "爆头线是什么",
    "控图什么意思",
]


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def run_load(url: str, mode: str, concurrency: int, total: int, timeout: float):
    path = "/api/vector/search" if mode == "retrieval" else "/api/chat/send"
    questions = RETRIEVAL_QUESTIONS if mode == "retrieval" else OFFICIAL_QUESTIONS
    latencies, errors = [], []
    lock = threading.Lock()
    counter = {"i": 0}

    def one(client: httpx.Client):
        with lock:
            i = counter["i"]
            counter["i"] += 1
        q = questions[i % len(questions)]
        session_id = f"stress-{mode}-{threading.get_ident()}-{i}"
        t0 = time.time()
        try:
            if mode == "retrieval":
                resp = client.post(path, params={"question": q, "kb_id": "valorant", "top_k": 5})
            else:
                resp = client.post(path, json={"session_id": session_id, "question": q,
                                               "kb_id": "valorant"})
            ms = (time.time() - t0) * 1000
            ok = resp.status_code == 200 and (resp.json().get("code", 200) in (200, None))
            with lock:
                (latencies if ok else errors).append(ms if ok else f"HTTP{resp.status_code}:{ms:.0f}ms")
        except Exception as e:
            ms = (time.time() - t0) * 1000
            with lock:
                errors.append(f"{type(e).__name__}:{ms:.0f}ms")

    with httpx.Client(base_url=url, timeout=timeout) as client:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(lambda _: one(client), range(total)))
        wall = time.time() - t0

    lat_sorted = sorted(latencies)
    return {
        "mode": mode, "path": path, "concurrency": concurrency, "total": total,
        "success": len(latencies), "errors": errors,
        "error_rate": round(len(errors) / total, 4) if total else 0.0,
        "p50": round(_pct(lat_sorted, 0.50), 1),
        "p95": round(_pct(lat_sorted, 0.95), 1),
        "mean": round(statistics.mean(latencies), 1) if latencies else 0,
        "max": round(lat_sorted[-1], 1) if lat_sorted else 0,
        "throughput_rps": round(total / wall, 1) if wall else 0,
        "wall_seconds": round(wall, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--mode", choices=["official", "retrieval", "both"], default="both")
    parser.add_argument("--question", default=None, help="自定义问题（注意：非官方直答问题会调 LLM 产生费用）")
    args = parser.parse_args()

    global OFFICIAL_QUESTIONS, RETRIEVAL_QUESTIONS
    if args.question:
        OFFICIAL_QUESTIONS = RETRIEVAL_QUESTIONS = [args.question]

    results = []
    modes = ["official", "retrieval"] if args.mode == "both" else [args.mode]
    print(f"[压测] 目标={args.url} 并发={args.concurrency} 总请求={args.total} 模式={modes}")
    for mode in modes:
        print(f"\n--- 预热（{mode}，2 次）---")
        run_load(args.url, mode, 1, 2, args.timeout)  # 小流量预热（模型加载不进统计）
        print(f"--- 正式压测（{mode}）---")
        r = run_load(args.url, mode, args.concurrency, args.total, args.timeout)
        results.append(r)
        print(f"  成功 {r['success']}/{r['total']}  错误率 {r['error_rate'] * 100:.1f}%")
        print(f"  P50={r['p50']}ms  P95={r['p95']}ms  mean={r['mean']}ms  max={r['max']}ms")
        print(f"  吞吐 {r['throughput_rps']} req/s（墙钟 {r['wall_seconds']}s）")
        if r["errors"]:
            print(f"  错误样例: {r['errors'][:3]}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = BACKEND_DIR / "eval" / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    out = rep_dir / f"stress_test_{ts}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 并发压测报告（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n\n")
        f.write(f"- 目标：{args.url}｜并发 {args.concurrency}｜每口径 {args.total} 请求\n")
        f.write(f"- 成本口径：官方直答（零 LLM）/ 纯检索（零 LLM），均不触大模型生成\n\n")
        f.write("| 口径 | 接口 | 成功/总数 | 错误率 | P50(ms) | P95(ms) | mean | 吞吐(req/s) |\n|---|---|---|---|---|---|---|---|\n")
        for r in results:
            f.write(f"| {r['mode']} | `{r['path']}` | {r['success']}/{r['total']} "
                    f"| {r['error_rate'] * 100:.1f}% | {r['p50']} | {r['p95']} | {r['mean']} "
                    f"| {r['throughput_rps']} |\n")
        f.write("\n- 口径说明：单进程 uvicorn（FastAPI 同步 def 走线程池）；"
                "带生成的真实并发压测会烧 LLM 额度且受上游限流扰动，未纳入默认口径。\n")
    print(f"\n[压测] 报告已落盘: {out}")


if __name__ == "__main__":
    main()
