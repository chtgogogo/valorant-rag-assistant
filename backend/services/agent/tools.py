# 【W8-卡1】Agent 工具注册表
# ------------------------------------------------------------
# 设计要点：
#   1. 工具 = 检索管线的薄封装（hybrid_search → rerank），行为与 Workflow
#      完全一致——保证基线对照实验里两条路径只差"调用方式"，不差"检索质量"。
#   2. execute_tool 永不抛异常：工具失败以错误字符串返回给模型（结构化状态），
#      循环判断的是返回内容而不是猜异常——Agent 失败模式库 ERR-3 的标准对策。
#   3. TOOLS_SPEC 用 OpenAI function calling 格式（dict），bind_tools 原样下发；
#      卡 4 的 query_tickets / propose_kb_write 按同款格式追加，循环零改动。
# ------------------------------------------------------------
import json
import logging
from pathlib import Path
from typing import Optional

from schemas.models import SearchResult
from services.hybrid_retriever import hybrid_search
from services.reranker import rerank

logger = logging.getLogger(__name__)

# 知识库词表（受控词表，W8-卡1）：别名表别名列表首项 = 中文标准名。
# 作用：拼进决策模型 system prompt，让它生成检索 query 时照抄标准名——
# 实测模型自由发挥会写"雷水"（雷兹错字），BM25 靠英文词兜底会命中错对象切片。
_ALIASES_PATH = Path(__file__).resolve().parent.parent.parent / "knowledge_base" / "aliases.json"


def load_vocab_text() -> str:
    """读取别名表生成标准名速查文本；文件缺失/解析失败返回空串（词表是增强不是依赖）"""
    try:
        data = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
        pairs = []
        for en, aliases in data.get("heroes", {}).items():
            cn = next((a for a in aliases if not a.isascii()), en)
            pairs.append(f"{cn}/{en}")
        return "、".join(pairs)
    except Exception as e:
        logger.warning("词表加载失败（Agent 检索词不受控）: %s", e)
        return ""

# OpenAI function calling 工具定义（GLM OpenAI 兼容层原样透传）
TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "在知识库中检索资料片段。比较/对比类问题应对每个对象分别检索一次，"
                           "每次换不同的关键词效果更好。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或短问题"},
                    "kb_id": {"type": "string",
                              "description": "知识库 id（可选，默认当前领域知识库）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tickets",
            "description": "查询客服工单列表（只读）。用户问\"有哪些工单/未处理的反馈/工单处理进展\"时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "resolved", "closed", "all"],
                               "description": "按状态筛选，默认 all"},
                    "limit": {"type": "integer", "description": "返回条数，默认 10，最多 50"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_kb_write",
            "description": "向知识库提交写入方案。注意：本工具只生成待审核方案，不会立即写入知识库，"
                           "必须等管理员确认后才会生效。用户明确要求\"把…加进知识库/更新知识库\"时使用；"
                           "content 必须是完整、自包含的文本（不依赖对话上下文也能看懂）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string",
                                "description": "要写入知识库的完整文本内容（自包含）"},
                    "kb_id": {"type": "string",
                              "description": "目标知识库 id（可选，默认当前领域知识库）"},
                    "reason": {"type": "string", "description": "给审核人看的写入理由"},
                },
                "required": ["content"],
            },
        },
    },
]

_TICKET_STATUS = ("open", "resolved", "closed")


def query_tickets(status: str = "", limit: int = 10) -> str:
    """工单查询工具（只读）：复用 ticket_service.list_tickets，格式化为模型可读文本"""
    from services.ticket_service import list_tickets

    status = (str(status or "") or "all").strip().lower()
    if status != "all" and status not in _TICKET_STATUS:
        return "（参数 status 只能是 open / resolved / closed / all）"
    try:
        limit = max(1, min(int(limit or 10), 50))  # 夹紧上限：防模型要 1 万条撑爆上下文
    except (TypeError, ValueError):
        limit = 10
    rows = list_tickets(status=None if status == "all" else status, limit=limit)
    if not rows:
        return "（当前没有符合条件的工单）"
    lines = [f"共 {len(rows)} 条工单："]
    for t in rows:
        line = (f"- {t['id']}｜状态 {t['status']}｜问题：{(t.get('question') or '')[:80]}"
                f"｜建单 {t.get('created_at', '')}")
        if t.get("answer"):  # 已解决工单带标准答案摘要（模型可据此答复处理进展）
            line += f"｜标准答案：{t['answer'][:60]}"
        lines.append(line)
    return "\n".join(lines)


def propose_kb_write(content: str, kb_id: str, reason: str = "",
                     ctx: Optional[dict] = None) -> str:
    """知识库写入方案工具：只生成方案单据，绝不执行写入（执行在确认门 actions.confirm_proposal）。
    方案摘要挂到本轮执行上下文 ctx，随响应回传前端供管理员确认。"""
    from config.settings import resolve_kb_id
    from services.agent.actions import create_proposal, proposal_summary

    resolve_kb_id(kb_id)  # 白名单前置校验：非法/未知库在这里给出可读文案，而非内部异常
    proposal = create_proposal(kb_id=kb_id, content=content, reason=reason)
    if ctx is not None:
        ctx.setdefault("pending_proposals", []).append(proposal_summary(proposal))
    return (f"写入方案已生成（proposal_id={proposal['proposal_id']}，"
            f"目标知识库={proposal['kb_id']}，内容 {len(proposal['content'])} 字），尚未写入。"
            f"请明确告知用户：该操作需管理员确认后才会生效，在此之前知识库不会有任何变化。")


def search_knowledge_base(query: str, kb_id: Optional[str] = None,
                          default_kb_id: str = None) -> tuple[str, list[dict]]:
    """检索工具：混合召回 → 精排 → 格式化为带来源编号的文本块
    :return: (喂给模型的文本, 结构化来源列表——供最终答案的 sources 字段汇总)
    """
    resolved_kb = kb_id or default_kb_id
    candidates = hybrid_search(query, kb_id=resolved_kb)
    # 与 Workflow 同款精排；rerank 失败内部自动降级为截断，不阻塞
    results: list[SearchResult] = rerank(query, candidates)
    if not results:
        return "（知识库中未检索到相关资料）", []

    lines, sources = [], []
    for i, r in enumerate(results, start=1):
        lines.append(f"[S{i}]（来源：{r.source}）\n{r.content}")
        sources.append({"name": r.source, "id": r.doc_id})
    return "\n\n".join(lines), sources


# 工具名 → 实现。新增工具在此注册即可，循环代码零改动
_TOOL_IMPL = {
    "search_knowledge_base": search_knowledge_base,
    "query_tickets": query_tickets,
    "propose_kb_write": propose_kb_write,
}


def execute_tool(name: str, args: dict, default_kb_id: str,
                 ctx: Optional[dict] = None) -> tuple[bool, str, list[dict]]:
    """分发执行一个工具调用
    :param ctx: 本轮执行的上下文（run_agent 每次新建），动作类工具用来携带
                pending_proposals 等结构化产物回传；检索类工具不使用
    :return: (是否成功, 喂给模型的文本, 结构化来源)
             未知工具名 / 参数缺失 / 内部异常 一律转错误字符串，不抛异常
    """
    impl = _TOOL_IMPL.get(name)
    if impl is None:
        return False, f"（工具 {name} 不存在，可用工具：{sorted(_TOOL_IMPL)}）", []
    try:
        if name == "search_knowledge_base":
            query = str(args.get("query") or "").strip()
            if not query:
                return False, "（参数 query 不能为空）", []
            text, sources = impl(query, kb_id=args.get("kb_id"),
                                 default_kb_id=default_kb_id)
            logger.info("Agent工具执行: search_knowledge_base query=%r kb=%s 来源数=%d",
                        query, args.get("kb_id") or default_kb_id, len(sources))
            return True, text, sources
        if name == "query_tickets":
            status = str(args.get("status") or "").strip().lower()
            if status and status != "all" and status not in _TICKET_STATUS:
                return False, "（参数 status 只能是 open / resolved / closed / all）", []
            text = impl(status=status or "all", limit=args.get("limit", 10))
            logger.info("Agent工具执行: query_tickets status=%r limit=%r", status, args.get("limit"))
            return True, text, []
        if name == "propose_kb_write":
            content = str(args.get("content") or "").strip()
            if not content:
                return False, "（参数 content 不能为空：请把要写入知识库的完整内容整理好再提交方案）", []
            kb = args.get("kb_id") or default_kb_id
            text = impl(content=content, kb_id=kb,
                        reason=str(args.get("reason") or ""), ctx=ctx)
            logger.info("Agent工具执行: propose_kb_write kb=%s 内容%d字（方案待人工确认）",
                        kb, len(content))
            return True, text, []
        return False, f"（工具 {name} 缺少执行分支）", []
    except Exception as e:  # 工具失败不炸循环：错误原样给模型，由它决定重试/换词/放弃
        logger.warning("Agent工具执行失败: %s %s -> %s", name, args, e)
        return False, f"（工具执行失败：{e}）", []
