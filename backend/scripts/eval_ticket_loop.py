# -*- coding: utf-8 -*-
"""
工单闭环评测脚本（v3.5）

验证业务闭环的核心承诺：答不上的问题 → 人工答案回流 → 同类问题可答。
三阶段全流程（不调用 LLM，只走 检索+重排，避免依赖外部 API）：
  Phase 1 基线：对"知识库故意未覆盖"的个案问题跑检索，确认全部低于兜底阈值
                （即真实业务里会走 fallback+自动建工单），并创建工单
  Phase 2 人工处理：模拟客服填标准答案 → resolve 并回流知识库
  Phase 3 复测：同题 + 换问法改写题重跑检索，统计二次命中率与分数提升

用法：
    cd backend
    py scripts/eval_ticket_loop.py            # 全流程（含回流）
    py scripts/eval_ticket_loop.py --clean    # 先清掉本脚本创建的工单与回流文档再跑
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import RAG_CONFIG
from services.hybrid_retriever import hybrid_search
from services.reranker import rerank
from services import ticket_service

KB_ID = "law"
MARK = "tkfeval"  # 评测标记：session_id 前缀，--clean 按此清理

# 个案问题（法律场景）：知识库只覆盖"通用条文要点"，这些具体个案场景故意未覆盖
# → 基线应全部兜底；人工答案回流后同题复问可命中
CASES = [
    {
        "q": "小区电梯里的广告收益归谁，物业说归他们所有",
        "answer": "根据《民法典》第282条，建设单位、物业服务企业或者其他管理人等利用业主的共有部分产生的收入（如电梯广告费），在扣除合理成本之后，属于业主共有。物业无权独占。业主可要求物业公示收支明细，协商不成可向住建部门投诉或起诉。"
                  "您的电梯广告收益纠纷发生在共有部分，属于业主共有收益范畴，可先要求物业公开合同与账目，再由业主大会决定使用方式。",
        "paraphrase": "电梯广告费的钱应该归业主还是物业",
    },
    {
        "q": "公司让我签自愿放弃社保的承诺书，我签了还有效吗",
        "answer": "无效。缴纳社会保险是用人单位和劳动者的法定强制义务，不因双方约定或承诺而免除（社会保险法第58条）。《劳动合同法》第26条规定，免除自己的法定责任、排除劳动者权利的条款无效。"
                  "你签署的放弃社保承诺书不产生法律效力，你仍然可以：①要求公司补缴社保；②以未依法缴纳社保为由解除劳动合同并要求经济补偿（每工作一年补偿一个月工资）。",
        "paraphrase": "签了放弃社保协议还能要求公司补缴吗",
    },
    {
        "q": "网店先涨价再打七折的秒杀价算不算价格欺诈",
        "answer": "属于价格欺诈的典型情形。《消费者权益保护法》第55条规定，经营者有欺诈行为的，消费者可要求退一赔三，不足500元按500元计。先提价再虚构折扣（虚构原价）是市场监管部门明令禁止的价格欺诈行为。"
                  "请保存商品页面改价前后的截图、订单记录作为证据，先与商家协商，协商不成可向12315投诉举报，主张三倍赔偿。",
        "paraphrase": "先提价再打折可以要求三倍赔偿吗",
    },
    {
        "q": "邻居装修把我家墙砸坏了，他只肯修不肯赔我的损失",
        "answer": "邻居装修损坏你家墙体构成财产侵权，依据《民法典》第1165条（过错侵权责任）和第1184条（财产损失按市场价或其他合理方式计算），你除了有权要求恢复原状（修复墙体），还有权主张修复期间的合理损失（如另行租房费用）。"
                  "建议先拍照固定损坏现状、请物业或第三方出具损坏证明，协商不成可向法院起诉，同时主张修复费用与相关损失。注意三年诉讼时效。",
        "paraphrase": "邻居装修弄坏我家墙面除了修复还能索赔吗",
    },
    {
        "q": "微信借钱给朋友没打借条，只有转账记录能起诉吗",
        "answer": "可以起诉。《民事诉讼法》规定的证据包括电子数据，微信转账记录、聊天记录均可作为证据。仅有转账记录虽然能证明资金往来，但最好再补充能证明借贷合意的材料（如聊天中提到“借”“还”的记录、通话录音）。"
                  "起诉流程：向被告住所地法院提交起诉状与证据，标的额较小的可适用小额诉讼程序（一审终审）。注意三年诉讼时效，起诉前可先发催款消息引起时效中断。",
        "paraphrase": "没有借条只有微信转账记录法院会受理吗",
    },
]


def _top1(question: str):
    """跑 混合检索+重排，返回 (top1分数, 结果条数)"""
    candidates = hybrid_search(question, KB_ID)
    results = rerank(question, candidates)
    return (results[0].score if results else 0.0), len(results)


def _threshold() -> float:
    return RAG_CONFIG["rerank_score_threshold"] if RAG_CONFIG.get("enable_rerank", True) \
        else RAG_CONFIG["score_threshold"]


def phase_clean():
    """清理本脚本历史产生的工单与回流文档"""
    import json
    from services.document_service import _load_doc_list, _save_doc_list
    from services.vector_service import delete_doc_vectors

    cleaned = 0
    for t in ticket_service.list_tickets(limit=10000):
        if (t.get("session_id") or "").startswith(MARK):
            if t.get("doc_id"):
                try:
                    delete_doc_vectors(t["doc_id"], t["kb_id"] or KB_ID)
                except Exception:
                    pass
            with ticket_service._lock:
                conn = ticket_service._conn()
                try:
                    conn.execute("DELETE FROM tickets WHERE id=?", (t["id"],))
                    conn.commit()
                finally:
                    conn.close()
            cleaned += 1
    # 清文档清单里的回流记录
    doc_list = _load_doc_list(KB_ID)
    kept = [d for d in doc_list if not (d.get("doc_id") or "").startswith("tkdoc_")]
    _save_doc_list(KB_ID, kept)
    print(f"[clean] 已清理历史评测工单/回流文档 {cleaned} 条")


def phase1_baseline():
    print(f"\n========== Phase 1 基线：{len(CASES)} 个个案问题（预期全部兜底）==========")
    th = _threshold()
    print(f"兜底阈值: {th}")
    tickets = []
    baseline_rows = []
    all_below = True
    for c in CASES:
        top1, n = _top1(c["q"])
        hit = top1 >= th
        all_below = all_below and not hit
        status = "会兜底✓" if not hit else "直接命中✗(库里有答案,不构成兜底场景)"
        print(f"  [{status}] top1={top1:.3f} 召回{n}条 | {c['q']}")
        baseline_rows.append({"q": c["q"], "top1": round(top1, 3), "below": not hit})
        tid = ticket_service.create_ticket(c["q"], c["q"], top1, KB_ID, f"{MARK}_s1")
        tickets.append({"id": tid, **c})
    print(f"基线结论: {'全部兜底，闭环前提成立' if all_below else '存在直接命中，请更换未覆盖的个案问题'}")
    return tickets, baseline_rows


def phase2_resolve(tickets):
    print(f"\n========== Phase 2 模拟人工：处理 {len(tickets)} 张工单并回流 ==========")
    for t in tickets:
        r = ticket_service.resolve_ticket(t["id"], t["answer"], feedback=True)
        print(f"  工单 {t['id']} 已处理，回流文档 {r['doc_id']}")


def phase3_retest():
    print(f"\n========== Phase 3 复测：同题 + 换问法改写题 ==========")
    th = _threshold()
    same_hit, para_hit, same_scores, para_scores = 0, 0, [], []
    rows = []
    for c in CASES:
        top1, _ = _top1(c["q"])
        same_scores.append(top1)
        s1 = top1 >= th
        same_hit += s1
        top1p, _ = _top1(c["paraphrase"])
        para_scores.append(top1p)
        s2 = top1p >= th
        para_hit += s2
        rows.append({"q": c["q"], "same_top1": round(top1, 3), "same_hit": s1,
                     "para": c["paraphrase"], "para_top1": round(top1p, 3), "para_hit": s2})
        print(f"  同题 top1={top1:.3f} [{'命中✓' if s1 else '未中✗'}] | "
              f"改写「{c['paraphrase']}」 top1={top1p:.3f} [{'命中✓' if s2 else '未中✗'}]")
    n = len(CASES)
    print(f"\n========== 闭环评测结论 ==========")
    print(f"同题二次命中:   {same_hit}/{n} = {same_hit / n * 100:.0f}%")
    print(f"改写题泛化命中: {para_hit}/{n} = {para_hit / n * 100:.0f}%")
    print(f"统计口径: rerank 分 ≥ 兜底阈值({th}) 视为命中（即不再触发兜底/建单）")
    return {"same_hit": same_hit, "para_hit": para_hit, "n": n, "threshold": th,
            "same_scores": same_scores, "para_scores": para_scores, "rows": rows}


def save_report(baseline_rows, result):
    """【v3.23】评测报告落盘 eval/reports/（仿 evaluate_rag 的报告机制）"""
    import datetime
    rep_dir = Path(__file__).resolve().parent.parent / "eval" / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    rep = rep_dir / f"report_ticket_loop_{datetime.datetime.now():%Y%m%d_%H%M%S}.md"
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"# 工单闭环评测报告（{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
        f.write(f"- 闭环链路：低置信兜底 → 自动建工单 → 人工答案回流知识库 → 同题复问可命中\n")
        f.write(f"- 基线：{len(baseline_rows)} 个个案全部低于兜底阈值 "
                f"{'✓' if all(r['below'] for r in baseline_rows) else '✗（存在直接命中）'}\n")
        f.write(f"- **同题二次命中: {result['same_hit']}/{result['n']}**\n")
        f.write(f"- **改写题泛化命中: {result['para_hit']}/{result['n']}**\n")
        f.write(f"- 口径: rerank 分 ≥ 兜底阈值({result['threshold']}) 视为命中\n\n")
        f.write("| 个案问题 | 基线top1 | 同题top1 | 同题 | 改写问法 | 改写top1 | 改写 |\n|---|---|---|---|---|---|---|\n")
        for b, r in zip(baseline_rows, result["rows"]):
            f.write(f"| {b['q'][:24]} | {b['top1']} | {r['same_top1']} "
                    f"| {'✓' if r['same_hit'] else '✗'} | {r['para'][:20]} "
                    f"| {r['para_top1']} | {'✓' if r['para_hit'] else '✗'} |\n")
    print(f"报告已落盘: {rep}")
    return rep


def main():
    if "--clean" in sys.argv:
        phase_clean()
    stats_before = ticket_service.ticket_stats()
    print(f"工单库当前状态: {stats_before}")
    tickets, baseline_rows = phase1_baseline()
    phase2_resolve(tickets)
    result = phase3_retest()
    print(f"\n最终工单库: {ticket_service.ticket_stats()}")
    save_report(baseline_rows, result)
    return result


if __name__ == "__main__":
    main()
