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
import re
import sys
import time

from langchain.prompts import ChatPromptTemplate

# ---------- 命令行定义（解析延迟到 __main__：模块可被 pytest 导入测纯函数） ----------
parser = argparse.ArgumentParser(description="RAG 效果评估")
parser.add_argument("--mode", choices=["baseline", "hybrid", "custom"], default="hybrid",
                    help="baseline=纯向量(旧v2管线) hybrid=改写+混合+重排(新v3管线)")
parser.add_argument("--skip-llm", action="store_true", help="跳过答案生成，只测检索指标")
parser.add_argument("--suite", default=None, help="自定义评测集路径（默认 backend/eval/eval_set.json）")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFUSAL_MARKERS = ["只解答", "没找到足够相关", "换个问法", "暂时不会"]  # 拒答/兜底话术特征


def _apply_mode_env(args):
    """评测环境预设——必须在导入 config.settings 之前调用（管线开关在 import 时读 env）
    v3.3：默认关质量自评 Critic（对话层增强，开了变慢且混入重试）；v3.4：温度固定 0 消除采样漂移"""
    os.environ.setdefault("RAG_CRITIC", "0")
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


def judge_refusal(answer: str, sources: list) -> bool:
    """判定答案是否为拒答/兜底：来源为空，或命中拒答话术特征"""
    return not sources or any(m in answer for m in REFUSAL_MARKERS)


# ---------- 忠实度（faithfulness）评测：简化版 RAGAS ----------
# 判定"答案的关键声明是否都能被检索来源支持"：judge 模型对照参考资料核对答案，
# 二元判定 + 列出无依据声明（允许同义转述，不允许来源里没有的事实/数字）。
# 判分失败（LLM 异常/输出不可解析）记 None，不进忠实度分母，报告单列。
_FAITHFULNESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是 RAG 答案忠实度评审员。给你用户问题、参考资料和助手答案，判断答案中的关键声明"
     "是否都能被参考资料支持（允许同义转述，不允许参考资料里没有的事实或数字）。"
     # 注意：提示词里的 JSON 花括号必须双写转义，否则被 langchain 当占位符解析
     # （v3.3 Critic 提示词、v3.16 生成链路同款坑——本处初版也踩了，判分容错拦下）
     '只输出JSON，格式：{{"faithful": true或false, "unsupported": ["参考资料无法支持的声明"]}}，'
     "完全支持时 unsupported 为空数组。不要输出任何其他内容。"),
    ("human", "用户问题：{question}\n\n参考资料：\n{context}\n\n助手答案：\n{answer}"),
])

_faithfulness_llm = None  # 【v3.15 同款】懒加载：import 本模块不建实例、不要求密钥


def parse_faithfulness(raw: str) -> dict:
    """解析 judge 输出的 JSON（容错截取第一个 {...}）；不可解析返回 faithful=None"""
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return {"faithful": None, "unsupported": []}
    try:
        data = json.loads(m.group(0))
        return {"faithful": bool(data.get("faithful")), "unsupported": data.get("unsupported") or []}
    except Exception:
        return {"faithful": None, "unsupported": []}


def judge_faithfulness(question: str, answer: str, results: list) -> dict:
    """忠实度判分：3 次尝试（退避 3s/6s 抗免费档限流），仍失败返回 faithful=None（不计入分母）"""
    global _faithfulness_llm
    if _faithfulness_llm is None:
        from services.llm_factory import make_llm
        _faithfulness_llm = make_llm(thinking=False, timeout=60, temperature=0)
    context = "\n".join(
        f"[{i + 1}]（来源：{r.source}）{r.content[:300]}" for i, r in enumerate(results[:5])
    )
    waits = (3, 6, 0)
    for attempt in range(3):
        try:
            chain = _FAITHFULNESS_PROMPT | _faithfulness_llm
            raw = chain.invoke({"question": question, "context": context, "answer": answer}).content
            parsed = parse_faithfulness(raw)
            if parsed["faithful"] is not None:
                return parsed
            print(f"  [忠实度判分第{attempt + 1}次输出不可解析] {str(raw)[:80]}")
        except Exception as e:
            print(f"  [忠实度判分第{attempt + 1}次失败] {e}")
        time.sleep(waits[attempt])
    return {"faithful": None, "unsupported": []}


def main(args):
    # 环境变量就位后才能导入项目模块（settings 与管线开关在 import 时读 env）
    _apply_mode_env(args)
    sys.path.insert(0, BACKEND_DIR)
    from config.settings import FALLBACK_ANSWER, REFUSE_ANSWER  # noqa: E402
    from services.chat_service import _retrieve, _build_rag_messages, _call_llm_with_retry, _should_fallback  # noqa: E402
    from schemas.models import ChatMessage  # noqa: E402

    suite_path = args.suite or os.path.join(BACKEND_DIR, "eval", "eval_set.json")

    # 【v3.17】预热一次检索再开始计时：首条用例的耗时会混入模型冷启动（首次加载
    # embedding/重排模型可达数十秒），污染 P50/P95；预热本身不进统计
    t_warm = time.time()
    _retrieve("预热：无畏契约英雄和武器介绍", [], "valorant")
    print(f"  [预热完成 {time.time() - t_warm:.1f}s，冷启动耗时不进延迟统计]")

    with open(suite_path, "r", encoding="utf-8") as f:
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
    observe_total = 0  # 【v3.16】观察题计数：只记录行为不计分，须从检索指标分母扣除
    faith_total = 0    # 【v3.17】忠实度：判分成功且答案非兜底的题数
    faith_ok = 0
    faith_unknown = 0  # 判分失败题数（不进分母，报告单列）
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
                # 【v3.23 修复】与真实管线同口径：先兜底判定，放行才生成
                if not results or _should_fallback(results):
                    answer = FALLBACK_ANSWER
                else:
                    prompt = _build_rag_messages([], results, q)
                    answer = _call_llm_with_retry(prompt, {"question": q})
                refused = judge_refusal(answer, results)
            refusal_ok += 1 if refused else 0
            row["refused"] = refused
            details.append(row)
            continue

        # ---- v3.4 观察题：只记录行为不计分（边界题期望行为待人工复核）----
        if case.get("observe"):
            observe_total += 1
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
            # 【v3.17】忠实度判分：只对正常回答判（兜底回答没有"依据来源"可言）
            if results:
                faith = judge_faithfulness(q, answer, results)
                row["faithful"] = faith["faithful"]
                if faith.get("unsupported"):
                    row["unsupported_claims"] = faith["unsupported"][:3]
                if faith["faithful"] is None:
                    faith_unknown += 1
                else:
                    faith_total += 1
                    faith_ok += 1 if faith["faithful"] else 0
        details.append(row)

    # ---- 汇总输出 ----
    # 【v3.16】检索指标分母只含真实检索题：拒答/官方直答/观察题均不计分，也不进分母
    # （此前只扣拒答题，观察题与官方题留在分母里稀释 Hit@5/MRR，与"不计分"口径不符）
    retrieval_cases = len(cases) - refusal_total - official_total - observe_total
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
    # 【v3.23】除零保护：官方直答集单独跑时检索题为 0，检索指标显示 N/A 而非崩溃
    if retrieval_cases:
        print(f"  检索 Hit@5      : {hit_cnt}/{retrieval_cases} = {hit_cnt/retrieval_cases:.1%}")
        print(f"  检索 MRR        : {mrr_sum/retrieval_cases:.3f}")
    else:
        print("  检索 Hit@5      : N/A（本集无普通检索题）")
        print("  检索 MRR        : N/A")
    if not args.skip_llm:
        if kw_total:
            print(f"  答案关键词覆盖  : {kw_hit}/{kw_total} = {kw_hit/kw_total:.1%}")
        else:
            print("  答案关键词覆盖  : N/A（本集无关键词覆盖题）")
        if sim_cnt:
            print(f"  标准答案相似度  : {sim_sum/sim_cnt:.3f}（{sim_cnt} 题有 reference_answer，语义余弦 1.0=一致）")
        if faith_total or faith_unknown:
            pct = f"{faith_ok/faith_total:.1%}" if faith_total else "-"
            extra = f"（判分失败 {faith_unknown} 题不计入）" if faith_unknown else ""
            print(f"  答案忠实度      : {faith_ok}/{faith_total} = {pct}{extra}")
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
            extra_counts = ""
            if official_total:
                extra_counts += f" + 官方 {official_total}"
            if observe_total:
                extra_counts += f" + 观察 {observe_total}"
            f.write(f"- 用例：{len(cases)} 条（检索 {retrieval_cases} + 拒答 {refusal_total}{extra_counts}）｜ 生成：{'关' if args.skip_llm else '开'}\n")
            if retrieval_cases:
                f.write(f"- Hit@5: {hit_cnt}/{retrieval_cases}｜MRR: {mrr_sum/retrieval_cases:.3f}\n")
            else:
                f.write("- Hit@5/MRR: N/A（本集无普通检索题，全部为直答/拒答类）\n")
            if not args.skip_llm:
                if kw_total:
                    f.write(f"- 关键词覆盖: {kw_hit}/{kw_total}\n")
                else:
                    f.write("- 关键词覆盖: N/A（本集无关键词覆盖题）\n")
                if sim_cnt:
                    f.write(f"- 标准答案相似度: {sim_sum/sim_cnt:.3f}（{sim_cnt} 题）\n")
                if faith_total or faith_unknown:
                    pct = f"{faith_ok/faith_total:.1%}" if faith_total else "-"
                    extra = f"（判分失败 {faith_unknown} 题不计入）" if faith_unknown else ""
                    f.write(f"- 答案忠实度: {faith_ok}/{faith_total} = {pct}{extra}\n")
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
            f.write("| id | 类型 | 结果 | 耗时ms | 忠实 | 问题 |\n|---|---|---|---|---|---|\n")
            for r in details:
                flag = "✓拒答" if r.get("refused") else (f"命中@{r['hit_rank']}" if r.get("hit_rank") else "✗未命中")
                if r.get("official_ok") is not None:
                    flag = "✓官方直答" if r["official_ok"] else "✗直答失败"
                elif r.get("behavior"):
                    flag = f"观察:{r['behavior']}"
                faithful = ("✓" if r["faithful"] else "✗") if r.get("faithful") is not None else ""
                suffix = f" → {r['rewritten']}" if r.get("rewritten") else ""
                f.write(f"| {r['id']} | {r['type']} | {flag} | {r.get('retrieve_ms', '-')} | {faithful} | {r['question'][:30]}{suffix} |\n")
        print(f"  报告已保存: {rep}")
    except Exception as e:
        print(f"  报告落盘失败(不影响评测): {e}")


if __name__ == "__main__":
    _args = parser.parse_args()
    main(_args)
