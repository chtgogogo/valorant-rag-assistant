# 【新增 v3.6】领域列表接口：前端一键切换知识库/领域的数据源
# 【v3.17】鉴权由 main.py include_router 统一挂载，router 内不再重复挂（去双重挂载）
from fastapi import APIRouter

from config.settings import DOMAIN_PROFILES, DOMAIN
from schemas.models import ApiResponse

domain_router = APIRouter()


@domain_router.get("/list", summary="可用领域列表（前端切换控件数据源）")
def domain_list():
    domains = [
        {
            "domain": name,                      # 领域键（= kb_id，请求时透传）
            "app_name": p.get("app_name", name),  # 展示名
            "short_name": p.get("short_name", p.get("app_name", name)),  # 切换按钮短名
            "is_default": name == DOMAIN,
            "quick_questions": p.get("quick_questions", []),  # 欢迎屏快捷问题随域切换
        }
        for name, p in DOMAIN_PROFILES.items()
    ]
    return ApiResponse(data={"domains": domains, "default": DOMAIN})
