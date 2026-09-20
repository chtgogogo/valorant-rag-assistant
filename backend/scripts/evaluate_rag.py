# 【新增 v3.0】RAG 效果评估脚本
# ------------------------------------------------------------
# 用法（在 backend 目录下运行）：
#   python scripts/evaluate_rag.py --mode baseline   # 旧管线：纯向量检索
#   python scripts/evaluate_rag.py --mode hybrid     # 新管线：改写+混合+重排
#   python scripts/evaluate_rag.py --mode hybrid --skip-llm   # 只测检索，不调大模型
#
# 指标说明（拒答题只算拒答正确率，不参与检索指标）：
#   Hit@5      期望文档出现在 top5 来源中的比例（检索准不准）
#   MRR        期望文档首次命中排名倒数的均值（排得靠不靠前）
#   关键词覆盖 答案包含期望关键词的比例（答得全不全）
#   拒答正确率 无关问题被正确拒绝/兜底的比例
# ------------------------------------------------------------
import argparse
import json
import os
import sys
import time

# ---------- 1. 先解析参数并设置环境变量（必须在 import config 之前） ----------
parser = argparse.ArgumentParser(description="RAG 效果评估")
parser.add_argument("--mode", choices=["baseline", "hybrid", "custom"], default="hybrid",
                    help="baseline=纯向量(旧v2管线) hybrid=改写+混合+重排(新v3管线)")
parser.add_argument("--skip-llm", action="store_true", help="跳过答案生成，只测检索指标")
parser.add_argument("--suite", default=None, help="自定义评测集路径（默认 backend/eval/eval_set.json）")
args = parser.parse_args()

# v3.3：评测默认关质量自评 Critic（它是对话层增强，开了会让评测变慢且混入重试；需要时可显式 RAG_CRITIC=1）
os.environ.setdefault("RAG_CRITIC", "0")
# v3.4：评测固定温度 0——生成指标不再随采样漂移（v3.3 实测同口径两次差 9.4 个百分点）
os.environ.setdefault("LLM_TEMPERATURE", "0")

if args.mode == "baseline":
    os.environ["RAG_HYBRID"] = "0"
    os.environ["RAG_RERANK"] = "0"
    os.environ["RAG_QUERY_REWRITE"] = "0"
elif args.mode == "hybrid":
    os.environ["RAG_HYBRID"] = "1"
    os.environ["RAG_RERANK"] = "1"
    os.environ["RAG_QUERY_REWRITE"] = "1"
# custom：不覆盖任何环境变量，管线开关由外部传入（消融实验用）

# ---------- 2. 导入项目模块 ----------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import RAG_CONFIG, FALLBACK_ANSWER, REFUSE_ANSWER  # noqa: E402
from services.chat_service import _retrieve, _build_rag_messages, _call_llm_with_retry, _should_fallback  # noqa: E402
from schemas.models import ChatMessage  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE_PATH = args.suite or os.path.join(BACKEND_DIR, "eval", "eval_set.json")

REFUSAL_MARKERS = ["只解答", "没找到足够相关", "换个问法", "暂时不会"]  # 拒答/兜底话术特征


def judge_refusal(answer: str, sources: list) -> bool:
    """判定答案是否为拒答/兜底：来源为空，或命中拒答话术特征"""
    return not sources or any(m in answer for m in REFUSAL_MARKERS)


def main():
    with open(SUITE_PATH, "r", encoding="utf-8") as f:
        suite = json.load(f)
    cases = suite["cases"]
    print(f"\n{'='*62}\nRAG 评估  模式={args.mode}  用例={len(cases)} 条  "
          f"生成={'关闭' if args.skip_llm else '开启'}\n{'='*62}")

    hit_cnt = 0      # Hit@5 命中数
    mrr_sum = 0.0    # MRR 累计
    kw_total = 0     # 期望关键词总数
    kw_hit = 0       # 命中的关键词数
    sim_sum = 0.0    # 标准答案语义相似度累计（v3.3）
    sim_cnt = 0      # 有标准答案的题数
    official_total = 0  # 官方直答题数（v3.4）
    official_ok = 0
    ms_list = []     # 检索耗时样本（P50/P95）
    type_stats = {}  # 分类型检索统计 {type: {"n":x,"hit":y}}
    refusal_total = 0
    refusal_ok = 0
    details = []

    for case in cases:
        q = case["question"]
        expect_refusal = case.get("expect_refusal", False)
        row = {"id": case["id"], "type": case["type"], "question": q}

        # ---- v3.4 官方数据直答路径：英雄列表/武器价格等确定性回答，不经检索与 LLM ----
        if case.get("path") == "official":
            from services.official_data_service import answer_official_data_query
            ans = answer_official_data_query(case["question"]) or ""
            kws = case.get("expected_keywords", [])
            ok = all(k in ans for k in kws) if kws else bool(ans)
            official_total += 1
            official_ok += 1 if ok else 0
            row["official_ok"] = ok
            row["answer_preview"] = ans[:70]
            details.append(row)
            continue

        # ---- 检索（独立评估；v3.4 支持多轮题的 history 字段）----
        t0 = time.time()
        history = [ChatMessage(**m) for m in case.get("history", [])]
        rewritten_q, results = _retrieve(q, history, case.get("kb_id", "valorant"))
        retrieve_ms = (time.time() - t0) * 1000
        ms_list.append(retrieve_ms)
        if history:
            row["rewritten"] = rewritten_q  # 多轮题记录改写结果，验证指代消解是否生效
        source_names = [r.source for r in results]
        row["retrieve_ms"] = round(retrieve_ms)
        row["top_sources"] = [f"{s}@{round(results[i].score, 3)}"
                              for i, s in enumerate(source_names[:3])]

        if expect_refusal:
            refusal_total += 1
            if args.skip_llm:
                # 只测检索：与真实管线同一套兜底判定（来源为空或置信分低于阈值）
                refused = not results or _should_fallback(results)
            else:
                prompt = _build_rag_messages([], results, q) if results else None
                answer = _call_llm_with_retry(prompt, {"question": q}) if prompt else FALLBACK_ANSWER
                refused = judge_refusal(answer, results)
            refusal_ok += 1 if refused else 0
            row["refused"] = refused
            details.append(row)
            continue

        # ---- v3.4 观察题：只记录行为不计分（边界题期望行为待人工复核）----
        if case.get("observe"):
            if args.skip_llm:
                refused = not results or _should_fallback(results)
                row["behavior"] = "拒答" if refused else "正常回答"
            else:
                prompt = _build_rag_messages([], results, q) if results else None
                answer = _call_llm_with_retry(prompt, {"question": q}) if prompt else FALLBACK_ANSWER
                row["answer_preview"] = answer[:70]
                row["behavior"] = "拒答" if judge_refusal(answer, results) else "正常回答"
            details.append(row)
            continue

        # ---- 检索指标：Hit@5 / MRR ----
        expected = case["expected_docs"]
        rank = next((i + 1 for i, s in enumerate(source_names) if s in expected), None)
        if rank:
            hit_cnt += 1
            mrr_sum += 1.0 / rank
        row["hit_rank"] = rank
        tstat = type_stats.setdefault(case["type"], {"n": 0, "hit": 0})
        tstat["n"] += 1
        tstat["hit"] += 1 if rank else 0

        # ---- 生成指标：关键词覆盖 ----
        if not args.skip_llm:
            if results:
                prompt = _build_rag_messages([], results, q)
                answer = _call_llm_with_retry(prompt, {"question": q})
            else:
                answer = FALLBACK_ANSWER
            row["answer_preview"] = answer[:80].replace("\n", " ")
            kws = case.get("expected_keywords", [])
            kw_total += len(kws)
            missing = [k for k in kws if k not in answer]
            kw_hit += len(kws) - len(missing)
            row["missing_keywords"] = missing
            # v3.3 标准答案语义相似度：答案 vs 参考答案的 embedding 余弦（1.0=语义一致）
            ref = case.get("reference_answer")
            if ref:
                from services.vector_service import _get_embedding_model
                emb = _get_embedding_model().encode([answer, ref], normalize_embeddings=True)
                sim = float(emb[0] @ emb[1])
                sim_sum += sim
                sim_cnt += 1
                row["ref_similarity"] = round(sim, 3)
        details.append(row)

    # ---- 汇总输出 ----
    retrieval_cases = len(cases) - refusal_total
    print(f"\n{'-'*62}\n逐条明细")
    for row in details:
        flag = "✓拒答" if row.get("refused") else (f"命中@{row['hit_rank']}" if row.get("hit_rank") else "✗未命中")
        if row.get("official_ok") is not None:
            flag = "✓官方直答" if row["official_ok"] else "✗直答失败"
        elif row.get("behavior"):
            flag = f"观察:{row['behavior']}"
        extra = f"  缺关键词:{row['missing_keywords']}" if row.get("missing_keywords") else ""
        rewritten = f"  改写→{row['rewritten']}" if row.get("rewritten") else ""
        ms = row.get("retrieve_ms", "-")
        print(f"  [{row['id']}] {flag} ({ms}ms) {row['question'][:26]}{extra}{rewritten}")

    print(f"\n{'-'*62}\n汇总（模式={args.mode}）")
    print(f"  检索 Hit@5      : {hit_cnt}/{retrieval_cases} = {hit_cnt/retrieval_cases:.1%}")
    print(f"  检索 MRR        : {mrr_sum/retrieval_cases:.3f}")
    if not args.skip_llm:
        print(f"  答案关键词覆盖  : {kw_hit}/{kw_total} = {kw_hit/kw_total:.1%}")
        if sim_cnt:
            print(f"  标准答案相似度  : {sim_sum/sim_cnt:.3f}（{sim_cnt} 题有 reference_answer，语义余弦 1.0=一致）")
    if refusal_total:
        print(f"  拒答正确率      : {refusal_ok}/{refusal_total} = {refusal_ok/refusal_total:.0%}")
    # ---- v3.4：官方直答 / 分类型 / 延迟 / 设备口径 ----
    if official_total:
        print(f"  官方直答正确率  : {official_ok}/{official_total}")
    if type_stats:
        print("  分类型 Hit@5    :")
        for tname, st in sorted(type_stats.items(), key=lambda x: -x[1]["n"]):
            print(f"    {tname:<10}: {st['hit']}/{st['n']}")
    if ms_list:
        ms_sorted = sorted(ms_list)
        p50 = ms_sorted[len(ms_sorted) // 2]
        p95 = ms_sorted[min(len(ms_sorted) - 1, int(len(ms_sorted) * 0.95))]
        print(f"  检索延迟        : P50 {p50:.0f}ms / P95 {p95:.0f}ms")
    try:
        from services.device_manager import get_device
        print(f"  设备口径        : {get_device()}")
    except Exception:
        pass
    print(f"{'='*62}\n")

    # v3.3：评测报告落盘 backend/eval/reports/，方便对比历次结果
    try:
        import datetime
        rep_dir = os.path.join(BACKEND_DIR, "eval", "reports")
        os.makedirs(rep_dir, exist_ok=True)
        rep = os.path.join(rep_dir, f"report_{args.mode}_{datetime.datetime.now():%Y%m%d_%H%M%S}.md")
        with open(rep, "w", encoding="utf-8") as f:
            f.write(f"# 评测报告 {args.mode}（{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
            f.write(f"- 用例：{len(cases)} 条（检索 {retrieval_cases} + 拒答 {refusal_total}）｜ 生成：{'关' if args.skip_llm else '开'}\n")
            f.write(f"- Hit@5: {hit_cnt}/{retrieval_cases}｜MRR: {mrr_sum/retrieval_cases:.3f}\n")
            if not args.skip_llm:
                f.write(f"- 关键词覆盖: {kw_hit}/{kw_total}\n")
                if sim_cnt:
                    f.write(f"- 标准答案相似度: {sim_sum/sim_cnt:.3f}（{sim_cnt} 题）\n")
            if refusal_total:
                f.write(f"- 拒答正确率: {refusal_ok}/{refusal_total}\n")
            if official_total:
                f.write(f"- 官方直答正确率: {official_ok}/{official_total}\n")
            if type_stats:
                f.write("\n### 分类型 Hit@5\n\n| 类型 | 命中 | 题数 |\n|---|---|---|\n")
                for tname, st in sorted(type_stats.items(), key=lambda x: -x[1]["n"]):
                    f.write(f"| {tname} | {st['hit']} | {st['n']} |\n")
            if ms_list:
                ms_sorted = sorted(ms_list)
                f.write(f"\n- 检索延迟: P50 {ms_sorted[len(ms_sorted)//2]:.0f}ms / P95 {ms_sorted[min(len(ms_sorted)-1, int(len(ms_sorted)*0.95))]:.0f}ms\n")
            f.write("| id | 类型 | 结果 | 耗时ms | 问题 |\n|---|---|---|---|---|\n")
            for r in details:
                flag = "✓拒答" if r.get("refused") else (f"命中@{r['hit_rank']}" if r.get("hit_rank") else "✗未命中")
                if r.get("official_ok") is not None:
                    flag = "✓官方直答" if r["official_ok"] else "✗直答失败"
                elif r.get("behavior"):
                    flag = f"观察:{r['behavior']}"
                suffix = f" → {r['rewritten']}" if r.get("rewritten") else ""
                f.write(f"| {r['id']} | {r['type']} | {flag} | {r.get('retrieve_ms', '-')} | {r['question'][:30]}{suffix} |\n")
        print(f"  报告已保存: {rep}")
    except Exception as e:
        print(f"  报告落盘失败(不影响评测): {e}")


if __name__ == "__main__":
    main()
