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
from routers.ticket_router import ticket_router
from routers.domain_router import domain_router
# 企业化预留：AUTH_ENABLED=1 时所有 /api 接口要求 X-API-Key 请求头
app.include_router(doc_router, prefix="/api/document", tags=["知识库文档"], dependencies=[Depends(verify_api_key)])
app.include_router(vector_router, prefix="/api/vector", tags=["向量检索"], dependencies=[Depends(verify_api_key)])
app.include_router(chat_router, prefix="/api/chat", tags=["对话业务"], dependencies=[Depends(verify_api_key)])
app.include_router(ticket_router, prefix="/api/ticket", tags=["售后工单"], dependencies=[Depends(verify_api_key)])
app.include_router(domain_router, prefix="/api/domain", tags=["领域切换"], dependencies=[Depends(verify_api_key)])

# 审计日志查询接口（运维排查用）
@app.get("/api/audit/recent", tags=["审计日志"], summary="查询最近N天问答审计记录")
def recent_audit(days: int = 7):
    from utils.audit import read_recent_audit
    return {"code": 200, "msg": "success", "data": read_recent_audit(days)}

if __name__ == "__main__":
    import uvicorn
    # 默认关闭热重载：reload=True 时 uvicorn 会额外起一个"监视文件"的父进程，
    # 而 main.py 顶层 import 会把 torch/sentence_transformers 一起带进来，
    # 等于白白多占约 1.6GB 内存，而且一改文件就把子进程连同模型重载一遍。
    # 需要改代码即时生效时：先设环境变量 DEV_RELOAD=1 再启动。
    DEV_RELOAD = os.getenv("DEV_RELOAD", "0") == "1"
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=DEV_RELOAD)
