# 【W8-卡1】Agent 主循环（自写，不引入 LangGraph —— 决策清单 D1 拍板）
# ------------------------------------------------------------
# 循环四步（全部透明可见，护栏在卡 2 参数化）：
#   ① 把问题 + 工具列表给模型（关思考：GLM 混合思考模型开思考时答案进
#      reasoning_content，content 为空——llm_factory.py 顶注的原坑）
#   ② 模型要工具 → 执行 → 结果以 ToolMessage 回填
#   ③ 重复，直到模型不再要工具（= 判定资料够了）
#   ④ 终答：用收集到的全部资料 + 开思考生成（对齐 Workflow 答案质量；
#      决策求快、生成求准，两段分离）
# 步数上限 v1 硬编码 6（任务卡约定），卡 2 改配置化并接降级。
# ------------------------------------------------------------
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config.settings import LLM_CONFIG
from services.agent.tools import TOOLS_SPEC, execute_tool, load_vocab_text
from services.llm_factory import make_llm

import logging
logger = logging.getLogger(__name__)

MAX_STEPS = 6  # 【W8-卡1】硬编码；卡 2 参数化 + 超限降级回 Workflow

_DECIDE_SYSTEM = """你是只能依据知识库资料回答问题的检索助手。铁律（违者答案作废）：
1. 只要问题涉及任何具体事实（英雄/技能/武器/地图/法条/数据等），必须先调用 search_knowledge_base 检索，
   禁止凭你自己的记忆直接回答——你的记忆会张冠李戴（实测曾把无畏契约英雄说成《英雄联盟》角色）。
2. 比较/对比类问题必须对每个对象分别检索一次。
3. 检索时 query 中的对象名必须使用下方标准名清单里的写法（禁止写错别字或自创简称）。
4. 检索回来后先核对：资料讲述的对象是否就是你问的对象（标题/名称对不上 = 料拿错了）；
   不符或不足时换更精确的关键词（可带上对象的标志性名称，如技能名）重查。
5. 资料足够且对象核对无误后输出最终答案，不再调用工具；核对不过就如实说明。
6. 最多执行 {max_steps} 步工具调用，请有节制地检索。

知识库对象标准名清单：{vocab}"""


def _decide_system() -> str:
    """组装决策系统提示（词表从别名表动态加载，加载失败时 vocab 为空串）"""
    return _DECIDE_SYSTEM.format(max_steps=MAX_STEPS, vocab=load_vocab_text())


_ANSWER_SYSTEM = """你是严谨的问答助手。只依据给出的资料回答用户问题：
- 关键事实必须能在资料中找到，并标注来源编号（如 [S1]）。
- 资料不足以回答的部分，明确说明"资料中未提及"，绝不编造。
- 用 Markdown 输出，中文回答。"""


def _summarize(text: str, limit: int = 120) -> str:
    """工具结果摘要（trace/前端轨迹展示用，截断即可）"""
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def run_agent(question: str, kb_id: str,
              max_steps: int = MAX_STEPS) -> dict:
    """Agent 主循环
    :return: {"answer", "sources", "steps", "degraded"}
             steps = 每步轨迹（工具名/参数/结果摘要/耗时），卡 5 前端直接渲染
             degraded 在卡 2 前恒为 False（本卡不接降级路径）
    """
    started = time.time()
    decide_llm = make_llm(thinking=False,
                          timeout=LLM_CONFIG.get("agent_timeout", LLM_CONFIG["timeout"]))
    messages = [SystemMessage(content=_decide_system()),
                HumanMessage(content=question)]

    steps: list[dict] = []
    materials: list[str] = []   # 各轮检索到的资料（终答用）
    sources: list[dict] = []    # 结构化来源（去重后随响应返回）
    seen_sources: set = set()

    for step_no in range(1, max_steps + 1):
        t0 = time.time()
        ai: AIMessage = decide_llm.bind_tools(TOOLS_SPEC).invoke(messages)

        if not ai.tool_calls:
            # 模型判定够了。content 通常已含草稿答案，但 v1 统一走开思考终答
            # （对齐 Workflow 质量）；草稿仅作终答失败时的兜底。
            draft = (ai.content or "").strip()
            logger.info("Agent第%d步: 模型停止调工具，进入终答（草稿%d字）",
                        step_no, len(draft))
            answer = _final_answer(question, materials, draft)
            steps.append({"step": step_no, "type": "final", "tool": None,
                          "args": None, "summary": _summarize(answer),
                          "elapsed_ms": int((time.time() - t0) * 1000)})
            logger.info("Agent完成: %d步 / %.1fs / 来源%d个",
                        step_no, time.time() - started, len(sources))
            return {"answer": answer, "sources": sources, "steps": steps,
                    "degraded": False}

        # 执行模型点的每一个工具，结果逐条 ToolMessage 回填
        messages.append(ai)
        for tc in ai.tool_calls:
            ok, text, tool_sources = execute_tool(
                tc["name"], tc.get("args") or {}, default_kb_id=kb_id)
            if ok:
                materials.append(f"【检索「{tc['args'].get('query', '')}」的结果】\n{text}")
                for s in tool_sources:
                    key = (s.get("name"), s.get("id"))
                    if key not in seen_sources:
                        seen_sources.add(key)
                        sources.append(s)
            messages.append(ToolMessage(content=text, tool_call_id=tc["id"]))
            steps.append({"step": step_no, "type": "tool_call", "tool": tc["name"],
                          "args": tc.get("args"), "summary": _summarize(text),
                          "ok": ok, "elapsed_ms": int((time.time() - t0) * 1000)})
            logger.info("Agent第%d步: 调用%s(%r) ok=%s -> %d字",
                        step_no, tc["name"], tc.get("args", {}).get("query", ""), ok, len(text))

    # 步数超限：v1 如实报告（卡 2 在此处接"降级回 Workflow"）
    logger.warning("Agent步数超限(%d)，未得出最终答案", max_steps)
    return {"answer": f"（本次 Agent 检索未在 {max_steps} 步内完成，尚未接入自动降级，请重试或换一种问法）",
            "sources": sources, "steps": steps, "degraded": True}


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
