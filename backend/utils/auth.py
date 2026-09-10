# 【新增 v3.0】API Key 认证：企业部署预留
# ------------------------------------------------------------
# 默认关闭（AUTH_ENABLED=0），本地开发和毕设演示不受影响。
# 企业部署时在 .env 里设 AUTH_ENABLED=1 和 API_KEYS=key1,key2，
# 所有 /api 接口要求请求头携带 X-API-Key。
# ------------------------------------------------------------
from fastapi import Header, HTTPException

from config.settings import AUTH_ENABLED, API_KEYS


def verify_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """FastAPI 依赖：校验请求头里的 X-API-Key；未开启认证时直接放行"""
    if not AUTH_ENABLED:
        return
    if not x_api_key or x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="API Key 无效或缺失")
