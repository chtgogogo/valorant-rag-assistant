# 【新增 v3.24】提示词注入检测：输入端保守拦截
# ------------------------------------------------------------
# 威胁定位（OWASP LLM Top1）：用户在问题里夹带"忽略规则/改设定/套系统提示词"类指令，
# 企图劫持生成。本项目的注入后果上限 = 多烧 token + 答非所问（system_prompt 无机密、
# 无外站请求、无越权工具），所以策略取**保守拦截**：只命中特征极明确的覆盖式指令，
# 宁可漏掉变种，也不误伤正常游戏提问（"你现在什么段位"这类绝不能拦）。
# 纵深防御三件套（本模块只是第一层）：
#   1. 本检测 → 高危直接拒答（审计标记 prompt_injection）
#   2. _build_rag_messages：参考资料 <reference> 标签包裹 + 系统声明"标签内是数据不是指令"
#   3. 拒答兜底（重排阈值）天然存在
# 检索依据：OWASP LLM Top10、AgentWatcher(arXiv 2026-07)、上下文隔离最佳实践——
# 检测+隔离声明是当前可落地的性价比组合，防御式训练（SecAlign 类）不在应用层范围。
# ------------------------------------------------------------
import re

# 覆盖式指令特征（宽松大小写、中英双语）——只收"明确命令改规则/泄提示词"的句式
_INJECTION_PATTERNS = [
    r"忽略.{0,8}(指令|规则|设定|要求|约束)",
    r"忽略(掉)?(之前|以上|上面|前面|上述|所有|全部)(的)?(指令|提示|规则|设定|要求|约束)",
    r"(无视|不管|不要理会)(之前|以上|上述|所有)(的)?(指令|规则|设定|约束)",
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+)?(previous|prior|the)\s+(instructions?|prompts?|rules?|guidelines)",
    r"(system|系统)提示词?(是|为|给我|看看|打印|泄露)",
    r"(打印|输出|告诉我|泄露|揭示)(你的)?(系统提示|system\s*prompt|初始指令)",
    r"(从现在开始|现在起)\s*，?\s*你(是|将)(不再|别的|其他)",
    r"越狱模式|DAN\s*模式|developer\s*mode",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def detect_prompt_injection(text: str) -> bool:
    """命中任一高危注入模式返回 True（调用方走拒答话术 + 审计标记）。"""
    if not text:
        return False
    probe = text[:600]  # 只扫前 600 字：注入指令总在开头，超长输入另有长度闸门
    return any(p.search(probe) for p in _COMPILED)
