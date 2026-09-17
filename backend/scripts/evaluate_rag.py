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
    refusal_total = 0
    refusal_ok = 0
    details = []

    for case in cases:
        q = case["question"]
        expect_refusal = case.get("expect_refusal", False)
        row = {"id": case["id"], "type": case["type"], "question": q}

        # ---- 检索（不写会话历史，独立评估）----
        t0 = time.time()
        _, results = _retrieve(q, [], case.get("kb_id", "valorant"))
        retrieve_ms = (time.time() - t0) * 1000
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

        # ---- 检索指标：Hit@5 / MRR ----
        expected = case["expected_docs"]
        rank = next((i + 1 for i, s in enumerate(source_names) if s in expected), None)
        if rank:
            hit_cnt += 1
            mrr_sum += 1.0 / rank
        row["hit_rank"] = rank

        # ---- 生成指标：关键词覆盖 ----
        if not args.skip_llm:
            if results:
                prompt = _build_rag_messages([], results, q)
                answer = _call_llm_with_retry(prompt, {"question": q})
            else:
                answer = FALLBACK_ANSWER
            row["answer_preview"] = answer[:80].replace("\n", " ")
            kws = case["expected_keywords"]
            kw_total += len(kws)
            missing = [k for k in kws if k not in answer]
            kw_hit += len(kws) - len(missing)
            row["missing_keywords"] = missing
        details.append(row)

    # ---- 汇总输出 ----
    retrieval_cases = len(cases) - refusal_total
    print(f"\n{'-'*62}\n逐条明细")
    for row in details:
        flag = "✓拒答" if row.get("refused") else (f"命中@{row['hit_rank']}" if row.get("hit_rank") else "✗未命中")
        extra = f"  缺关键词:{row['missing_keywords']}" if row.get("missing_keywords") else ""
        print(f"  [{row['id']}] {flag} ({row['retrieve_ms']}ms) {row['question'][:26]}{extra}")

    print(f"\n{'-'*62}\n汇总（模式={args.mode}）")
    print(f"  检索 Hit@5      : {hit_cnt}/{retrieval_cases} = {hit_cnt/retrieval_cases:.1%}")
    print(f"  检索 MRR        : {mrr_sum/retrieval_cases:.3f}")
    if not args.skip_llm:
        print(f"  答案关键词覆盖  : {kw_hit}/{kw_total} = {kw_hit/kw_total:.1%}")
    if refusal_total:
        print(f"  拒答正确率      : {refusal_ok}/{refusal_total} = {refusal_ok/refusal_total:.0%}")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
