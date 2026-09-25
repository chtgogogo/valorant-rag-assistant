# 【新增 v3.5】工单管理接口：列表 / 处理回流 / 关闭 / 闭环统计
# 【v3.17】鉴权由 main.py include_router 统一挂载，router 内不再重复挂（去双重挂载）
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import TICKET_CONFIG
from services import ticket_service
from schemas.models import ApiResponse

ticket_router = APIRouter()


class ResolveRequest(BaseModel):
    answer: str  # 人工标准答案
    feedback: bool = True  # 是否回流知识库


@ticket_router.get("/list", summary="工单列表（可按状态筛选：open/resolved/closed）")
def ticket_list(status: str = None, limit: int = 100):
    if status and status not in ("open", "resolved", "closed"):
        raise HTTPException(status_code=400, detail="status 只能是 open/resolved/closed")
    tickets = ticket_service.list_tickets(status, limit)
    return ApiResponse(data={"tickets": tickets, "count": len(tickets)})


@ticket_router.get("/stats", summary="闭环概览：各状态数量与回流率")
def ticket_stats():
    return ApiResponse(data=ticket_service.ticket_stats())


@ticket_router.post("/{ticket_id}/resolve", summary="处理工单：填标准答案，可选回流知识库")
def ticket_resolve(ticket_id: str, req: ResolveRequest):
    if not TICKET_CONFIG["enabled"]:
        raise HTTPException(status_code=403, detail="工单功能未开启（TICKET_ENABLED=0）")
    try:
        result = ticket_service.resolve_ticket(ticket_id, req.answer, req.feedback)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ApiResponse(data=result, msg="已回流知识库" if result["doc_id"] else "已关闭工单")


@ticket_router.post("/{ticket_id}/close", summary="关闭工单（不填答案不回流）")
def ticket_close(ticket_id: str):
    try:
        ticket = ticket_service.close_ticket(ticket_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ApiResponse(data={"ticket": ticket})


@ticket_router.delete("/{ticket_id}", summary="删除工单记录（建议仅对已关闭工单使用）")
def ticket_delete(ticket_id: str):
    try:
        ticket = ticket_service.delete_ticket(ticket_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ApiResponse(data={"ticket": ticket}, msg="工单已删除")
