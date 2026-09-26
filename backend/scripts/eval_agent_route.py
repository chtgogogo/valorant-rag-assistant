# -*- coding: utf-8 -*-
# 【W8-卡3】Agent 路由评测：路由准确率（默认，零成本确定性）+ 双路径事实错误对照（--with-llm）
# ------------------------------------------------------------
# 用法（backend 目录下）：
#   python scripts/eval_agent_route.py             # 路由准确率：16 题 expect_route vs 规则路由实判
#   python scripts/eval_agent_route.py --with-llm  # 追加双路径对照：A01~A10 真跑 Workflow 与 Agent
#                                                  # 各一遍，LLM 裁判对照知识库资料数事实错误
#                                                  #（消耗真实额度，建议低峰期跑，run_full_eval 同惯例）
# 报告落盘 eval/reports/report_agent_route_<时间戳>.md。
# 口径对齐：任务卡卡 3「评测报告新增两列：路由准确率、双路径事实错误对照」；
#   双路径对照即卡 1 顺延、卡 7 完成后并入卡 3 的 10 题基线（UPGRADE_STATE 状态板）。
# 提示词纪律：JSON 花括号样板用字符串拼接而非 str.format——v3.3 Critic / 忠实度判分
#   同款坑（花括号被当占位符解析），不重踩。
# ------------------------------------------------------------
import argparse
import datetime
import json
import os
import re
import sys
import time

parser = argparse.ArgumentParser(description="W8-卡3 Agent 路由评测")
parser.add_argument("--with-llm", action="store_true",
                    help="追加双路径对照（Workflow vs Agent 真跑 + LLM 事实错误裁判）")
parser.add_argument("--kb-id", default="valorant", help="双路径对照使用的知识库（默认 valorant）")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(BACKEND_DIR, "eval", "eval_set_agent.json")


def _apply_eval_env():
    """评测环境预设（与 run_full_eval 同款）：必须在导入项目模块前调用"""
    os.environ.setdefault("RAG_CRITIC", "0")
    os.environ.setdefault("LLM_TEMPERATURE", "0")
    os.environ.setdefault("EMBEDDING_DEVICE", os.getenv("EMBEDDING_DEVICE", "cpu"))
    sys.path.insert(0, BACKEND_DIR)


def compute_route_accuracy(cases: list) -> dict:
    """【列 1】路由准确率：expect_route vs 规则路由实判（纯函数，确定性，零成本）"""
    from services.router_service import route_query
    rows = []
    for c in cases:
        d = route_query(c["question"])
        rows.append({"id": c["id"], "type": c["type"], "question": c["question"],
                     "expect": c["expect_route"], "actual": d.route,
                     "ok": d.route == c["expect_route"], "reason": d.reason})
    correct = sum(1 for r in rows if r["ok"])
    return {"total": len(rows), "correct": correct,
            "accuracy": (correct / len(rows)) if rows else 0.0, "rows": rows}


def run_workflow_path(question: str, kb_id: str) -> dict:
    """Workflow 路径：检索管线直连（不写历史/缓存/审计——评测零污染，evaluate_rag 同款）"""
    from services.chat_service import _retrieve, _build_rag_messages, _call_llm_with_retry
    t0 = time.time()
    _rewritten, results = _retrieve(question, [], kb_id)
    if not results:
        return {"answer": "", "context": "", "sources": [],
                "elapsed_s": time.time() - t0}
    prompt = _build_rag_messages([], results, question)
    answer = _call_llm_with_retry(prompt, {"question": question})
    context = "\n".join(f"[{i + 1}]（{r.source}）{r.content[:300]}"
                        for i, r in enumerate(results[:5]))
    return {"answer": answer or "", "context": context,
            "sources": [r.source for r in results[:5]],
            "elapsed_s": time.time() - t0}


def run_agent_path(question: str, kb_id: str) -> dict:
    """Agent 路径：run_agent 完整循环（轨迹照常落 agent_traces/ 可回放）"""
    from services.agent.loop import run_agent
    t0 = time.time()
    result = run_agent(question, kb_id=kb_id, session_id="eval-agent-route")
    return {"answer": result["answer"] or "", "degraded": result.get("degraded", False),
            "steps": len(result.get("steps", [])),
            "sources": [s.get("name", "") for s in result.get("sources", [])[:5]],
            "elapsed_s": time.time() - t0}


_JUDGE_RULES = ('你是严格的事实核查员。给你用户问题、知识库检索资料和两个系统分别生成的答案。\n'
                '分别核对两个答案：与资料不符、资料中没有依据（编造）或自相矛盾的关键事实，各算一个错误；\n'
                '措辞差异不算错误；答案明确说明"资料中未提及"不算错误。\n'
                '只输出JSON，格式：{"workflow_errors": 数字, "agent_errors": 数字, '
                '"issues": ["错误简述，前缀标注 wf: 或 agent:"]}\n\n')


def _judge_prompt(question: str, context: str, wf_answer: str, agent_answer: str) -> str:
    return (_JUDGE_RULES
            + f"用户问题：{question}\n\n知识库检索资料：\n{context}\n\n"
            + f"【工作流答案】\n{wf_answer}\n\n【Agent答案】\n{agent_answer}")


def judge_dual_path(question: str, context: str, wf_answer: str, agent_answer: str) -> dict:
    """【列 2】双路径事实错误裁判：对照知识库资料核两路答案，3 次退避重试，失败 None 不进分母"""
    from services.llm_factory import make_llm
    llm = make_llm(thinking=False, timeout=90, temperature=0)
    waits = (3, 6, 0)
    for attempt in range(3):
        try:
            raw = llm.invoke(_judge_prompt(question, context, wf_answer, agent_answer)).content
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                return {"wf_errors": max(0, int(data.get("workflow_errors", 0))),
                        "agent_errors": max(0, int(data.get("agent_errors", 0))),
                        "issues": data.get("issues") or []}
            print(f"  [裁判第{attempt + 1}次输出不可解析] {str(raw)[:80]}")
        except Exception as e:
            print(f"  [裁判第{attempt + 1}次失败] {e}")
        time.sleep(waits[attempt])
    return {"wf_errors": None, "agent_errors": None, "issues": ["裁判调用失败"]}


def main():
    _apply_eval_env()
    args = parser.parse_args()
    with open(SUITE, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    # ---- 列 1：路由准确率（确定性）----
    ra = compute_route_accuracy(cases)
    print(f"{'=' * 62}\nW8-卡3 路由评测  评测集={len(cases)} 题  双路径对照={'开' if args.with_llm else '关'}")
    for r in ra["rows"]:
        mark = "✓" if r["ok"] else "✗"
        print(f"  [{r['id']}] {mark} 期望={r['expect']} 实判={r['actual']}  {r['question'][:26]}")
    print(f"-" * 62)
    print(f"  路由准确率 : {ra['correct']}/{ra['total']} = {ra['accuracy']:.0%}")

    # ---- 列 2：双路径事实错误对照（--with-llm）----
    dual_rows = []
    if args.with_llm:
        agent_cases = [c for c in cases if c["expect_route"] == "agent"]
        print(f"\n双路径对照：{len(agent_cases)} 道 Agent 题真跑（Workflow vs Agent + 裁判）…")
        for c in agent_cases:
            print(f"  [{c['id']}] 跑双路径: {c['question'][:30]}")
            wf = run_workflow_path(c["question"], args.kb_id)
            ag = run_agent_path(c["question"], args.kb_id)
            verdict = judge_dual_path(c["question"], wf["context"], wf["answer"], ag["answer"])
            dual_rows.append({"id": c["id"], "question": c["question"],
                              "wf_errors": verdict["wf_errors"],
                              "agent_errors": verdict["agent_errors"],
                              "issues": verdict["issues"],
                              "wf_s": wf["elapsed_s"], "agent_s": ag["elapsed_s"],
                              "agent_steps": ag["steps"], "agent_degraded": ag["degraded"]})
            print(f"    → wf错误={verdict['wf_errors']} agent错误={verdict['agent_errors']} "
                  f"(wf {wf['elapsed_s']:.1f}s / agent {ag['elapsed_s']:.1f}s {ag['steps']}步)")

    # ---- 报告落盘 ----
    rep_dir = os.path.join(BACKEND_DIR, "eval", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    rep = os.path.join(rep_dir, f"report_agent_route_{datetime.datetime.now():%Y%m%d_%H%M%S}.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"# W8-卡3 路由评测（{datetime.datetime.now():%Y-%m-%d %H:%M:%S}）\n\n")
        f.write(f"- 评测集：eval_set_agent.json {ra['total']} 题｜双路径对照：{'开' if args.with_llm else '关'}\n")
        f.write(f"- **路由准确率：{ra['correct']}/{ra['total']} = {ra['accuracy']:.0%}**\n\n")
        f.write("### 路由逐题判定\n\n| id | 类型 | 期望 | 实判 | 结果 | 问题 |\n|---|---|---|---|---|---|\n")
        for r in ra["rows"]:
            f.write(f"| {r['id']} | {r['type']} | {r['expect']} | {r['actual']} "
                    f"| {'✓' if r['ok'] else '✗'} | {r['question'][:30]} |\n")
        if dual_rows:
            judged = [r for r in dual_rows if r["wf_errors"] is not None]
            wf_sum = sum(r["wf_errors"] for r in judged)
            ag_sum = sum(r["agent_errors"] for r in judged)
            f.write(f"\n### 双路径事实错误对照（{len(agent_cases) if args.with_llm else 0} 题，裁判成功 {len(judged)} 题）\n\n")
            f.write(f"- Workflow 事实错误合计：{wf_sum}｜Agent 事实错误合计：{ag_sum}"
                    f"（{len(judged)}/{len(dual_rows)} 题进分母，裁判失败不计）\n\n")
            f.write("| id | wf错误 | agent错误 | wf耗时s | agent耗时s | agent步数 | 降级 | 问题 |\n|---|---|---|---|---|---|---|---|\n")
            for r in dual_rows:
                f.write(f"| {r['id']} | {r['wf_errors'] if r['wf_errors'] is not None else '-'} "
                        f"| {r['agent_errors'] if r['agent_errors'] is not None else '-'} "
                        f"| {r['wf_s']:.1f} | {r['agent_s']:.1f} | {r['agent_steps']} "
                        f"| {'是' if r['agent_degraded'] else '否'} | {r['question'][:26]} |\n")
            issues = [i for r in dual_rows for i in r["issues"]]
            if issues:
                f.write("\n### 裁判标注的问题明细\n\n")
                for i in issues[:20]:
                    f.write(f"- {i}\n")
        else:
            f.write("\n### 双路径事实错误对照\n\n未运行（加 --with-llm 真跑，低峰期执行）。\n")
    print(f"\n{'=' * 62}\n报告已落盘: {rep}\n{'=' * 62}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
