from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import json
from schemas.models import ApiResponse, ChatRequest, ChatResponse, ChatMessage
chat_router = APIRouter()
# 测试接口保留
@chat_router.get("/test", response_model=ApiResponse, summary="测试模块加载")
async def test_chat_module():
    return ApiResponse(msg="对话模块加载成功", data={"status": "测试通过"})
@chat_router.get("/test_llm", response_model=ApiResponse, summary="测试大模型调用")
async def test_llm(question: str = "你好，介绍一下你自己"):
    from services.chat_service import test_llm_call
    answer = test_llm_call(question)
    return ApiResponse(data={"answer": answer})
# 核心发送接口
@chat_router.post("/send", response_model=ApiResponse, summary="发送消息获取回答")
async def send_message(req: ChatRequest):
    from services.chat_service import chat_single_turn
    answer, sources, history = chat_single_turn(req.session_id, req.question, req.kb_id)
    resp = ChatResponse(answer=answer, sources=sources, history=history)
    return ApiResponse(data=resp.model_dump())

# 流式发送接口（v3.0 新增）：SSE 协议，答案逐字推送，前端边收边渲染
@chat_router.post("/stream", summary="流式发送消息（SSE）")
async def stream_message(req: ChatRequest):
    from services.chat_service import chat_single_turn_stream

    def event_stream():
        for event in chat_single_turn_stream(req.session_id, req.question, req.kb_id):
            event_type = event.pop("type")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# 清空对话接口
@chat_router.post("/clear", response_model=ApiResponse, summary="清空指定会话历史")
async def clear_chat(session_id: str):
    from services.chat_service import clear_history
    clear_history(session_id)
    return ApiResponse(msg="对话已清空")
# 回滚对话接口
@chat_router.post("/rollback", response_model=ApiResponse, summary="回滚到指定轮次")
async def rollback_chat(session_id: str, turn_index: int):
    from services.chat_service import rollback_history, load_history
    success = rollback_history(session_id, turn_index)
    if not success:
        raise HTTPException(status_code=400, detail="回滚失败，轮次索引不合法")
    new_history = load_history(session_id)
    return ApiResponse(msg="回滚成功", data={"history": [h.model_dump() for h in new_history]})