# 【新增 v3.8】用户反馈接口：点赞/点踩落库 + 最近反馈只读查询（管理页用）
# 【v3.17】鉴权由 main.py include_router 统一挂载，router 内不再重复挂（去双重挂载）
# 【v3.30】/recent 属管理页数据，单独叠加管理密码（普通用户提交反馈的 POST 不受影响）
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services import feedback_service
from schemas.models import ApiResponse
from utils.auth import verify_admin

feedback_router = APIRouter()


class FeedbackRequest(BaseModel):
    session_id: str  # 来源会话
    question: str  # 用户问题
    answer: str  # 被评价的 AI 回答
    rating: str  # up=点赞 / down=点踩


@feedback_router.post("", summary="提交评价：对一条 AI 回复点赞/点踩，同问题同答案重复评价时覆盖原记录")
def submit_feedback(req: FeedbackRequest):
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 只能是 up/down")
    if not req.answer.strip():
        raise HTTPException(status_code=400, detail="answer 不能为空")
    action = feedback_service.save_feedback(req.session_id, req.question, req.answer, req.rating)
    return ApiResponse(data={"action": action}, msg="已记录评价")


@feedback_router.get("/recent", summary="最近 N 条反馈（默认50，badcase 回流与管理页查询用）",
                     dependencies=[Depends(verify_admin)])
def recent_feedback(limit: int = 50):
    items = feedback_service.list_recent(min(max(limit, 1), 200))
    return ApiResponse(data={"feedback": items, "count": len(items)})
