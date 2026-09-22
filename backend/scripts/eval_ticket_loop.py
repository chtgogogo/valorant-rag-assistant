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

KB_ID = "ecommerce"
MARK = "tkfeval"  # 评测标记：session_id 前缀，--clean 按此清理

# 个案问题：知识库只覆盖"标准政策"，这些具体品类个案故意未覆盖 → 基线应全部兜底
CASES = [
    {
        "q": "我买的蓝牙耳机用了两个月左耳没声音了怎么办",
        "answer": "数码配件类商品按商品详情页标注的保修期执行：蓝牙耳机一般提供12个月保修。"
                  "您的故障发生在两个月内，属于保修期内非人为损坏，可免费维修或更换。"
                  "请在订单页申请售后选择「维修/换新」，登记故障现象（左耳无声），"
                  "客服核实后提供寄修或换新服务，保修期内来回运费由商家承担。",
        "paraphrase": "耳机单边没声音还在保修期可以换新吗",
    },
    {
        "q": "商家给我开的电子发票抬头写错了怎么换开",
        "answer": "电子发票抬头开错可以申请换开：①订单完成后90天内，在「订单详情-发票中心」发起换开申请，"
                  "填写正确的抬头信息（单位名称需与营业执照全称一致）；"
                  "②已开具的蓝字发票需先红冲再重开，全程1-3个工作日，换开的新发票会发送到您的邮箱；"
                  "③换开发票不影响商品售后权益——售后以订单信息为准，不以发票抬头为准；"
                  "④超过90天的换开需求请提交人工工单，由财务专人评估处理。",
        "paraphrase": "发票抬头打错了怎么重新开一张",
    },
    {
        "q": "预售付的定金现在不想要了能退吗",
        "answer": "根据《消费者权益保护法》及平台预售规则，定金具有担保性质，消费者单方面取消订单的定金原则上不退；"
                  "但两种情况可退：①商家未在承诺时间内发货或取消活动的，双倍返还定金；"
                  "②尾款支付失败非买家原因导致订单未成立的。建议优先与商家协商，协商不成可申请平台介入。",
        "paraphrase": "预售定金不想要了怎么要回来",
    },
    {
        "q": "山地车骑了半个月刹车线断了算质量问题吗",
        "answer": "正常使用半个月内刹车线断裂属于自然故障范畴，不算人为损坏。"
                  "自行车整车一般提供12个月质保（易损耗材如刹车片、内外胎除外，刹车线属质保范围）。"
                  "您可发起质保售后，商家免费补发刹车线并提供安装教程，或就近维修后凭发票报销费用。",
        "paraphrase": "自行车刹车线断了在保修范围吗",
    },
    {
        "q": "网购的猫粮猫不爱吃能退吗",
        "answer": "\"宠物不爱吃\"属于主观口感偏好，不是质量问题，且食品类拆封后影响二次销售，"
                  "不适用七天无理由退货。两个建议：①未拆封的可以走七天无理由退货（自付运费）；"
                  "②已拆封的可联系客服，部分品牌支持\"挑食包退\"活动（首袋不满意全额退），以商品页标识为准。",
        "paraphrase": "猫不吃买的猫粮可以申请退货不",
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
    all_below = True
    for c in CASES:
        top1, n = _top1(c["q"])
        hit = top1 >= th
        all_below = all_below and not hit
        status = "会兜底✓" if not hit else "直接命中✗(库里有答案,不构成兜底场景)"
        print(f"  [{status}] top1={top1:.3f} 召回{n}条 | {c['q']}")
        tid = ticket_service.create_ticket(c["q"], c["q"], top1, KB_ID, f"{MARK}_s1")
        tickets.append({"id": tid, **c})
    print(f"基线结论: {'全部兜底，闭环前提成立' if all_below else '存在直接命中，请更换未覆盖的个案问题'}")
    return tickets


def phase2_resolve(tickets):
    print(f"\n========== Phase 2 模拟人工：处理 {len(tickets)} 张工单并回流 ==========")
    for t in tickets:
        r = ticket_service.resolve_ticket(t["id"], t["answer"], feedback=True)
        print(f"  工单 {t['id']} 已处理，回流文档 {r['doc_id']}")


def phase3_retest():
    print(f"\n========== Phase 3 复测：同题 + 换问法改写题 ==========")
    th = _threshold()
    same_hit, para_hit, same_scores, para_scores = 0, 0, [], []
    for c in CASES:
        top1, _ = _top1(c["q"])
        same_scores.append(top1)
        s1 = top1 >= th
        same_hit += s1
        top1p, _ = _top1(c["paraphrase"])
        para_scores.append(top1p)
        s2 = top1p >= th
        para_hit += s2
        print(f"  同题 top1={top1:.3f} [{'命中✓' if s1 else '未中✗'}] | "
              f"改写「{c['paraphrase']}」 top1={top1p:.3f} [{'命中✓' if s2 else '未中✗'}]")
    n = len(CASES)
    print(f"\n========== 闭环评测结论 ==========")
    print(f"同题二次命中:   {same_hit}/{n} = {same_hit / n * 100:.0f}%")
    print(f"改写题泛化命中: {para_hit}/{n} = {para_hit / n * 100:.0f}%")
    print(f"统计口径: rerank 分 ≥ 兜底阈值({th}) 视为命中（即不再触发兜底/建单）")
    return {"same_hit": same_hit, "para_hit": para_hit, "n": n,
            "same_scores": same_scores, "para_scores": para_scores}


def main():
    if "--clean" in sys.argv:
        phase_clean()
    stats_before = ticket_service.ticket_stats()
    print(f"工单库当前状态: {stats_before}")
    tickets = phase1_baseline()
    phase2_resolve(tickets)
    result = phase3_retest()
    print(f"\n最终工单库: {ticket_service.ticket_stats()}")
    return result


if __name__ == "__main__":
    main()
