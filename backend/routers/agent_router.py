# 【W8-卡1】Agent 路由：多跳/对比/动作类问题的自主循环入口
# 样板与防线对齐 chat_router：限流 + kb 白名单 + 同步 def（LLM 调用走线程池，不阻塞事件循环）
# 【W8-卡5】新增 /chat/stream：SSE 流式轨迹（D2 拍板：事件协议 tool_call/tool_result/
#   final_answer 通用化 + degraded 降级说明，未来加新工具/新事件前端按未知类型兜底零改动）
import json
import queue
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

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
    result = run_agent(req.question, kb_id=req.kb_id, session_id=req.session_id)
    resp = AgentChatResponse(
        answer=result["answer"],
        sources=result["sources"],
        steps=result["steps"],
        degraded=result["degraded"],
    )
    return ApiResponse(data=resp.model_dump())


def _sse(event_type: str, payload: dict) -> str:
    """一条 SSE 帧：event 行 + data 行（JSON），与 /api/chat/stream 同款格式"""
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@agent_router.post("/chat/stream", summary="Agent 模式问答 · SSE 流式轨迹（tool_call/tool_result/final_answer/degraded + done）")
def agent_chat_stream(req: AgentChatRequest, request: Request):
    from services.agent.loop import run_agent
    try:
        check_chat_rate_limit(_client_ip(request))
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=e.message)
    resolve_kb_id(req.kb_id)

    events: queue.Queue = queue.Queue()
    _SENTINEL = object()  # 流结束哨兵

    def worker():
        """线程里跑同步 Agent 循环，事件经回调推入队列；结束/异常都保证流会关闭"""
        try:
            result = run_agent(req.question, kb_id=req.kb_id,
                               session_id=req.session_id, on_event=events.put)
            events.put({"type": "done", "answer": result["answer"],
                        "sources": result["sources"],
                        "degraded": result["degraded"]})
        except Exception as e:  # Agent 本体异常：结构化 error 事件（前端错误气泡），不留悬挂流
            events.put({"type": "error", "message": f"Agent 执行失败：{e}"})
        finally:
            events.put(_SENTINEL)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            ev = events.get()
            if ev is _SENTINEL:
                break
            yield _sse(ev.pop("type"), ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})  # Nginx 反代不缓冲，轨迹才逐行到得了前端
