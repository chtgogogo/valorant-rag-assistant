# 向量入库/检索逻辑
import logging
import os
# 设置 HuggingFace 镜像，解决国内 SSL/网络问题（必须在 import sentence_transformers 之前设置）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("CURL_CA_BUNDLE", "")
# chromadb 0.5.5 的遥测与 posthog 不兼容会刷无害报错，直接静音
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry.segment").setLevel(logging.CRITICAL)
import chromadb
from config.settings import VECTOR_DB_PATH, EMBEDDING_CONFIG
from schemas.models import DocumentChunk, SearchResult

# -------------------------- 全局实例（懒加载，避免启动时卡住） --------------------------
_embedding_model = None
_chroma_client = None


def _get_embedding_model():
    """懒加载 bge-small-zh-v1.5 embedding 模型（GPU 优先，OOM 自动降级 CPU）"""
    global _embedding_model
    if _embedding_model is None:
        # 延迟导入：sentence_transformers 会把 torch 一起拉进来（约 1.5GB），
        # 挪到真正要用模型的时候才 import，进程启动就轻得多
        from sentence_transformers import SentenceTransformer
        from services.device_manager import get_device, is_oom_error, degrade_to_cpu
        model_name = EMBEDDING_CONFIG["model_name"]
        # bge 系列模型在 HuggingFace 上的完整路径是 BAAI/xxx，配置里写的是简写
        if "/" not in model_name:
            model_name = f"BAAI/{model_name}"
        device = get_device()
        print(f"[向量引擎] 正在加载 Embedding 模型: {model_name} (device={device}) ...")
        try:
            _embedding_model = SentenceTransformer(model_name, device=device)
        except Exception as e:
            if is_oom_error(e) and device == "cuda":
                degrade_to_cpu(None)
                _embedding_model = SentenceTransformer(model_name, device="cpu")
            else:
                raise
        print("[向量引擎] Embedding 模型加载完成")
    return _embedding_model


def _get_chroma_client():
    """懒加载 ChromaDB 持久化客户端"""
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
    return _chroma_client


def _get_collection(kb_id: str):
    """获取或创建指定知识库的 collection（用 cosine 距离）"""
    client = _get_chroma_client()
    return client.get_or_create_collection(
        name=kb_id,
        metadata={"hnsw:space": "cosine"}
    )


# -------------------------- 核心功能函数 --------------------------

def add_chunks(chunks: list[DocumentChunk], kb_id: str = "valorant") -> bool:
    """
    文档块批量存入向量库
    :param chunks: 切好的文档块列表
    :param kb_id: 知识库 ID（对应 ChromaDB 的 collection 名）
    :return: 成功 True / 失败 False
    """
    try:
        if not chunks:
            return False
        model = _get_embedding_model()
        collection = _get_collection(kb_id)

        texts = [chunk.content for chunk in chunks]
        metadatas = [chunk.metadata for chunk in chunks]
        # 用 doc_id + 序号生成唯一 ID，避免重复
        ids = []
        for i, chunk in enumerate(chunks):
            doc_id = chunk.metadata.get("doc_id", "unknown")
            ids.append(f"{doc_id}_{i}")

        # 生成 embedding 向量
        embeddings = model.encode(texts, show_progress_bar=False).tolist()

        # 存入 ChromaDB
        collection.add(
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids
        )
        print(f"[向量引擎] 成功入库 {len(chunks)} 个文档块到知识库 [{kb_id}]")
        return True
    except Exception as e:
        print(f"[向量引擎] 入库失败: {e}")
        return False


def search_vector(question: str, kb_id: str = "valorant", top_k: int = 3) -> list[SearchResult]:
    """
    语义检索：把问题转向量，在向量库中找最相似的文档块
    :param question: 用户问题
    :param kb_id: 知识库 ID
    :param top_k: 返回前 K 条结果
    :return: SearchResult 列表
    """
    try:
        model = _get_embedding_model()
        collection = _get_collection(kb_id)

        # 如果集合为空，直接返回空列表
        if collection.count() == 0:
            return []

        # 生成查询向量
        query_embedding = model.encode([question], show_progress_bar=False).tolist()

        # ChromaDB 检索
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=min(top_k, collection.count())
        )

        # 转换为 SearchResult
        search_results = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                # cosine 距离转相似度分数: distance=0 → score=1.0
                distance = results["distances"][0][i] if results["distances"] else 1.0
                score = max(0.0, 1.0 - distance)

                search_results.append(SearchResult(
                    content=doc,
                    source=metadata.get("doc_name", "未知文档"),
                    score=round(score, 4),
                    doc_id=metadata.get("doc_id", "unknown")
                ))

        return search_results
    except Exception as e:
        print(f"[向量引擎] 检索失败: {e}")
        return []


def delete_doc_vectors(doc_id: str, kb_id: str = "valorant") -> bool:
    """
    删除指定文档在向量库中的所有向量
    :param doc_id: 文档 ID
    :param kb_id: 知识库 ID
    :return: 成功 True / 失败 False
    """
    try:
        collection = _get_collection(kb_id)
        # 通过 metadata 中的 doc_id 过滤删除
        collection.delete(where={"doc_id": doc_id})
        print(f"[向量引擎] 已删除文档 [{doc_id}] 的向量数据")
        return True
    except Exception as e:
        print(f"[向量引擎] 删除失败: {e}")
        return False


def get_collection_stats(kb_id: str = "valorant") -> dict:
    """获取指定知识库的向量规模统计，供前端展示"""
    try:
        collection = _get_collection(kb_id)
        count = collection.count()
        return {
            "kb_id": kb_id,
            "chunk_count": count,
            "collection": kb_id
        }
    except Exception as e:
        print(f"[向量引擎] 统计失败: {e}")
        return {"kb_id": kb_id, "chunk_count": 0, "collection": kb_id}

