# 【W8-卡1】Agent 路由：多跳/对比/动作类问题的自主循环入口
# 样板与防线对齐 chat_router：限流 + kb 白名单 + 同步 def（LLM 调用走线程池，不阻塞事件循环）
from fastapi import APIRouter, HTTPException, Request

from config.settings import resolve_kb_id
from schemas.models import AgentChatRequest, AgentChatResponse, ApiResponse
from utils.rate_limit import check_chat_rate_limit, RateLimitExceeded
from routers.chat_router import _client_ip  # 【W8-卡1】同一套真实 IP 解析（Nginx 反代后限流口径一致）

agent_router = APIRouter()


@agent_router.post("/chat", response_model=ApiResponse, summary="Agent 模式问答（多步自主检索）")
def agent_chat(req: AgentChatRequest, request: Request):
    from services.agent.loop import run_agent
    try:
        check_chat_rate_limit(_client_ip(request))  # 与 /api/chat 同款限流：Agent 每问消耗多次 LLM 调用，更不能裸奔
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=e.message)
    resolve_kb_id(req.kb_id)  # kb 白名单：非法 400 / 未知 404，与 Workflow 同款防线
    result = run_agent(req.question, kb_id=req.kb_id)
    resp = AgentChatResponse(
        answer=result["answer"],
        sources=result["sources"],
        steps=result["steps"],
        degraded=result["degraded"],
    )
    return ApiResponse(data=resp.model_dump())
