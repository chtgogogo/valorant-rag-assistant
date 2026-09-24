import os
import json
import re
import time
import threading
from langchain.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from config.settings import (
    LLM_CONFIG, SYSTEM_PROMPT, CHAT_CONFIG, RAG_CONFIG,
    FALLBACK_ANSWER, REFUSE_ANSWER, CUSTOM_RULES, DEFAULT_KB_ID, TICKET_CONFIG,
    get_profile, CACHE_CONFIG,
)
from schemas.models import ChatMessage, SearchResult
from utils.sensitive import filter_sensitive
from utils.audit import log_qa
from services.official_data_service import answer_official_data_query, expand_query_aliases, replace_aliases_with_official
from services.llm_factory import make_llm
from services.semantic_cache import get_semantic_cache

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 大模型实例（v3.3：按环节拆分，各自独立思考开关与超时，见 llm_factory）
# 【v3.15】改为懒加载：import 本模块不再创建实例、不再要求密钥（评测/CI 的
# import 路径因此可无 key 运行）；首次真正调用时才创建，之后复用缓存
_llm_instances: dict = {}


def get_llm():
    """最终答案生成实例：默认关思考（保速度）"""
    if "llm" not in _llm_instances:
        _llm_instances["llm"] = make_llm(LLM_CONFIG.get("thinking", False), LLM_CONFIG["timeout"])
    return _llm_instances["llm"]


def get_llm_rewrite():
    """查询改写实例：默认开思考（精度优先，多轮指代消解受益）"""
    if "rewrite" not in _llm_instances:
        _llm_instances["rewrite"] = make_llm(
            LLM_CONFIG.get("thinking_rewrite", False),
            LLM_CONFIG["thinking_timeout"] if LLM_CONFIG.get("thinking_rewrite") else LLM_CONFIG["timeout"])
    return _llm_instances["rewrite"]


def get_llm_critic():
    """质量自评实例：默认关思考（Layer3 标注集 A/B 实测思考零增益）"""
    if "critic" not in _llm_instances:
        _llm_instances["critic"] = make_llm(LLM_CONFIG.get("thinking_critic", True), LLM_CONFIG["thinking_timeout"])
    return _llm_instances["critic"]


def get_llm_fallback():
    """限流兜底实例（【v3.18】LLM_FALLBACK_MODEL，默认 glm-4-flashx 同账号最便宜付费款；
    设空串关闭兜底，返回 None）"""
    if not LLM_CONFIG.get("fallback_model"):
        return None
    if "fallback" not in _llm_instances:
        _llm_instances["fallback"] = make_llm(
            thinking=False, timeout=LLM_CONFIG["timeout"],
            model_name=LLM_CONFIG["fallback_model"])
    return _llm_instances["fallback"]

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


# 【v3.17】per-session 历史锁：send 路由是同步 def（FastAPI 走线程池），同一会话并发请求
# 会在多线程里同时"读历史→追加→写文件"，无锁时互相覆盖丢消息
_history_locks: dict = {}
_locks_guard = threading.Lock()


def _get_history_lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        if session_id not in _history_locks:
            _history_locks[session_id] = threading.Lock()
        return _history_locks[session_id]


def append_history(session_id: str, user_content: str, assistant_content: str) -> list[ChatMessage]:
    """持锁追加一轮问答并持久化：load→append→save 在同一把 per-session 锁内完成，
    防并发请求互相覆盖。返回追加后的完整历史。"""
    with _get_history_lock(session_id):
        history = load_history(session_id)
        history.append(ChatMessage(role="user", content=user_content))
        history.append(ChatMessage(role="assistant", content=assistant_content))
        save_history(session_id, history)
        return history


def clear_history(session_id: str) -> bool:
    with _get_history_lock(session_id):
        path = _get_history_path(session_id)
        if os.path.exists(path):
            os.remove(path)
    return True


def rollback_history(session_id: str, turn_index: int) -> bool:
    with _get_history_lock(session_id):
        history = load_history(session_id)
        if turn_index < 0 or turn_index > len(history):
            return False
        save_history(session_id, history[:turn_index])
        return True
# -------------------------- 检索管线（v3.0：改写→混合召回→重排） --------------------------
def _retrieve(question: str, history: list[ChatMessage], kb_id: str, profile: dict = None) -> tuple[str, list[SearchResult]]:
    """
    三级检索管线：
      1. 查询改写：把"它的伤害是多少"这类指代问题改写成独立完整问题（改写提示词随领域）
      2. 混合召回：BM25 关键词 + 向量语义双路召回，RRF 融合
      3. 重排序：CrossEncoder 精排取 top_k
    :param profile: 领域配置（None 时用默认领域）——改写提示词等领域内容运行时跟随 kb_id
    :return: (实际用于检索的问题, 精排后的结果列表)
    """
    from services.query_rewriter import rewrite_query
    from services.hybrid_retriever import hybrid_search
    from services.reranker import rerank

    profile = profile or get_profile(kb_id)
    question = expand_query_aliases(question)
    rewritten = rewrite_query(question, history, rewrite_prompt=profile.get("query_rewrite_prompt", ""))
    candidates = hybrid_search(rewritten, kb_id)
    results = rerank(rewritten, candidates)
    return rewritten, results


def _should_fallback(results: list[SearchResult]) -> bool:
    """兜底判断：检索结果为空，或最高置信分低于阈值（阈值随管线模式切换）"""
    if not results:
        return True
    if RAG_CONFIG.get("enable_rerank", True):
        # 【v3.16】重排失败降级时 score 已是召回量纲（BM25/RRF，如 8.5），按 rerank
        # 阈值 0.60 比会失真——退回余弦相似度阈值判定（与无 rerank 分支同口径）
        if results[0].rerank_degraded:
            best = max((r.dense_score if r.dense_score is not None else r.score) for r in results)
            return best < RAG_CONFIG["score_threshold"]
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


# -------------------------- 质量自评 Critic（v3.3） --------------------------
# 定位：重排分数落在 [拒答阈值, CRITIC_SCORE_HIGH) 灰区时，用带思考的模型评估
# "检索资料是否足以回答"；不足则换角度重写查询再检索，最多 critic_max_iterations 轮。
# 每轮评估/重试全部写日志（logger + 返回 critic_log 供审计），行为可由 RAG_CRITIC=0 关闭。
_CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是RAG检索质量评审员。给你用户问题和检索到的资料片段，判断资料是否足以回答该问题。"
     "只输出JSON，格式：{{\"sufficient\": true或false, \"missing\": \"不足时缺什么信息（一句话，足够则留空）\"}}。不要输出任何其他内容。"),
    ("human", "用户问题：{question}\n\n检索到的资料：\n{context}"),
])

_ALT_QUERY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是查询改写助手。上一次检索用的查询没有找到足够资料。请换一个角度或用词重写这个查询"
     "（换同义词/上位词/拆出更具体的子问题，不要重复原查询）。只输出改写后的查询本身，不要解释。"),
    ("human", "用户原始问题：{question}\n上一次查询：{rewritten}\n资料中缺少：{missing}\n请输出新查询："),
])


def _critic_judge(question: str, results: list[SearchResult]) -> dict:
    """让带思考的模型评估检索资料充分性；任何失败都视为'足够'（不阻塞主流程）"""
    context = "\n".join(
        f"[{i + 1}]（来源：{r.source}，分数{r.score}）{r.content[:200]}"
        for i, r in enumerate(results[:5])
    )
    try:
        raw = _call_llm_with_retry(_CRITIC_PROMPT,
                                   {"question": question, "context": context},
                                   model=get_llm_critic())
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        return json.loads(m.group(0)) if m else {"sufficient": True}
    except Exception as e:
        logger.warning("[Critic] 评审调用失败(按'足够'处理，不阻塞): %s", e)
        return {"sufficient": True}


def _generate_alternative_query(question: str, rewritten: str, missing: str) -> str | None:
    """生成换角度的替代查询；失败返回 None（调用方结束循环）"""
    try:
        raw = _call_llm_with_retry(_ALT_QUERY_PROMPT,
                                   {"question": question, "rewritten": rewritten, "missing": missing},
                                   model=get_llm_rewrite())
        alt = (raw or "").strip().splitlines()[0].strip().strip('"')
        return alt or None
    except Exception as e:
        logger.warning("[Critic] 替代查询生成失败: %s", e)
        return None


def _critic_refine(question: str, rewritten: str, results: list[SearchResult],
                   kb_id: str) -> tuple[str, list[SearchResult], list[dict]]:
    """
    质量自评主流程（在三级检索管线之后、生成之前执行）：
      1. 高分快速通道：top1 ≥ CRITIC_SCORE_HIGH 直接放行（绝大多数问题走这里，零额外延迟）
      2. 灰区：评估资料充分性 → 不足则换写法重检索，取两轮中 top1 更高者
      3. 全程 logger.info 留痕，并返回逐轮 critic_log
    :return: (最终用于检索的问题, 最终结果列表, critic_log)
    """
    critic_log: list[dict] = []
    if not RAG_CONFIG.get("critic_enabled", True) or not results:
        return rewritten, results, critic_log
    if not RAG_CONFIG.get("enable_rerank", True):
        return rewritten, results, critic_log  # 无 rerank 置信分，灰区无从判断

    high = RAG_CONFIG.get("critic_score_high", 0.75)
    best_q, best_r = rewritten, results
    max_iter = RAG_CONFIG.get("critic_max_iterations", 1)  # 【v3.11】默认 1 轮（速度优先），CRITIC_MAX_ROUNDS 可调回 3

    from services.hybrid_retriever import hybrid_search
    from services.reranker import rerank as _rerank

    for i in range(1, max_iter + 1):
        top1 = best_r[0].score
        if top1 >= high:
            logger.info("[Critic] 第%d轮跳过：top1=%.3f ≥ %.2f 高分快速通道", i, top1, high)
            critic_log.append({"iter": i, "action": "skip", "top1": top1})
            break

        verdict = _critic_judge(question, best_r)
        sufficient = bool(verdict.get("sufficient", True))
        missing = (verdict.get("missing") or "").strip()
        critic_log.append({"iter": i, "action": "judge", "top1": top1,
                           "sufficient": sufficient, "missing": missing})
        logger.info("[Critic] 第%d轮评估: top1=%.3f sufficient=%s missing=%s",
                    i, top1, sufficient, missing or "-")
        if sufficient:
            break

        alt = _generate_alternative_query(question, best_q, missing or "与问题直接相关的资料")
        if not alt or alt == best_q:
            critic_log.append({"iter": i, "action": "no_alt"})
            logger.info("[Critic] 第%d轮未能生成不同的替代查询，结束", i)
            break

        candidates = hybrid_search(alt, kb_id)
        new_results = _rerank(alt, candidates)
        new_top1 = new_results[0].score if new_results else 0.0
        critic_log.append({"iter": i, "action": "retry", "alt_query": alt, "new_top1": new_top1})
        logger.info("[Critic] 第%d轮重检索: 「%s」 top1=%.3f（原 %.3f）", i, alt, new_top1, top1)
        if new_top1 > top1:
            best_q, best_r = alt, new_results

    return best_q, best_r, critic_log


def _build_sources(results: list[SearchResult]) -> list[dict]:
    results = _dedupe_results(results)
    return [
        {"name": r.source, "id": r.doc_id, "score": r.score}
        for r in results
    ] if RAG_CONFIG.get("enable_source_score", True) else [
        {"name": r.source, "id": r.doc_id}
        for r in results
    ]


def _build_rag_messages(history: list[ChatMessage], results: list[SearchResult], question: str,
                        profile: dict = None) -> ChatPromptTemplate:
    """拼 RAG 提示词：系统提示 + 参考资料 + 历史 + 最新问题（系统提示与拒答话术随领域）
    【v3.16】系统提示/历史改用 Message 对象直装，不走模板插值——知识库内容与历史消息
    含 {xxx}（JSON 示例、代码片段）时会被 langchain 当占位符解析导致调用崩溃
    （Critic 提示词同款坑 v3.3 已修，本处补齐）；仅最后一条 human 保留 {question} 占位符。"""
    profile = profile or get_profile()
    sys_prompt = profile.get("system_prompt", SYSTEM_PROMPT)
    refuse = profile.get("refuse_answer", REFUSE_ANSWER)
    results = _dedupe_results(results)
    context = "\n".join(
        [f"参考资料{i+1}（来源：{r.source}）：{r.content}" for i, r in enumerate(results)]
    )
    system_text = sys_prompt + f"""
请严格基于参考资料和历史对话回答问题，遵守以下规则：
1. 参考资料：{context}
2. 如果问题与本领域完全无关（其他领域闲聊、写代码、做菜、天气等日常请求），即使参考资料里出现了相关字样，也必须直接返回：{refuse}
3. 参考资料里没有的内容不要编造，直接返回兜底话术
4. 不要重复介绍同一技能或同一段内容；如果答案已经说清楚，直接结束。
"""
    messages = [SystemMessage(content=system_text)]
    for msg in history:
        if msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
        else:
            messages.append(HumanMessage(content=msg.content))
    messages.append(("human", "{question}"))
    return ChatPromptTemplate.from_messages(messages)


def _llm_error_event(e: Exception) -> dict:
    """【v3.10】把大模型异常归类为用户可读的降级提示事件：429 限流 / 5xx / 超时文案区分。
    供流式生成失败时推送 {"type": "error", ...}，前端据此停止 loading 并渲染错误气泡。"""
    text = str(e)
    low = text.lower()
    if "429" in text or "rate limit" in low or "1302" in text or "1305" in text:
        # 智谱免费档常见：HTTP 429 / 账户速率限制(1302) / 模型访问量过大(1305)
        return {"type": "error", "code": "rate_limited", "message": "模型服务繁忙，请稍后再试~"}
    if "timeout" in low or "timed out" in low:
        return {"type": "error", "code": "timeout", "message": "模型响应超时，请稍后再试~"}
    if "500" in text or "502" in text or "503" in text or "server error" in low:
        return {"type": "error", "code": "server_error", "message": "模型服务开小差了，请稍后再试~"}
    return {"type": "error", "code": "unavailable", "message": "模型服务暂时不可用，请稍后再试~"}


def _call_llm_with_retry(prompt, inputs, max_retry=1, model=None, fallback_used=None):
    """大模型调用带重试：失败自动重试，最终失败返回友好提示（真实错误已写入日志）
    【v3.11】重试从 2 次收紧为 1 次、退避固定 1.5s——SDK 自动重试与上层重试叠加
    曾把限流最坏耗时拖到 2 分钟级；现在最多 1.5s 后快速失败。
    【v3.18】主模型重试耗尽且属限流/模型过载类错误时，自动切换 LLM_FALLBACK_MODEL
    兜底模型再试一次（同账号同密钥只换模型名）；连接失败/超时换模型无意义，不切。
    :param model: 指定环节实例（get_llm_rewrite()/get_llm_critic()），默认用最终答案生成实例
    :param fallback_used: 传空 list 可回收兜底标记（接管成功时 append(True)），供审计打标"""
    last_err = None
    for i in range(max_retry + 1):
        try:
            chain = prompt | (model or get_llm())
            response = chain.invoke(inputs)
            return response.content
        except Exception as e:
            last_err = e
            logger.error("大模型调用失败(第%d次) 输入=%s 错误=%s", i + 1, inputs, e)
            if i == max_retry:
                break
            time.sleep(1.5)  # 【v3.11】固定短退避重试一次，再失败立即快速结束（原递增退避 3s/6s）

    fb_model = LLM_CONFIG.get("fallback_model")
    err_code = _llm_error_event(last_err)["code"] if last_err else None
    if fb_model and err_code in ("rate_limited", "server_error"):
        logger.warning("[兜底] 主模型%s，切换备用模型 %s", err_code, fb_model)
        try:
            chain = prompt | get_llm_fallback()
            response = chain.invoke(inputs)
            if fallback_used is not None:
                fallback_used.append(True)
            logger.info("[兜底] 备用模型 %s 接管成功", fb_model)
            return response.content
        except Exception as e2:
            logger.error("[兜底] 备用模型 %s 也失败: %s", fb_model, e2)
    if fallback_used is not None and not fallback_used:
        fallback_used.append(False)  # 【v3.19】主模型与兜底全部失败的标记（调用方据此不缓存该"回答"）
    return "抱歉，当前服务有点忙，请稍后再试~"


def _auto_create_ticket(session_id: str, question: str, rewritten: str,
                        results: list[SearchResult], kb_id: str) -> str:
    """v3.5 工单闭环：低置信兜底时自动建人工工单，返回要附在回答里的工单提示（失败返回空串，不阻塞回答）"""
    if not TICKET_CONFIG["enabled"] or not TICKET_CONFIG["auto_create"]:
        return ""
    try:
        from services import ticket_service
        if ticket_service.has_recent_duplicate(session_id, question, TICKET_CONFIG["cooldown_minutes"]):
            return ""
        top1 = results[0].score if results else 0.0
        ticket_id = ticket_service.create_ticket(question, rewritten, top1, kb_id, session_id)
        logger.info("[工单] 低置信兜底自动建单: %s question=%s top1=%.3f", ticket_id, question[:30], top1)
        return f"\n\n（你的问题已记录为人工工单 **{ticket_id}**，客服补充答案后同类问题我就能直接回答）"
    except Exception as e:
        logger.warning("工单自动创建失败(不影响回答): %s", e)
        return ""


def chat_single_turn(session_id: str, question: str, kb_id: str = None) -> tuple[str, list[dict], list[ChatMessage]]:
    """单轮问答主流程（v3.0 管线），返回 (答案, 来源, 最新历史)。
    v3.6：kb_id 即领域键——领域配置（提示词/兜底话术/关键词规则/改写提示词）运行时跟随 kb_id"""
    kb_id = kb_id or DEFAULT_KB_ID
    P = get_profile(kb_id)
    start_time = time.time()

    # 1. 敏感词校验
    is_sensitive, filtered_q = filter_sensitive(question)
    if is_sensitive:
        answer = "你的问题包含敏感词，请重新提问~"
        log_qa(session_id, question, question, [], answer, (time.time() - start_time) * 1000, kb_id, "sensitive_block")
        return answer, [], load_history(session_id)

    # 2. 自定义关键词规则优先（随领域）
    for keyword, reply in (P.get("custom_rules") or {}).items():
        if keyword in question:
            history = append_history(session_id, question, reply)
            return reply, [], history

    # 官方结构化数据优先（游戏领域专属：英雄/武器/地图数据文件）；其他领域跳过
    if kb_id == "valorant":
        official_answer = answer_official_data_query(question)
        if official_answer:
            history = append_history(session_id, question, official_answer)
            return official_answer, [], history

    question_for_prompt = replace_aliases_with_official(question)

    # 3. 加载历史
    history = load_history(session_id)

    # 3.5 语义缓存（v3.19）：相似问题命中直接复用答案，跳过 改写→检索→重排→生成 全程；
    # 只缓存"带来源的成功回答"，兜底/失败回答不入缓存；知识库变更经 epoch 信号自动失效
    cache = get_semantic_cache() if CACHE_CONFIG["enabled"] else None
    cached = cache.lookup(question_for_prompt, kb_id) if cache else None
    if cached:
        answer, sources = cached["answer"], cached["sources"]
        history = append_history(session_id, question, answer)
        log_qa(session_id, question, question_for_prompt, sources, answer,
               (time.time() - start_time) * 1000, kb_id, "cache_hit")
        return answer, sources, history

    # 4~6. 三级检索管线：改写 → 混合召回 → 重排（+ v3.3 质量自评 Critic）
    rewritten, results = _retrieve(question_for_prompt, history, kb_id, profile=P)
    rewritten, results, critic_log = _critic_refine(question_for_prompt, rewritten, results, kb_id)
    pipeline_desc = ("hybrid+rerank" if RAG_CONFIG.get("enable_rerank") else "hybrid") \
        if RAG_CONFIG.get("enable_hybrid_search") else "vector"
    retries = sum(1 for c in critic_log if c.get("action") == "retry")
    if retries:
        pipeline_desc += f"+critic{retries}"

    # 7. 低置信兜底（v3.5：自动生成人工工单，闭环入口；兜底话术随领域）
    fallback_flag = []
    if _should_fallback(results):
        answer = P["fallback_answer"] + _auto_create_ticket(session_id, question, rewritten, results, kb_id)
        sources = []
    else:
        # 8. 拼提示词（加拒答规则），带重试调用大模型（【v3.18】限流自动切兜底模型）
        prompt = _build_rag_messages(history, results, question_for_prompt, profile=P)
        answer = _call_llm_with_retry(prompt, {"question": question_for_prompt},
                                      fallback_used=fallback_flag)
        if fallback_flag:
            pipeline_desc += "+fallback"
        sources = _build_sources(results)

    # 8.5 生成成功才写缓存：主模型或兜底模型给出的有效回答可缓存；
    # 全部失败（fallback_flag=[False]，返回 canned 文案）与低置信兜底（sources=[]）不入缓存
    if cache and fallback_flag != [False]:
        cache.put(question_for_prompt, kb_id, answer, sources)

    # 9. 更新历史（持锁）+ 审计留痕
    history = append_history(session_id, question, answer)
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
      {"type": "error",   "code": "...", "message": "..."}  # v3.10：生成失败降级提示，推送后流直接结束
    """
    kb_id = kb_id or DEFAULT_KB_ID
    P = get_profile(kb_id)
    start_time = time.time()

    # 敏感词 / 关键词规则 / 兜底：这些场景没有流式生成过程，一次性给出
    is_sensitive, _ = filter_sensitive(question)
    if is_sensitive:
        answer = "你的问题包含敏感词，请重新提问~"
        yield {"type": "token", "delta": answer}
        yield {"type": "done", "answer": answer, "history": [m.model_dump() for m in load_history(session_id)]}
        return

    for keyword, reply in (P.get("custom_rules") or {}).items():
        if keyword in question:
            history = append_history(session_id, question, reply)
            yield {"type": "token", "delta": reply}
            yield {"type": "done", "answer": reply, "history": [m.model_dump() for m in history]}
            return

    if kb_id == "valorant":
        official_answer = answer_official_data_query(question)
        if official_answer:
            history = append_history(session_id, question, official_answer)
            yield {"type": "token", "delta": official_answer}
            yield {"type": "done", "answer": official_answer, "history": [m.model_dump() for m in history]}
            return

    question_for_prompt = replace_aliases_with_official(question)
    history = load_history(session_id)

    # 语义缓存（v3.19）：命中直接推来源+答案，毫秒级返回（跳过检索与生成全程）
    cache = get_semantic_cache() if CACHE_CONFIG["enabled"] else None
    cached = cache.lookup(question_for_prompt, kb_id) if cache else None
    if cached:
        answer, sources = cached["answer"], cached["sources"]
        yield {"type": "sources", "sources": sources}
        yield {"type": "token", "delta": answer}
        history = append_history(session_id, question, answer)
        log_qa(session_id, question, question_for_prompt, sources, answer,
               (time.time() - start_time) * 1000, kb_id, "cache_hit")
        yield {"type": "done", "answer": answer, "history": [m.model_dump() for m in history]}
        return

    rewritten, results = _retrieve(question_for_prompt, history, kb_id, profile=P)
    rewritten, results, _critic_log = _critic_refine(question_for_prompt, rewritten, results, kb_id)
    pipeline_desc = ("hybrid+rerank" if RAG_CONFIG.get("enable_rerank") else "hybrid") \
        if RAG_CONFIG.get("enable_hybrid_search") else "vector"

    if _should_fallback(results):
        sources = []
        yield {"type": "sources", "sources": sources}
        answer = P["fallback_answer"] + _auto_create_ticket(session_id, question, rewritten, results, kb_id)
        for ch in answer:
            yield {"type": "token", "delta": ch}
    else:
        sources = _build_sources(results)
        yield {"type": "sources", "sources": sources}
        prompt = _build_rag_messages(history, results, question_for_prompt, profile=P)
        chain = prompt | get_llm()
        parts = []
        try:
            for chunk in chain.stream({"question": question_for_prompt}):
                delta = chunk.content or ""
                if delta:
                    parts.append(delta)
                    yield {"type": "token", "delta": delta}
            answer = "".join(parts)
        except Exception as e:
            # v3.10：LLM 生成失败（429/5xx/超时/连接失败）不再把"抱歉"文案伪装成回答，
            # 改推结构化 error 事件让前端停止 loading 并显示错误气泡，杜绝无限转圈
            logger.error("流式大模型调用失败: %s", e)
            error_event = _llm_error_event(e)
            # 【v3.18】尚未产出任何 token 且属限流/模型过载类错误 → 备用模型重新流式生成；
            # 已有部分 token 时换模型续写会前后不一致，维持 error 事件收尾
            fb_model = LLM_CONFIG.get("fallback_model")
            if not parts and fb_model and error_event["code"] in ("rate_limited", "server_error"):
                logger.warning("[兜底] 主模型%s，流式切换备用模型 %s", error_event["code"], fb_model)
                try:
                    chain = prompt | get_llm_fallback()
                    for chunk in chain.stream({"question": question_for_prompt}):
                        delta = chunk.content or ""
                        if delta:
                            parts.append(delta)
                            yield {"type": "token", "delta": delta}
                    answer = "".join(parts)
                    pipeline_desc += "+fallback"
                except Exception as e2:
                    logger.error("[兜底] 备用模型 %s 也失败: %s", fb_model, e2)
                    error_event = _llm_error_event(e2)
                    log_qa(session_id, question, rewritten, sources,
                           f"[生成失败:{error_event['code']}] {error_event['message']}",
                           (time.time() - start_time) * 1000, kb_id, pipeline_desc + "+llm_error")
                    yield error_event
                    return  # 错误后正常结束流：不发残缺答案，也不把失败文案写进对话历史
            else:
                log_qa(session_id, question, rewritten, sources,
                       f"[生成失败:{error_event['code']}] {error_event['message']}",
                       (time.time() - start_time) * 1000, kb_id, pipeline_desc + "+llm_error")
                yield error_event
                return  # 错误后正常结束流：不发残缺答案，也不把失败文案写进对话历史

    # 生成成功（未被错误分支提前 return）才写缓存；兜底回答 sources=[] 会被 put 内部忽略
    if cache:
        cache.put(question_for_prompt, kb_id, answer, sources)
    history = append_history(session_id, question, answer)
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
