# 【W8-卡3】规则路由器：单跳问题走 Workflow（现有链路），多跳/对比/统计/操作类走 Agent 循环
# ------------------------------------------------------------
# 拍板依据：技术决策清单 D3 = 纯规则起步（零成本零延迟、百分百可解释、坏了好修），
#   拿不准默认 Workflow；误路由样本攒够后评测再决定是否升级分类器。
# 可解释性：每个路由决策附 reason（命中了哪条规则的哪个词），接线后写入响应 meta。
# 规则纪律：只上高置信信号——误路由到 Agent 的代价是慢+贵，误留 Workflow 只损失部分质量；
#   单词"还是/有哪些/几个"这类单跳常见词不上表（"捷风有几个技能"是单跳事实题不是统计），
#   评测集 W05/W06 钉住这些边界，改动规则前先看对应用例。
# 接线说明：本模块是纯函数，不 import 模型/检索；chat 链路接入（响应 meta 携带路由结果）
#   与 Agent 分支调用 /api/agent/chat 属卡 3 接线部分，待卡 1 产出后完成。
# ------------------------------------------------------------
import re
from dataclasses import dataclass

ROUTE_AGENT = "agent"
ROUTE_WORKFLOW = "workflow"

# 规则表（单一事实源）：分组 / 关键词 / 理由描述；顺序即优先级，命中即返回
_RULES: list[tuple[str, str, str]] = [
    ("对比", "区别", "多跳对比"),
    ("对比", "差异", "多跳对比"),
    ("对比", "不同", "多跳对比"),
    ("对比", "对比", "多跳对比"),
    ("对比", "相比", "多跳对比"),
    ("对比", "哪个更", "多跳对比"),
    ("对比", "哪种更", "多跳对比"),
    ("对比", "谁更", "多跳对比"),
    ("对比", "各自", "多跳对比"),
    ("统计", "多少个", "统计聚合"),
    ("统计", "几种", "统计聚合"),
    ("统计", "总共", "统计聚合"),
    ("统计", "一共", "统计聚合"),
    ("统计", "排名", "统计聚合"),
    ("统计", "最多", "统计聚合"),
    ("统计", "最少", "统计聚合"),
    ("操作", "帮我", "操作/复合指令"),
    ("操作", "并且", "操作/复合指令"),
    ("操作", "然后再", "操作/复合指令"),
    ("操作", "同时还要", "操作/复合指令"),
]

# "X更Y/A还是B"类弱信号用正则收紧：比较词前后须有实词，避免命中"还是说/是不是"
# "多少个/种/把…" 数量统计弱信号：带量词才聚合，"多少钱"（单跳价格）不命中
_WEAK_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("对比", re.compile(r"(哪个|哪种|谁).{0,4}(好|强|适合|快|高|大)"), "多跳对比(弱信号)"),
    ("统计", re.compile(r"多少(个|种|把|款|名|条)"), "统计聚合(弱信号)"),
]


@dataclass
class RouteDecision:
    route: str  # 'agent' / 'workflow'
    reason: str  # 命中说明（可解释性，接线后写进响应 meta）


def route_query(question: str) -> RouteDecision:
    """规则路由：命中高置信意图信号 → Agent；拿不准/未命中 → Workflow（拍板 D3）"""
    q = (question or "").strip()
    if not q:
        return RouteDecision(ROUTE_WORKFLOW, "空问题默认走 Workflow")
    for group, kw, desc in _RULES:
        if kw in q:
            return RouteDecision(ROUTE_AGENT, f"命中[{group}]·{desc}规则：'{kw}'")
    for group, pat, desc in _WEAK_PATTERNS:
        if pat.search(q):
            return RouteDecision(ROUTE_AGENT, f"命中[{group}]·{desc}")
    return RouteDecision(ROUTE_WORKFLOW, "未命中 Agent 规则，默认走 Workflow")
