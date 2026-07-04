# 【分工3写】向量入库/检索逻辑

from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from config.settings import LLM_CONFIG, SYSTEM_PROMPT, CHAT_CONFIG, FALLBACK_ANSWER
from schemas.models import ChatMessage, SearchResult
from utils.sensitive import filter_sensitive
# 全局大模型实例
llm = ChatOpenAI(
    api_key=LLM_CONFIG["api_key"],
    base_url=LLM_CONFIG["base_url"],
    model=LLM_CONFIG["model_name"],
    temperature=LLM_CONFIG["temperature"],
    max_tokens=LLM_CONFIG["max_tokens"]
)
# -------------------------- 临时模拟检索，等分工3写完删掉这部分就行 --------------------------
def mock_search(question: str, kb_id: str = "valorant", top_k: int = 3) -> list[SearchResult]:
    """模拟分工3的检索接口，返回正确的游戏资料，后期直接换成真实调用"""
    mock_data = [
        SearchResult(
            content="捷风（Jett）是无畏契约中的决斗者定位英雄，国籍韩国，技能包括：1. 上升气流（Q）：立即向上跃起；2. 顺风（E）：向移动方向冲刺一段距离；3. 逐风（C）：扔出烟雾弹；4. 飓刃（X）：召唤5把高精度飞刀，击杀敌人刷新飞刀。",
            source="无畏契约英雄手册.docx",
            score=0.92,
            doc_id="doc_001"
        ),
        SearchResult(
            content="捷风是高机动性决斗者，适合突破、拉枪线，常见技巧：E技能冲刺后急停开枪，Q技能升空不要原地停留容易被狙击，X飞刀适合中距离对枪。",
            source="无畏契约进阶技巧.pdf",
            score=0.87,
            doc_id="doc_002"
        )
    ]
    return mock_data
# ----------------------------------------------------------------------------------------
def chat_single_turn(session_id: str, question: str, kb_id: str = "valorant") -> tuple[str, list[dict]]:
    """
    单轮问答核心逻辑
    :return: (回答内容, 来源列表)
    """
    # 1. 敏感词校验
    is_sensitive, filtered_q = filter_sensitive(question)
    if is_sensitive:
        return "你的问题包含敏感词，请重新提问~", []
    # 2. 检索相关资料（现在用模拟，后期换成分工3的真实检索）
    search_results = mock_search(question, kb_id)
    # 3. 相似度判断，低于阈值返回兜底
    if not search_results or max([r.score for r in search_results]) < CHAT_CONFIG["similarity_threshold"]:
        return FALLBACK_ANSWER, []
    # 4. 拼接提示词
    context = "\n".join([f"参考资料{i+1}（来源：{r.source}）：{r.content}" for i, r in enumerate(search_results)])
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT + "\n请严格基于以下参考资料回答问题，不要编造内容：\n{context}"),
        ("human", "{question}")
    ])
    # 5. 调用大模型
    chain = prompt | llm
    response = chain.invoke({"context": context, "question": question})
    answer = response.content
    # 6. 整理来源
    sources = [{"name": r.source, "id": r.doc_id} for r in search_results]
    return answer, sources