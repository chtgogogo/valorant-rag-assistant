# 【共用文件】统一数据结构，不许改
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
# 所有接口统一返回格式，不许改
class ApiResponse(BaseModel):
    code: int = 200  # 200=成功，500=失败
    msg: str = "success"
    data: Optional[Dict | List] = None
# 文档块结构（文档解析切分后的标准格式，向量入库直接使用）
class DocumentChunk(BaseModel):
    content: str  # 切分后的文本内容
    metadata: Dict  # 固定包含doc_id、doc_name、kb_id
# 检索结果结构（检索服务返回的标准格式，对话服务直接使用）
class SearchResult(BaseModel):
    content: str  # 匹配到的文本
    source: str  # 来源文档名
    score: float  # 排序分数（rerank 分 / RRF 分 / 余弦相似度，随管线阶段变化）
    doc_id: str  # 文档ID
    dense_score: Optional[float] = None  # 向量余弦相似度（混合检索保留，供兜底阈值判断）
    rerank_degraded: bool = False  # 【v3.16】重排失败降级时置 True：score 已退回召回量纲，兜底判定应改用 dense_score 余弦阈值
    version: Optional[int] = None  # 【v3.21】所引文档块的版本号（同名文档重复上传递增；旧数据无此字段为 None）
    chunk_id: Optional[str] = None  # 【v3.25】块唯一 id（chroma 块 id）：RRF 融合键与溯源锚点；旧数据为 None
# 对话消息结构（多轮历史持久化格式）
class ChatMessage(BaseModel):
    role: str  # 只能是 user / assistant
    content: str  # 消息内容
# 对话请求结构（前端 → 对话服务）
class ChatRequest(BaseModel):
    # 【v3.20】session_id 双保险之一：模型层 pattern 拒绝路径穿越字符
    # （服务层 _get_history_path 入口还有同款白名单校验，两层防线独立生效）
    session_id: str = Field(pattern=r"^[\w\-]{1,64}$")  # 会话ID，隔离不同用户
    # 【v3.24】模型层 1000 字硬兜底（防超大包）；可调软上限 MAX_QUESTION_CHARS 在服务层校验
    question: str = Field(max_length=1000)  # 用户问题
    kb_id: Optional[str] = "valorant"  # 默认无畏契约知识库
# 对话返回结构（对话服务 → 前端）
class ChatResponse(BaseModel):
    answer: str  # Markdown格式的回答
    sources: List[Dict]  # 引用来源：[{"name": "文档名", "id": "文档id"}]
    history: List[ChatMessage]  # 最新对话历史
    route: Optional[Dict] = None  # 【W8-卡3】路由决策 meta {"route": "agent"/"workflow", "reason": "…"}；门槛拦截/缓存命中等无决策轮为 null（可解释性，前端可忽略）
    pending_proposals: List[Dict] = []  # 【W8-卡5.2】Agent 分岔轮待人工确认的知识库写入方案（仅 Agent 轮非空；确认走 /api/agent/kb-write/confirm）
# 【W8-卡1】Agent 请求结构（复用 ChatRequest 的三层防线：pattern/长度/kb白名单）
class AgentChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^[\w\-]{1,64}$")  # 会话ID，隔离不同用户
    question: str = Field(max_length=1000)  # 用户问题
    kb_id: Optional[str] = "valorant"  # 默认无畏契约知识库
# Agent 单步轨迹（前端逐行渲染"正在检索…"的素材，卡 5 启用）
class AgentStep(BaseModel):
    step: int  # 第几步
    type: str  # tool_call / final
    tool: Optional[str] = None  # 工具名
    args: Optional[Dict] = None  # 工具参数
    summary: str  # 结果摘要（截断）
    ok: Optional[bool] = None  # 工具是否执行成功
    elapsed_ms: int = 0  # 本步耗时
# Agent 返回结构（对话服务 → 前端）
class AgentChatResponse(BaseModel):
    answer: str  # Markdown格式的最终答案
    sources: List[Dict]  # 引用来源：[{"name": "文档名", "id": "文档id"}]
    steps: List[AgentStep]  # 执行轨迹
    degraded: bool = False  # 是否触发了未完成/降级（卡 2 起有真实降级路径）
    pending_proposals: List[Dict] = []  # 【W8-卡4】待人工确认的知识库写入方案（凭据 proposal_id，确认走 /api/agent/kb-write/confirm）
# 【W8-卡4】知识库写入方案确认请求（凭据 = proposal_id + X-Admin-Key 管理密码双因子）
class KbWriteConfirmRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=64)  # propose_kb_write 生成的方案凭据
