from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import json
from config.settings import resolve_kb_id
from schemas.models import ApiResponse, ChatRequest, ChatResponse, ChatMessage
from utils.rate_limit import check_chat_rate_limit, RateLimitExceeded
chat_router = APIRouter()


def _client_ip(request: Request) -> str:
    """【v3.24】取真实客户端 IP：Nginx 反代后 client.host 是 127.0.0.1，
    限流按 X-Forwarded-For 首段 / X-Real-IP 计（step3 Nginx 已注入这两个头）。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


# 测试接口保留（/test 供前端后端在线检测使用；【v3.12】"测试大模型"死代码路由已随 chat_service 死函数一并删除）
@chat_router.get("/test", response_model=ApiResponse, summary="测试模块加载")
async def test_chat_module():
    return ApiResponse(msg="对话模块加载成功", data={"status": "测试通过"})
# 核心发送接口
@chat_router.post("/send", response_model=ApiResponse, summary="发送消息获取回答")
# 【卡10】同步改 def：内部 chat_single_turn 含 LLM 秒级调用+rerank 推理，
# async def 内直调会阻塞事件循环（期间全站请求排队）；FastAPI 对同步 def 自动走线程池
def send_message(req: ChatRequest, request: Request):
    from services.chat_service import chat_single_turn, classify_turn_route, agent_turn
    try:
        check_chat_rate_limit(_client_ip(request))  # 【v3.24】IP 双层限流：超限 429
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=e.message)
    resolve_kb_id(req.kb_id)  # 【v3.20】kb_id 白名单：非法 400 / 未知 404，不再静默回退
    # 【W8-卡3】分岔口：多跳/对比/统计/操作类走 Agent，其余走 Workflow（拿不准默认 Workflow）；
    # 门槛拦截/缓存命中等无决策轮 route_meta=None，响应不带 route
    go_agent, route_meta = classify_turn_route(req.session_id, req.question, req.kb_id)
    if go_agent:
        answer, sources, history = agent_turn(req.session_id, req.question, req.kb_id)
    else:
        answer, sources, history = chat_single_turn(req.session_id, req.question, req.kb_id)
    resp = ChatResponse(answer=answer, sources=sources, history=history, route=route_meta)
    return ApiResponse(data=resp.model_dump())

# 流式发送接口（v3.0 新增）：SSE 协议，答案逐字推送，前端边收边渲染
@chat_router.post("/stream", summary="流式发送消息（SSE）")
async def stream_message(req: ChatRequest, request: Request):
    from services.chat_service import chat_single_turn_stream, classify_turn_route, agent_turn
    # 【v3.20】kb_id 必须在开流前校验：放进生成器里抛 HTTPException 只会变成断流而非 4xx 响应
    resolve_kb_id(req.kb_id)
    # 【v3.24】限流同样在开流前：超限以 SSE error 事件收尾（流式无 HTTPException 语义）
    try:
        check_chat_rate_limit(_client_ip(request))
    except RateLimitExceeded as e:
        # 闭包先捕获 message：except 块结束即 del e，延迟执行的生成器里访问 e 会
        # NameError（v3.24 既有坑——真实撞限流时用户收到断流而非友好提示，
        # W8-卡3 接线测试触发后顺手根治）
        limited_message = e.message

        def limited_stream():
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'code': 'rate_limited', 'message': limited_message}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            limited_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def event_stream():
        # 【W8-卡3】分岔口（与 /send 同一份判定，放在同步生成器里 → 线程池迭代，
        # 首次 embedding 冷加载不阻塞事件循环）：Agent 分支一次性产出——
        # 轨迹逐行流式是 /api/agent/chat/stream（卡 5）的职责，普通聊天分岔到 Agent 不推轨迹
        go_agent, route_meta = classify_turn_route(req.session_id, req.question, req.kb_id)
        if go_agent:
            answer, sources, history = agent_turn(req.session_id, req.question, req.kb_id)
            yield f"event: sources\ndata: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
            yield f"event: token\ndata: {json.dumps({'delta': answer}, ensure_ascii=False)}\n\n"
            done = {"answer": answer, "history": [m.model_dump() for m in history],
                    "route": route_meta}
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"
            return
        for event in chat_single_turn_stream(req.session_id, req.question, req.kb_id):
            event_type = event.pop("type")
            # 【W8-卡3】路由决策随 done 事件透传（可解释性；前端对未知字段天然忽略）
            if event_type == "done" and route_meta is not None:
                event["route"] = route_meta
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# 历史读取接口（会话刷新恢复）：按 session_id 返回该会话全部消息，只读不改
@chat_router.get("/history", response_model=ApiResponse, summary="按会话ID读取历史消息")
async def get_chat_history(session_id: str):
    from services.chat_service import load_history
    history = load_history(session_id)
    return ApiResponse(msg="历史读取成功", data={"history": [h.model_dump() for h in history]})

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

@chat_router.get("/warmup", response_model=ApiResponse, summary="预热检索模型")
# 【卡10】同步改 def：预热要跑完整检索（加载 embedding/BM25/CrossEncoder），秒级重活，不阻塞事件循环
def warmup_retrieval_api(kb_id: str = None):
    """预热 embedding、BM25、重排模型，避免第一次问答过慢。"""
    from services.chat_service import warmup_retrieval
    resolve_kb_id(kb_id)  # 【v3.20】kb_id 白名单
    data = warmup_retrieval(kb_id)
    return ApiResponse(msg="检索模型预热完成", data=data)
