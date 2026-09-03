import os
import json
import time
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from config.settings import LLM_CONFIG, SYSTEM_PROMPT, CHAT_CONFIG, RAG_CONFIG, FALLBACK_ANSWER, REFUSE_ANSWER
from schemas.models import ChatMessage, SearchResult
from utils.sensitive import filter_sensitive

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
# -------------------------- 大模型调用测试 --------------------------
def test_llm_call(question: str) -> str:
    """测试大模型是否能正常调用，返回回答文本"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是无畏契约游戏助手，简短回答即可。"),
        ("human", "{question}")
    ])
    return _call_llm_with_retry(prompt, {"question": question})

# 自定义关键词规则，命中直接返回，不调用大模型
CUSTOM_RULES = {
    "帮助": "我是无畏契约智能游戏助手，你可以问我：\n1. 英雄定位、技能、背景故事\n2. 武器属性、伤害、价格\n3. 地图点位、道具技巧\n4. 游戏玩法、上分技巧",
    "版本": "无畏契约智能助手 v2.0 | 毕业实训升级版（RAG增强+知识库管理+教程资源）",
    "你好": "你好呀！我是无畏契约专属小助手，有什么游戏问题都可以问我~",
    "谢谢": "不客气！祝你游戏愉快，把把五杀😎"
}
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
# -------------------------- 检索部分（已对接分工3真实检索） --------------------------
def search_docs(question: str, kb_id: str = "valorant", top_k: int = None) -> list[SearchResult]:
    """
    检索文档，调用分工3的向量检索引擎
    """
    if top_k is None:
        top_k = RAG_CONFIG["top_k"]
    from services.vector_service import search_vector
    return search_vector(question, kb_id, top_k)
# ----------------------------------------------------------------------------------------
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
            time.sleep(1)  # 等1秒后重试
def chat_single_turn(session_id: str, question: str, kb_id: str = "valorant") -> tuple[str, list[dict], list[ChatMessage]]:
    # 1. 敏感词校验
    is_sensitive, filtered_q = filter_sensitive(question)
    if is_sensitive:
        return "你的问题包含敏感词，请重新提问~", [], load_history(session_id)
    # 2. 自定义关键词规则优先
    for keyword, reply in CUSTOM_RULES.items():
        if keyword in question:
            history = load_history(session_id)
            history.append(ChatMessage(role="user", content=question))
            history.append(ChatMessage(role="assistant", content=reply))
            save_history(session_id, history)
            return reply, [], history
    # 3. 加载历史
    history = load_history(session_id)
    # 4. 检索资料
    search_results = search_docs(question, kb_id)
    # 5. 低相似度兜底
    if not search_results or max([r.score for r in search_results]) < RAG_CONFIG["score_threshold"]:
        answer = FALLBACK_ANSWER
        sources = []
    else:
        # 6. 拼提示词（加拒答规则）
        context = "\n".join([f"参考资料{i+1}（来源：{r.source}）：{r.content}" for i, r in enumerate(search_results)])
        messages = [
            ("system", SYSTEM_PROMPT + f"""
请严格基于参考资料和历史对话回答问题，遵守以下规则：
1. 参考资料：{context}
2. 如果问题和无畏契约游戏完全无关，直接返回：{REFUSE_ANSWER}
3. 参考资料里没有的内容不要编造，直接返回兜底话术
"""),
        ]
        for msg in history:
            messages.append((msg.role, msg.content))
        messages.append(("human", "{question}"))
        prompt = ChatPromptTemplate.from_messages(messages)
        # 7. 带重试调用大模型
        answer = _call_llm_with_retry(prompt, {"question": question})
        sources = [
            {"name": r.source, "id": r.doc_id, "score": r.score}
            for r in search_results
        ] if RAG_CONFIG.get("enable_source_score", True) else [
            {"name": r.source, "id": r.doc_id} for r in search_results
        ]
    # 8. 更新历史
    history.append(ChatMessage(role="user", content=question))
    history.append(ChatMessage(role="assistant", content=answer))
    save_history(session_id, history)
    return answer, sources, history