# 【共用文件】统一数据结构，不许改
from pydantic import BaseModel
from typing import List, Optional, Dict
# 所有接口统一返回格式，不许改
class ApiResponse(BaseModel):
    code: int = 200  # 200=成功，500=失败
    msg: str = "success"
    data: Optional[Dict | List] = None
# 文档块结构（分工2切完必须返回这个格式，分工3直接用）
class DocumentChunk(BaseModel):
    content: str  # 切分后的文本内容
    metadata: Dict  # 固定包含doc_id、doc_name、kb_id
# 检索结果结构（分工3检索必须返回这个格式，分工4直接用）
class SearchResult(BaseModel):
    content: str  # 匹配到的文本
    source: str  # 来源文档名
    score: float  # 相似度分数
    doc_id: str  # 文档ID
# 对话消息结构（分工4存历史必须用这个格式）
class ChatMessage(BaseModel):
    role: str  # 只能是 user / assistant
    content: str  # 消息内容
# 对话请求结构（前端传给分工4）
class ChatRequest(BaseModel):
    session_id: str  # 会话ID，隔离不同用户
    question: str  # 用户问题
    kb_id: Optional[str] = "valorant"  # 默认无畏契约知识库
# 对话返回结构（分工4返回给前端）
class ChatResponse(BaseModel):
    answer: str  # Markdown格式的回答
    sources: List[Dict]  # 引用来源：[{"name": "文档名", "id": "文档id"}]
    history: List[ChatMessage]  # 最新对话历史