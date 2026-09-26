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
    }
]


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


# 工具名 → 实现。新增工具（卡 4）在此注册即可，循环代码零改动
_TOOL_IMPL = {
    "search_knowledge_base": search_knowledge_base,
}


def execute_tool(name: str, args: dict, default_kb_id: str) -> tuple[bool, str, list[dict]]:
    """分发执行一个工具调用
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
        return False, f"（工具 {name} 缺少执行分支）", []
    except Exception as e:  # 工具失败不炸循环：错误原样给模型，由它决定重试/换词/放弃
        logger.warning("Agent工具执行失败: %s %s -> %s", name, args, e)
        return False, f"（工具执行失败：{e}）", []
