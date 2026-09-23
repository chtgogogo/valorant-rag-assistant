# 【分工3写】向量检索接口
from fastapi import APIRouter
from typing import List
from schemas.models import ApiResponse, DocumentChunk
from services.vector_service import add_chunks, search_vector, delete_doc_vectors, get_collection_stats

vector_router = APIRouter()


@vector_router.post("/add_chunks", response_model=ApiResponse, summary="文档块存入向量库")
# 【卡10】同步改 def：批量生成 embedding + 写 ChromaDB，重活不阻塞事件循环
def add_chunks_api(chunks: List[DocumentChunk], kb_id: str = "valorant"):
    """接收切好的文档块，生成向量后存入 ChromaDB"""
    success = add_chunks(chunks, kb_id)
    if success:
        return ApiResponse(msg="向量入库成功", data={"chunk_count": len(chunks)})
    return ApiResponse(code=500, msg="向量入库失败")


@vector_router.post("/search", response_model=ApiResponse, summary="检索相关文档")
# 【卡10】同步改 def：查询向量化（embedding 推理）+ Chroma 检索，重活不阻塞事件循环
def search_api(question: str, kb_id: str = "valorant", top_k: int = 3):
    """根据问题检索向量库，返回最相似的文档块"""
    results = search_vector(question, kb_id, top_k)
    return ApiResponse(data=[r.model_dump() for r in results])


@vector_router.get("/stats", response_model=ApiResponse, summary="获取知识库向量统计")
async def stats_api(kb_id: str = "valorant"):
    """获取指定知识库的向量块数量等统计信息"""
    stats = get_collection_stats(kb_id)
    return ApiResponse(data=stats)



@vector_router.delete("/delete_doc", response_model=ApiResponse, summary="删除文档对应向量")
async def delete_doc_api(doc_id: str, kb_id: str = "valorant"):
    """删除指定文档在向量库中的所有向量数据"""
    success = delete_doc_vectors(doc_id, kb_id)
    if success:
        return ApiResponse(msg="向量删除成功")
    return ApiResponse(code=500, msg="向量删除失败")
