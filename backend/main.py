# 【共用文件】项目入口，统一改，不许私自改
import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from config.settings import SERVER_HOST, SERVER_PORT, UPLOAD_PATH, VECTOR_DB_PATH, CHAT_CONFIG, APP_NAME
from utils.auth import verify_api_key
# 启动自动创建所有需要的文件夹，不用手动建
os.makedirs(UPLOAD_PATH, exist_ok=True)
os.makedirs(VECTOR_DB_PATH, exist_ok=True)
os.makedirs(CHAT_CONFIG["history_path"], exist_ok=True)
app = FastAPI(title=APP_NAME)

# 健康检查接口
@app.get("/health")
def health_check():
    return {"status": "ok"}

# 跨域配置，必须加，不然前端调不通
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers.document_router import doc_router
from routers.vector_router import vector_router
from routers.chat_router import chat_router
# 企业化预留：AUTH_ENABLED=1 时所有 /api 接口要求 X-API-Key 请求头
app.include_router(doc_router, prefix="/api/document", tags=["知识库文档"], dependencies=[Depends(verify_api_key)])
app.include_router(vector_router, prefix="/api/vector", tags=["向量检索"], dependencies=[Depends(verify_api_key)])
app.include_router(chat_router, prefix="/api/chat", tags=["对话业务"], dependencies=[Depends(verify_api_key)])

# 审计日志查询接口（运维排查用）
@app.get("/api/audit/recent", tags=["审计日志"], summary="查询最近N天问答审计记录")
def recent_audit(days: int = 7):
    from utils.audit import read_recent_audit
    return {"code": 200, "msg": "success", "data": read_recent_audit(days)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
