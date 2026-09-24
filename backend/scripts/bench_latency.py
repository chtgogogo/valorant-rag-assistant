# -*- coding: utf-8 -*-
"""
延迟基准（v3.19）：主模型通道 × 语义缓存 miss/hit 同卷对比

做什么：
  1. 预热检索链路（加载 embedding / BM25 / 重排模型）
  2. 第一轮：8 个问题逐个提问（缓存 miss，走完整管线）
  3. 第二轮：同样的 8 个问题再问一遍（应命中语义缓存）
  4. 输出逐题延迟 + P50/P95 汇总 + 缓存统计，报告落盘 backend/eval/reports/

模型对比方法（环境变量优先于 .env，load_dotenv 不覆盖已有环境变量）：
  python scripts/bench_latency.py --label paid --base-url http://127.0.0.1:8011
  set ZHIPU_MODEL=glm-4.7-flash && python scripts/bench_latency.py --label free --base-url http://127.0.0.1:8011
推荐 --base-url 连真实 uvicorn 进程（TestClient 的 anyio 代理线程里初始化 torch CUDA
在 Windows 上会随机段错误，实测 exit 139——真实服务进程无此问题）。
"""
import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUESTIONS = [
    "新手应该先练什么",
    "暴徒和幻影有什么区别",
    "经济局该怎么买枪",
    "捷风应该怎么玩",
    "排位机制是怎么算分的",
    "什么是爆头线",
    "控图是什么意思",
    "手枪局买什么比较好",
]

# 【v3.22】TTFB 轮专用变体问题：与 miss 轮同义但措辞不同——若复用原题会命中
# 前两轮写入的语义缓存，测出的将是缓存命中延迟而非真实生成首字延迟；
# 语义相近的变体仍可能个别命中缓存（阈值 0.92），报告口径已注明
TTFB_QUESTIONS = [
    "刚开始玩应该先练哪个英雄",
    "暴徒跟幻影的差别是什么",
    "没钱的时候应该怎么买装备",
    "捷风的玩法思路是什么",
    "排位分数是怎么计算的",
    "爆头线指的是什么",
    "怎么控制地图啊",
    "手枪局应该买什么",
]


def _pct(sorted_ms: list, p: float) -> float:
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, max(0, int(round(p * (len(sorted_ms) - 1)))))
    return sorted_ms[idx]


def run_round(do_post, tag: str) -> list:
    rows = []
    for q in QUESTIONS:
        payload = {"session_id": f"bench-{uuid.uuid4().hex[:8]}", "question": q, "kb_id": "valorant"}
        t0 = time.time()
        resp = do_post(payload)
        ms = (time.time() - t0) * 1000
        data = (resp.json() or {}).get("data") or {}
        answer = data.get("answer") or ""
        rows.append({"tag": tag, "q": q, "ms": round(ms, 1),
                     "sources": len(data.get("sources") or []),
                     "answer_head": answer[:24].replace("\n", " ")})
        print(f"  [{tag}] {ms:8.1f}ms  sources={rows[-1]['sources']}  {q}")
    return rows


def measure_ttfb(do_stream, tag: str = "ttfb") -> list:
    """【v3.22】首字延迟（TTFB）：POST /api/chat/stream 到收到第一个 token 事件的毫秒数"""
    rows = []
    for q in TTFB_QUESTIONS:
        payload = {"session_id": f"bench-ttfb-{uuid.uuid4().hex[:8]}", "question": q, "kb_id": "valorant"}
        t0 = time.time()
        ttfb_ms = None
        try:
            for line in do_stream(payload):
                if line.startswith("event: token"):
                    ttfb_ms = round((time.time() - t0) * 1000, 1)
                    break
        except Exception as e:
            print(f"  [{tag}] {q} 流式失败: {e}")
        rows.append({"q": q, "ttfb_ms": ttfb_ms})
        print(f"  [{tag}] {ttfb_ms if ttfb_ms is not None else 'FAIL':>8}ms  TTFB  {q}")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run", help="本次运行标签（写入报告文件名，如 paid/free）")
    parser.add_argument("--base-url", default=None,
                        help="连接已启动的服务（推荐，如 http://127.0.0.1:8011）；不传则用进程内 TestClient")
    args = parser.parse_args()

    from config.settings import LLM_CONFIG, CACHE_CONFIG
    from services.semantic_cache import get_semantic_cache

    if args.base_url:
        import httpx
        http = httpx.Client(base_url=args.base_url, timeout=180)
        do_get = lambda path, **kw: http.get(path, **kw)   # noqa: E731
        do_post = lambda path, **kw: http.post(path, **kw)  # noqa: E731

        def do_stream(payload):
            with http.stream("POST", "/api/chat/stream", json=payload) as resp:
                for line in resp.iter_lines():
                    yield line
    else:
        from fastapi.testclient import TestClient
        from main import app
        tc = TestClient(app)
        do_get = tc.get
        do_post = lambda path, **kw: tc.post(path, **kw)  # noqa: E731

        def do_stream(payload):
            with tc.stream("POST", "/api/chat/stream", json=payload) as resp:
                for line in resp.iter_lines():
                    yield line

    cache = get_semantic_cache()

    print(f"[基准] 模型={LLM_CONFIG['model_name']} 兜底={LLM_CONFIG.get('fallback_model') or '无'} "
          f"改写思考={'开' if LLM_CONFIG.get('thinking_rewrite') else '关'} "
          f"语义缓存={'开' if CACHE_CONFIG['enabled'] else '关'} 阈值={CACHE_CONFIG['threshold']}")

    t0 = time.time()
    do_get("/api/chat/warmup", params={"kb_id": "valorant"})
    warm_ms = (time.time() - t0) * 1000
    print(f"[基准] 检索链路预热完成 {warm_ms:.0f}ms，开始第一轮（缓存 miss）...")

    miss_rows = run_round(lambda payload: do_post("/api/chat/send", json=payload), "miss")
    print("[基准] 第一轮完成，开始第二轮（应命中缓存）...")
    hit_rows = run_round(lambda payload: do_post("/api/chat/send", json=payload), "hit")

    print("[基准] 开始首字延迟测量（SSE 流式，独立会话）...")
    ttfb_rows = measure_ttfb(do_stream)
    ttfb_vals = sorted(r["ttfb_ms"] for r in ttfb_rows if r["ttfb_ms"] is not None)
    ttfb_sum = {"p50": _pct(ttfb_vals, 0.50), "p95": _pct(ttfb_vals, 0.95),
                "n": len(ttfb_vals)} if ttfb_vals else {"p50": 0, "p95": 0, "n": 0}

    def summarize(rows):
        ms = sorted(r["ms"] for r in rows)
        return {"p50": _pct(ms, 0.50), "p95": _pct(ms, 0.95),
                "mean": round(statistics.mean(ms), 1), "min": ms[0], "max": ms[-1]}

    miss_sum, hit_sum = summarize(miss_rows), summarize(hit_rows)
    # 第二轮相对第一轮的答案一致性（缓存命中意味着答案逐字相同、延迟骤降）
    same = sum(1 for a, b in zip(miss_rows, hit_rows) if a["answer_head"] == b["answer_head"])
    fast = sum(1 for a, b in zip(miss_rows, hit_rows)
               if a["answer_head"] == b["answer_head"] and a["ms"] > 0 and b["ms"] < a["ms"] / 3)
    if args.base_url:
        # --base-url 模式：缓存在服务进程内，本进程实例统计无意义；命中以"答案一致且延迟降至 1/3 以下"推断
        cache_stats = {"note": "base-url 模式，精确统计在服务端（审计 +cache_hit / 服务日志）",
                       "inferred_hits": fast}
    else:
        cache_stats = cache.stats()

    print("\n===== 汇总 =====")
    print(f"miss: P50={miss_sum['p50']}ms P95={miss_sum['p95']}ms mean={miss_sum['mean']}ms")
    print(f"hit : P50={hit_sum['p50']}ms P95={hit_sum['p95']}ms mean={hit_sum['mean']}ms")
    print(f"TTFB: P50={ttfb_sum['p50']}ms P95={ttfb_sum['p95']}ms (n={ttfb_sum['n']})")
    print(f"缓存: {json.dumps(cache_stats, ensure_ascii=False)}  答案一致轮数: {same}/{len(QUESTIONS)}")

    reports = Path(__file__).resolve().parent.parent / "eval" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report = reports / f"bench_{args.label}_{ts}.md"
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"# 延迟基准 · {args.label}（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n\n")
        f.write(f"- 主模型：`{LLM_CONFIG['model_name']}`；限流兜底：`{LLM_CONFIG.get('fallback_model') or '无'}`\n")
        f.write(f"- 查询改写思考：{'开' if LLM_CONFIG.get('thinking_rewrite') else '关'}；语义缓存："
                f"{'开' if CACHE_CONFIG['enabled'] else '关'}（阈值 {CACHE_CONFIG['threshold']}）\n")
        f.write(f"- 检索链路预热：{warm_ms:.0f}ms（不进统计）\n\n")
        f.write("| 问题 | miss(ms) | hit(ms) | TTFB(ms) | miss来源数 | 两轮答案一致 |\n|---|---|---|---|---|---|\n")
        for a, b, t in zip(miss_rows, hit_rows, ttfb_rows):
            f.write(f"| {a['q']} | {a['ms']} | {b['ms']} | {t['ttfb_ms'] if t['ttfb_ms'] is not None else '-'} "
                    f"| {a['sources']} | {'✓' if a['answer_head'] == b['answer_head'] else '✗'} |\n")
        f.write(f"\n- miss 汇总：P50={miss_sum['p50']}ms / P95={miss_sum['p95']}ms / mean={miss_sum['mean']}ms\n")
        f.write(f"- hit  汇总：P50={hit_sum['p50']}ms / P95={hit_sum['p95']}ms / mean={hit_sum['mean']}ms\n")
        f.write(f"- TTFB 汇总（【v3.22】首字延迟，SSE 首个 token 事件）：P50={ttfb_sum['p50']}ms / "
                f"P95={ttfb_sum['p95']}ms（成功 {ttfb_sum['n']}/{len(QUESTIONS)}）\n")
        f.write(f"- 缓存统计：{json.dumps(cache_stats, ensure_ascii=False)}（答案一致 {same}/{len(QUESTIONS)}）\n")
        f.write(f"- 口径说明：/api/chat/send 非流式端到端（含检索+生成全程）；每题独立会话；"
                f"miss=首轮冷问题，hit=同题复问（语义缓存命中）；TTFB=POST /api/chat/stream 到首个 "
                f"token 事件（含检索+首 token 生成，独立会话，用同义变体问题避开前两轮缓存，"
                f"个别仍可能命中）。单次运行样本量 8，仅供量级参考。\n")
    print(f"[基准] 报告已落盘: {report}")


if __name__ == "__main__":
    main()
