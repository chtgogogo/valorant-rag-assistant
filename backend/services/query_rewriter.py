# 【新增 v3.0】查询改写：多轮对话指代消解
# ------------------------------------------------------------
# 解决的问题：用户第二轮问"它的伤害是多少"，直接拿去向量检索
# 会命中不到"幻影"相关内容。这里用大模型把带指代的问题改写成
# "幻影的伤害是多少"这样的独立完整问题，再做检索。
# 设计要点：任何失败（超时/报错）都降级返回原问题，绝不阻塞主流程。
# ------------------------------------------------------------
from langchain_core.prompts import ChatPromptTemplate
from config.settings import LLM_CONFIG, QUERY_REWRITE_PROMPT, RAG_CONFIG
from schemas.models import ChatMessage

import logging
import time

logger = logging.getLogger(__name__)

# 改写用小模型参数：低温度、少 token、独立实例（v3.3 起走 llm_factory：
# glm-4.7 混合思考模型必须显式关思考，否则答案写进 reasoning_content、content 为空，
# 改写会静默失效退回原问题；LLM_REWRITE_THINKING=1 可开思考换精度，超时自动放宽）
from services.llm_factory import make_llm

# 思考文本会占用 token 预算：开思考时上限放大到 1024，否则思考没写完就截断、content 为空
_thinking_on = LLM_CONFIG.get("thinking_rewrite", False)
_rewriter_llm = None  # 【v3.15】懒加载：import 本模块不再建实例、不再要求密钥（评测/CI 可无 key 运行）


def _get_rewriter_llm():
    """首次调用时才创建改写实例（参数与主链路实例不同：低温度、少 token、独立超时）"""
    global _rewriter_llm
    if _rewriter_llm is None:
        _rewriter_llm = make_llm(
            thinking=_thinking_on,
            timeout=LLM_CONFIG["thinking_timeout"] if _thinking_on else 8,
            temperature=0.1,
            max_tokens=1024 if _thinking_on else 128,
        )
    return _rewriter_llm

# 常见指代/省略特征：命中才调大模型改写，首轮或完整问题直接跳过，省时省 token
_ANAPHORA_HINTS = ("它", "他", "她", "这个", "那个", "这把", "那把", "这种", "那种",
                   "上面", "刚才", "前面", "其中", "呢", "还有", "继续", "再",
                   "另外", "同一个", "一样")


def _needs_rewrite(question: str) -> bool:
    """判断问题是否需要改写：有指代特征或过短（信息不完整）才算需要"""
    return len(question) <= 8 or any(h in question for h in _ANAPHORA_HINTS)


def rewrite_query(question: str, history: list[ChatMessage], rewrite_prompt: str = None) -> str:
    """
    把多轮对话中的问题改写成独立完整的问题
    :param question: 用户最新问题
    :param history: 对话历史（取最近 3 轮做上下文）
    :param rewrite_prompt: 领域专属改写提示词（v3.6 随 kb_id 切换）；None 用默认领域
    :return: 改写后的问题；失败一律返回原问题
    """
    # 开关关闭 / 没有历史（首轮无指代可言）→ 原样返回
    if not RAG_CONFIG.get("enable_query_rewrite", True):
        return question
    if not history or not _needs_rewrite(question):
        return question

    prompt_text = rewrite_prompt or QUERY_REWRITE_PROMPT
    if not prompt_text:
        return question

    # 取最近 3 轮历史拼成上下文（3 轮足够定位指代，多了浪费 token）
    recent = history[-6:] if len(history) > 6 else history
    history_text = "\n".join(
        f"{'用户' if m.role == 'user' else '助手'}：{m.content[:200]}"
        for m in recent
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", prompt_text),
        ("human", "历史对话：\n{history}\n\n用户最新问题：{question}"),
    ])
    try:
        chain = prompt | _get_rewriter_llm()
        response = None
        for attempt in range(2):  # 免费档偶发 429：一次退避重试，仍失败才降级
            try:
                response = chain.invoke({"history": history_text, "question": question})
                break
            except Exception as e:
                if attempt == 0:
                    logger.warning("查询改写第1次失败(2s后重试): %s", e)
                    time.sleep(2)
                else:
                    raise
        rewritten = response.content.strip().strip('"').strip("'")
        # 改写结果为空或明显异常 → 用原问题（常见原因：开思考后 token 上限被思考吃光）
        if not rewritten:
            logger.warning("查询改写返回空 content（疑思考吃满 token 预算），降级用原问题")
            return question
        logger.info("查询改写: [%s] → [%s]", question, rewritten)
        return rewritten
    except Exception as e:
        logger.warning("查询改写失败(降级用原问题): %s", e)
        return question
