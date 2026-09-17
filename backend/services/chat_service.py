import os
import json
import time
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from config.settings import (
    LLM_CONFIG, SYSTEM_PROMPT, CHAT_CONFIG, RAG_CONFIG,
    FALLBACK_ANSWER, REFUSE_ANSWER, CUSTOM_RULES, DEFAULT_KB_ID,
)
from schemas.models import ChatMessage, SearchResult
from utils.sensitive import filter_sensitive
from utils.audit import log_qa
from services.official_data_service import answer_official_data_query, expand_query_aliases, replace_aliases_with_official

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 全局大模型实例
llm = ChatOpenAI(
    api_key=LLM_CONFIG["api_key"],
    base_url=LLM_CONFIG["base_url"],
    model=LLM_CONFIG["model_name"],
    temperature=LLM_CONFIG["temperature"],
    max_tokens=LLM_CONFIG["max_tokens"],
    timeout=30  # 加超时，RAG场景prompt较长需要更长时间
)

# glm-4.7 系列是混合思考模型：不显式关闭思考时，答案会写进 reasoning_content、content 为空，
# 界面上就是空白回答。langchain-openai 0.1.x 的 model_kwargs 走不到 openai SDK 的 extra_body
# （会被 create() 当未知参数拒收），所以在这里包装 client.create 注入 extra_body。
_THINKING_TYPE = "enabled" if LLM_CONFIG.get("thinking") else "disabled"
_original_create = llm.client.create

def _create_with_thinking(*args, **kwargs):
    kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "thinking": {"type": _THINKING_TYPE}}
    return _original_create(*args, **kwargs)

llm.client.create = _create_with_thinking
# -------------------------- 大模型调用测试 --------------------------
def test_llm_call(question: str) -> str:
    """测试大模型是否能正常调用，返回回答文本"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是无畏契约游戏助手，简短回答即可。"),
        ("human", "{question}")
    ])
    return _call_llm_with_retry(prompt, {"question": question_for_prompt})

# -------------------------- 记忆相关工具函数 --------------------------
def _get_history_path(session_id: str) -> str:
    return os.path.join(CHAT_CONFIG["history_path"], f"{session_id}.json")
def load_history(session_id: str) -> list[ChatMessage]:
    path = _get_history_path(session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ChatMessage(**msg) for msg in data]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取历史失败 session_id=%s: %s，返回空历史", session_id, e)
        return []
def save_history(session_id: str, history: list[ChatMessage]):
    max_len = CHAT_CONFIG["max_history_turns"]
    cut_history = history[-max_len:]
    path = _get_history_path(session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([msg.model_dump() for msg in cut_history], f, ensure_ascii=False, indent=2)
def clear_history(session_id: str) -> bool:
    path = _get_history_path(session_id)
    if os.path.exists(path):
        os.remove(path)
    return True
def rollback_history(session_id: str, turn_index: int) -> bool:
    history = load_history(session_id)
    if turn_index < 0 or turn_index > len(history):
        return False
    new_history = history[:turn_index]
    save_history(session_id, new_history)
    return True
# -------------------------- 检索管线（v3.0：改写→混合召回→重排） --------------------------
def _retrieve(question: str, history: list[ChatMessage], kb_id: str) -> tuple[str, list[SearchResult]]:
    """
    三级检索管线：
      1. 查询改写：把"它的伤害多少"这类指代问题改写成独立完整问题
      2. 混合召回：BM25 关键词 + 向量语义双路召回，RRF 融合
      3. 重排序：CrossEncoder 精排取 top_k
    :return: (实际用于检索的问题, 精排后的结果列表)
    """
    from services.query_rewriter import rewrite_query
    from services.hybrid_retriever import hybrid_search
    from services.reranker import rerank

    question = expand_query_aliases(question)
    rewritten = rewrite_query(question, history)
    candidates = hybrid_search(rewritten, kb_id)
    results = rerank(rewritten, candidates)
    return rewritten, results


def _should_fallback(results: list[SearchResult]) -> bool:
    """兜底判断：检索结果为空，或最高置信分低于阈值（阈值随管线模式切换）"""
    if not results:
        return True
    if RAG_CONFIG.get("enable_rerank", True):
        return results[0].score < RAG_CONFIG["rerank_score_threshold"]
    # 无 rerank 时退回向量余弦相似度阈值（兼容旧管线）
    best = max((r.dense_score if r.dense_score is not None else r.score) for r in results)
    return best < RAG_CONFIG["score_threshold"]


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen = set()
    out = []
    for r in results:
        key = (r.doc_id, (r.content or '')[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _build_sources(results: list[SearchResult]) -> list[dict]:
    results = _dedupe_results(results)
    return [
        {"name": r.source, "id": r.doc_id, "score": r.score}
        for r in results
    ] if RAG_CONFIG.get("enable_source_score", True) else [
        {"name": r.source, "id": r.doc_id}
        for r in results
    ]


def _build_rag_messages(history: list[ChatMessage], results: list[SearchResult], question: str) -> ChatPromptTemplate:
    """拼 RAG 提示词：系统提示 + 参考资料 + 历史 + 最新问题"""
    results = _dedupe_results(results)
    context = "\n".join(
        [f"参考资料{i+1}（来源：{r.source}）：{r.content}" for i, r in enumerate(results)]
    )
    messages = [
        ("system", SYSTEM_PROMPT + f"""
请严格基于参考资料和历史对话回答问题，遵守以下规则：
1. 参考资料：{context}
2. 如果问题与无畏契约游戏完全无关（如写代码、做菜、天气、闲聊等日常请求），即使参考资料里出现了相关字样，也必须直接返回：{REFUSE_ANSWER}
3. 参考资料里没有的内容不要编造，直接返回兜底话术
4. 不要重复介绍同一技能或同一段内容；如果答案已经说清楚，直接结束。
"""),
    ]
    for msg in history:
        messages.append((msg.role, msg.content))
    messages.append(("human", "{question}"))
    return ChatPromptTemplate.from_messages(messages)


def _call_llm_with_retry(prompt, inputs, max_retry=2):
    """大模型调用带重试：失败自动重试，最终失败返回友好提示（真实错误已写入日志）"""
    for i in range(max_retry + 1):
        try:
            chain = prompt | llm
            response = chain.invoke(inputs)
            return response.content
        except Exception as e:
            logger.error("大模型调用失败(第%d次) 输入=%s 错误=%s", i + 1, inputs, e)
            if i == max_retry:
                return "抱歉，当前服务有点忙，请稍后再试~"
            time.sleep(3 * (i + 1))  # 递增退避：免费模型偶发限流(429)，等久一点再试


def chat_single_turn(session_id: str, question: str, kb_id: str = None) -> tuple[str, list[dict], list[ChatMessage]]:
    """单轮问答主流程（v3.0 管线），返回 (答案, 来源, 最新历史)"""
    kb_id = kb_id or DEFAULT_KB_ID
    start_time = time.time()

    # 1. 敏感词校验
    is_sensitive, filtered_q = filter_sensitive(question)
    if is_sensitive:
        answer = "你的问题包含敏感词，请重新提问~"
        log_qa(session_id, question, question, [], answer, (time.time() - start_time) * 1000, kb_id, "sensitive_block")
        return answer, [], load_history(session_id)

    # 2. 自定义关键词规则优先
    for keyword, reply in CUSTOM_RULES.items():
        if keyword in question:
            history = load_history(session_id)
            history.append(ChatMessage(role="user", content=question))
            history.append(ChatMessage(role="assistant", content=reply))
            save_history(session_id, history)
            return reply, [], history

    # 官方结构化数据优先：列表/定位/价格类问题走确定性回答，避免 LLM 胡编
    official_answer = answer_official_data_query(question)
    if official_answer:
        history = load_history(session_id)
        history.append(ChatMessage(role="user", content=question))
        history.append(ChatMessage(role="assistant", content=official_answer))
        save_history(session_id, history)
        return official_answer, [], history

    question_for_prompt = replace_aliases_with_official(question)

    # 3. 加载历史
    history = load_history(session_id)

    # 4~6. 三级检索管线：改写 → 混合召回 → 重排
    rewritten, results = _retrieve(question_for_prompt, history, kb_id)
    pipeline_desc = ("hybrid+rerank" if RAG_CONFIG.get("enable_rerank") else "hybrid") \
        if RAG_CONFIG.get("enable_hybrid_search") else "vector"

    # 7. 低置信兜底
    if _should_fallback(results):
        answer = FALLBACK_ANSWER
        sources = []
    else:
        # 8. 拼提示词（加拒答规则），带重试调用大模型
        prompt = _build_rag_messages(history, results, question_for_prompt)
        answer = _call_llm_with_retry(prompt, {"question": question_for_prompt})
        sources = _build_sources(results)

    # 9. 更新历史 + 审计留痕
    history.append(ChatMessage(role="user", content=question))
    history.append(ChatMessage(role="assistant", content=answer))
    save_history(session_id, history)
    log_qa(session_id, question, rewritten, sources, answer,
           (time.time() - start_time) * 1000, kb_id, pipeline_desc)
    return answer, sources, history


def chat_single_turn_stream(session_id: str, question: str, kb_id: str = None):
    """
    流式问答主流程（生成器）：先产出检索来源，再逐 token 产出答案。
    统一 yield 事件字典：
      {"type": "sources", "sources": [...]}
      {"type": "token",   "delta": "..."}
      {"type": "done",    "answer": "...", "history": [...]}
    """
    kb_id = kb_id or DEFAULT_KB_ID
    start_time = time.time()

    # 敏感词 / 关键词规则 / 兜底：这些场景没有流式生成过程，一次性给出
    is_sensitive, _ = filter_sensitive(question)
    if is_sensitive:
        answer = "你的问题包含敏感词，请重新提问~"
        yield {"type": "token", "delta": answer}
        yield {"type": "done", "answer": answer, "history": [m.model_dump() for m in load_history(session_id)]}
        return

    for keyword, reply in CUSTOM_RULES.items():
        if keyword in question:
            history = load_history(session_id)
            history.append(ChatMessage(role="user", content=question))
            history.append(ChatMessage(role="assistant", content=reply))
            save_history(session_id, history)
            yield {"type": "token", "delta": reply}
            yield {"type": "done", "answer": reply, "history": [m.model_dump() for m in history]}
            return

    official_answer = answer_official_data_query(question)
    if official_answer:
        history = load_history(session_id)
        history.append(ChatMessage(role="user", content=question))
        history.append(ChatMessage(role="assistant", content=official_answer))
        save_history(session_id, history)
        yield {"type": "token", "delta": official_answer}
        yield {"type": "done", "answer": official_answer, "history": [m.model_dump() for m in history]}
        return

    question_for_prompt = replace_aliases_with_official(question)
    history = load_history(session_id)
    rewritten, results = _retrieve(question_for_prompt, history, kb_id)
    pipeline_desc = ("hybrid+rerank" if RAG_CONFIG.get("enable_rerank") else "hybrid") \
        if RAG_CONFIG.get("enable_hybrid_search") else "vector"

    if _should_fallback(results):
        sources = []
        yield {"type": "sources", "sources": sources}
        answer = FALLBACK_ANSWER
        for ch in answer:
            yield {"type": "token", "delta": ch}
    else:
        sources = _build_sources(results)
        yield {"type": "sources", "sources": sources}
        prompt = _build_rag_messages(history, results, question_for_prompt)
        chain = prompt | llm
        parts = []
        try:
            for chunk in chain.stream({"question": question_for_prompt}):
                delta = chunk.content or ""
                if delta:
                    parts.append(delta)
                    yield {"type": "token", "delta": delta}
            answer = "".join(parts)
        except Exception as e:
            logger.error("流式大模型调用失败: %s", e)
            answer = "抱歉，当前服务有点忙，请稍后再试~"
            yield {"type": "token", "delta": answer}

    history.append(ChatMessage(role="user", content=question))
    history.append(ChatMessage(role="assistant", content=answer))
    save_history(session_id, history)
    log_qa(session_id, question, rewritten, sources, answer,
           (time.time() - start_time) * 1000, kb_id, pipeline_desc)
    yield {"type": "done", "answer": answer, "history": [m.model_dump() for m in history]}


def warmup_retrieval(kb_id: str = None) -> dict:
    """预热检索链路：加载 embedding、BM25 索引、CrossEncoder 重排模型。
    不调用大模型生成，适合面试演示前先请求一次。"""
    kb_id = kb_id or DEFAULT_KB_ID
    start = time.time()
    _rewritten, results = _retrieve("无畏契约英雄和武器介绍", [], kb_id)
    return {
        "kb_id": kb_id,
        "candidates": len(results),
        "latency_ms": round((time.time() - start) * 1000, 1),
    }
