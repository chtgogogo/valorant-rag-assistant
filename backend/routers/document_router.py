# 【分工2写】知识库/文档接口
import asyncio
import os
from fastapi import APIRouter, UploadFile, File, Query
from schemas.models import ApiResponse
from config.settings import UPLOAD_PATH, ALLOWED_EXTENSIONS, MAX_FILE_SIZE
from services.document_service import upload_and_process, list_documents, delete_document

doc_router = APIRouter()


@doc_router.post("/upload", response_model=ApiResponse, summary="上传文档、解析切分")
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Query("valorant", description="知识库 ID")
):
    """上传文档，自动解析、清洗、切分、入库"""
    try:
        # 校验文件格式
        filename = file.filename
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return ApiResponse(code=500, msg=f"不支持的格式: {ext}，仅支持 {ALLOWED_EXTENSIONS}")

        # 校验文件大小
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            return ApiResponse(code=500, msg=f"文件过大，最大支持 {MAX_FILE_SIZE // 1024 // 1024}MB")

        # 保存原始文件
        os.makedirs(UPLOAD_PATH, exist_ok=True)
        file_path = os.path.join(UPLOAD_PATH, filename)
        with open(file_path, "wb") as f:
            f.write(content)

        # 解析、切分、入库
        # 【卡10】本路由必须保持 async（上方 await file.read()），重活用 to_thread 丢线程池，
        # 避免大文档解析+批量 embedding 阻塞事件循环
        chunks = await asyncio.to_thread(upload_and_process, file_path, filename, kb_id)

        return ApiResponse(msg="文档上传成功", data={
            "doc_name": filename,
            "chunk_count": len(chunks),
            "chunks": [c.model_dump() for c in chunks]
        })
    except ValueError as e:
        return ApiResponse(code=500, msg=str(e))
    except Exception as e:
        return ApiResponse(code=500, msg=f"上传失败: {e}")


@doc_router.get("/list", response_model=ApiResponse, summary="获取知识库文档列表")
async def list_docs(kb_id: str = Query("valorant", description="知识库 ID")):
    """获取指定知识库下的所有文档"""
    docs = list_documents(kb_id)
    return ApiResponse(data=docs)


@doc_router.delete("/delete", response_model=ApiResponse, summary="删除指定文档")
async def delete_doc(
    doc_id: str = Query(..., description="文档 ID"),
    kb_id: str = Query("valorant", description="知识库 ID")
):
    """删除文档，同时删除对应的向量数据"""
    try:
        delete_document(doc_id, kb_id)
        return ApiResponse(msg="文档删除成功")
    except Exception as e:
        return ApiResponse(code=500, msg=f"删除失败: {e}")
