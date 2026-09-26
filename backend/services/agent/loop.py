# 【W8-卡1/卡2】Agent 主循环（自写，不引入 LangGraph —— 决策清单 D1 拍板）
# ------------------------------------------------------------
# 循环四步（全部透明可见）：
#   ① 把问题 + 工具列表给模型（关思考：GLM 混合思考模型开思考时答案进
#      reasoning_content，content 为空——llm_factory.py 顶注的原坑）
#   ② 模型要工具 → 执行 → 结果以 ToolMessage 回填
#   ③ 重复，直到模型不再要工具（= 判定资料够了）
#   ④ 终答：用收集到的全部资料 + 开思考生成（对齐 Workflow 答案质量；
#      决策求快、生成求准，两段分离）
# 【W8-卡2】护栏三条（全部可配，settings.RAG_CONFIG）：
#   步数上限（agent_max_steps，超限降级）/
#   工具熔断（同一工具连续失败 agent_tool_fail_disable 次即停用）/ 
#   失败降级（回落 Workflow + 向用户说明原因）
#   每步轨迹 JSONL 落盘 agent_trace_dir，可完整回放（卡 5 前端同源）
# ------------------------------------------------------------
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config.settings import LLM_CONFIG, RAG_CONFIG
from services.agent.tools import TOOLS_SPEC, execute_tool, load_vocab_text
from services.llm_factory import make_llm

import logging
logger = logging.getLogger(__name__)

MAX_STEPS = RAG_CONFIG["agent_max_steps"]      # 兼容旧引用；实际以每次读取的配置为准
TOOL_FAIL_DISABLE = RAG_CONFIG["agent_tool_fail_disable"]

_DECIDE_SYSTEM = """你是只能依据知识库资料回答问题的检索助手。铁律（违者答案作废）：
1. 只要问题涉及任何具体事实（英雄/技能/武器/地图/法条/数据等），必须先调用 search_knowledge_base 检索，
   禁止凭你自己的记忆直接回答——你的记忆会张冠李戴（实测曾把无畏契约英雄说成《英雄联盟》角色）。
2. 比较/对比类问题必须对每个对象分别检索一次。
3. 检索时 query 中的对象名必须使用下方标准名清单里的写法（禁止写错别字或自创简称）。
4. 检索回来后先核对：资料讲述的对象是否就是你问的对象（标题/名称对不上 = 料拿错了）；
   不符或不足时换更精确的关键词（可带上对象的标志性名称，如技能名）重查。
5. 资料足够且对象核对无误后输出最终答案，不再调用工具；核对不过就如实说明。
6. 最多执行 {max_steps} 步工具调用，请有节制地检索。
7. 动作类工具纪律【W8-卡4】：propose_kb_write 只生成"待审核写入方案"，绝不等于已写入——
   提交方案后必须告诉用户"该操作需管理员确认后才会生效"，禁止声称已经写进知识库；
   query_tickets 是只读查询，可放心使用。

知识库对象标准名清单：{vocab}"""


def _decide_system() -> str:
    """组装决策系统提示（词表从别名表动态加载，加载失败时 vocab 为空串）"""
    return _DECIDE_SYSTEM.format(max_steps=RAG_CONFIG["agent_max_steps"], vocab=load_vocab_text())


_ANSWER_SYSTEM = """你是严谨的问答助手。只依据给出的资料回答用户问题：
- 关键事实必须能在资料中找到，并标注来源编号（如 [S1]）。
- 资料不足以回答的部分，明确说明"资料中未提及"，绝不编造。
- 用 Markdown 输出，中文回答。"""

_DEGRADE_PREFIX = "（本次由基础问答模式回答：Agent 多步检索未成功（{reason}），以下为基础模式结果）\n\n"


def _summarize(text: str, limit: int = 120) -> str:
    """工具结果摘要（trace/前端轨迹展示用，截断即可）"""
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


class _Trace:
    """【W8-卡2】轨迹日志：一次执行一个 JSONL 文件，逐行追加，可完整回放"""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.dir = Path(RAG_CONFIG["agent_trace_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{trace_id}.jsonl"

    def write(self, record: dict):
        record.setdefault("trace_id", self.trace_id)
        record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Agent轨迹写盘失败(不阻塞主流程): %s", e)


def _degrade_to_workflow(session_id: str, question: str, kb_id: str, reason: str) -> str:
    """【W8-卡2】失败降级：回落现有 Workflow 出答案，结果加说明前缀（对用户诚实）"""
    from services.chat_service import chat_single_turn
    try:
        answer, _, _ = chat_single_turn(session_id, question, kb_id)
        return _DEGRADE_PREFIX.format(reason=reason) + (answer or "（基础模式也未获得答案）")
    except Exception as e:
        logger.warning("Agent降级到Workflow失败: %s", e)
        return f"（Agent 检索未成功（{reason}），且基础模式降级失败：{e}）"


def _emit(on_event, type_: str, **payload):
    """【W8-卡5】事件回调出口：on_event 未传时零开销；回调异常不阻塞主循环"""
    if on_event is None:
        return
    try:
        on_event({"type": type_, **payload})
    except Exception as e:  # 前端流断了不该弄死 Agent 本体
        logger.warning("Agent事件回调失败(不阻塞主流程): %s", e)


def run_agent(question: str, kb_id: str,
              session_id: str = "agent-anon",
              max_steps: int = None,
              on_event=None) -> dict:
    """Agent 主循环
    :param on_event: 【W8-卡5】可选回调，SSE 流式轨迹用；每步推送
        {"type": "tool_call"|"tool_result"|"final_answer"|"degraded", ...}，
        与 D2 拍板的通用事件协议对齐（新增事件类型前端按未知类型兜底，零改动）
    :return: {"answer", "sources", "steps", "degraded"}
             steps = 每步轨迹（工具名/参数/结果摘要/耗时），卡 5 前端直接渲染
             degraded=True 表示护栏触发并已降级回 Workflow
    """
    max_steps = max_steps or RAG_CONFIG["agent_max_steps"]
    trace = _Trace(uuid.uuid4().hex[:12])
    started = time.time()
    trace.write({"type": "meta", "question": question, "kb_id": kb_id,
                 "max_steps": max_steps, "session_id": session_id})
    decide_llm = make_llm(thinking=False,
                          timeout=LLM_CONFIG.get("agent_timeout", LLM_CONFIG["timeout"]))
    messages = [SystemMessage(content=_decide_system()),
                HumanMessage(content=question)]

    steps: list[dict] = []
    materials: list[str] = []   # 各轮检索到的资料（终答用）
    sources: list[dict] = []    # 结构化来源（去重后随响应返回）
    seen_sources: set = set()
    fail_counts: dict[str, int] = {}   # 【卡2】工具名 -> 连续失败次数
    disabled: set[str] = set()         # 【卡2】已熔断停用的工具
    ctx: dict = {}                     # 【卡4】本轮执行上下文：动作类工具携带结构化产物（如待确认写入方案）

    for step_no in range(1, max_steps + 1):
        t0 = time.time()
        ai: AIMessage = decide_llm.bind_tools(TOOLS_SPEC).invoke(messages)

        if not ai.tool_calls:
            # 模型判定够了。content 通常已含草稿答案，但统一走开思考终答
            # （对齐 Workflow 质量）；草稿仅作终答失败时的兜底。
            draft = (ai.content or "").strip()
            logger.info("Agent第%d步: 模型停止调工具，进入终答（草稿%d字）",
                        step_no, len(draft))
            answer = _final_answer(question, materials, draft)
            steps.append({"step": step_no, "type": "final", "tool": None,
                          "args": None, "summary": _summarize(answer),
                          "elapsed_ms": int((time.time() - t0) * 1000)})
            trace.write({"type": "final", "step": step_no, "sources": len(sources),
                         "elapsed_ms": steps[-1]["elapsed_ms"]})
            logger.info("Agent完成: %d步 / %.1fs / 来源%d个",
                        step_no, time.time() - started, len(sources))
            _emit(on_event, "final_answer", answer=answer, sources=sources)
            return {"answer": answer, "sources": sources, "steps": steps,
                    "degraded": False,
                    "pending_proposals": ctx.get("pending_proposals", [])}  # 【卡4】待人工确认的写入方案

        # 【卡2】要执行的工具若全部被熔断 = 无工具可用的死局，立即降级
        usable = [tc for tc in ai.tool_calls if tc["name"] not in disabled]
        if ai.tool_calls and not usable:
            reason = f"工具全部熔断停用：{sorted(disabled)}"
            logger.warning("Agent第%d步: %s → 降级Workflow", step_no, reason)
            return _finish_degraded(steps, trace, question, kb_id, session_id,
                                    reason, sources, on_event=on_event, ctx=ctx)

        # 执行模型点的每一个工具，结果逐条 ToolMessage 回填
        messages.append(ai)
        for tc in ai.tool_calls:
            name = tc["name"]
            _emit(on_event, "tool_call", step=step_no, tool=name, args=tc.get("args"))
            if name in disabled:
                text = f"（工具 {name} 已因连续失败被停用，请改用其他方式或直接回答）"
                ok = False
                tool_sources: list[dict] = []
            else:
                ok, text, tool_sources = execute_tool(
                    name, tc.get("args") or {}, default_kb_id=kb_id, ctx=ctx)
                if ok:
                    fail_counts[name] = 0
                    materials.append(f"【检索「{tc['args'].get('query', '')}」的结果】\n{text}")
                    for s in tool_sources:
                        key = (s.get("name"), s.get("id"))
                        if key not in seen_sources:
                            seen_sources.add(key)
                            sources.append(s)
                else:
                    fail_counts[name] = fail_counts.get(name, 0) + 1
                    if fail_counts[name] >= TOOL_FAIL_DISABLE:
                        disabled.add(name)
                        text += f"\n（工具 {name} 已连续失败 {fail_counts[name]} 次，本轮停用）"
                        logger.warning("Agent工具熔断: %s 连败%d次", name, fail_counts[name])
            messages.append(ToolMessage(content=text, tool_call_id=tc["id"]))
            steps.append({"step": step_no, "type": "tool_call", "tool": name,
                          "args": tc.get("args"), "summary": _summarize(text),
                          "ok": ok, "elapsed_ms": int((time.time() - t0) * 1000)})
            _emit(on_event, "tool_result", step=step_no, tool=name, ok=ok,
                  summary=steps[-1]["summary"])
            trace.write({"type": "tool_call", "step": step_no, "tool": name,
                         "args": tc.get("args"), "ok": ok,
                         "summary": steps[-1]["summary"]})
            logger.info("Agent第%d步: 调用%s(%r) ok=%s -> %d字",
                        step_no, name, tc.get("args", {}).get("query", ""), ok, len(text))

    # 【卡2】步数超限：降级回 Workflow + 说明原因（不再是干巴巴的"请重试"）
    reason = f"{max_steps} 步内未得出答案"
    logger.warning("Agent步数超限(%d) → 降级Workflow", max_steps)
    return _finish_degraded(steps, trace, question, kb_id, session_id,
                            reason, sources, on_event=on_event, ctx=ctx)


def _finish_degraded(steps: list, trace: "_Trace", question: str, kb_id: str,
                     session_id: str, reason: str, sources: list,
                     on_event=None, ctx: dict | None = None) -> dict:
    """收尾降级：回落 Workflow、写轨迹、返回 degraded 结果"""
    answer = _degrade_to_workflow(session_id, question, kb_id, reason)
    trace.write({"type": "degraded", "reason": reason})
    _emit(on_event, "degraded", reason=reason, answer=answer)
    steps.append({"step": steps[-1]["step"] + 1 if steps else 1,
                  "type": "degraded", "tool": None, "args": None,
                  "summary": _summarize(answer), "elapsed_ms": 0})
    return {"answer": answer, "sources": sources, "steps": steps,
            "degraded": True,
            "pending_proposals": (ctx or {}).get("pending_proposals", [])}  # 【卡4】降级前已提交的方案仍可确认


def _final_answer(question: str, materials: list[str], draft: str) -> str:
    """开思考终答：全部资料一次性给足上下文，要求标注来源编号"""
    if not materials:
        return draft or "（知识库中未检索到相关资料，无法回答。）"
    llm = make_llm(thinking=True,
                   timeout=LLM_CONFIG.get("thinking_timeout", LLM_CONFIG["timeout"]))
    context = "\n\n".join(materials)
    prompt = f"用户问题：{question}\n\n检索到的资料：\n{context}"
    try:
        resp = llm.invoke([SystemMessage(content=_ANSWER_SYSTEM),
                           HumanMessage(content=prompt)])
        return (resp.content or "").strip() or draft
    except Exception as e:
        logger.warning("Agent终答生成失败，回退决策模型草稿: %s", e)
        return draft or f"（答案生成失败：{e}）"
