# 【新增 v3.0】混合检索：BM25 关键词召回 + 向量语义召回 + RRF 融合
# ------------------------------------------------------------
# 为什么需要两路召回：
#   纯向量检索对"幻影""暴徒"这类专有名词容易模糊匹配，
#   BM25 关键词检索对术语命中精确；两者互补，RRF（倒数排序融合）
#   把两路排名合并，比单路召回更稳。
# 这是 RAGFlow / QAnything 等企业级 RAG 的标准做法。
# ------------------------------------------------------------
import hashlib
import threading

import chromadb
import jieba
from rank_bm25 import BM25Okapi

from config.settings import VECTOR_DB_PATH, RAG_CONFIG, DEFAULT_KB_ID
from schemas.models import SearchResult
from services.vector_service import search_vector, _get_chroma_client

import logging
logger = logging.getLogger(__name__)

# RRF 常数：排名越靠前贡献越大，60 是论文推荐值
RRF_K = 60

# BM25 索引缓存：kb_id -> {"bm25", "docs", "metadatas", "ids", "count", "fingerprint"}
# 【v3.21】新鲜度机制（原"count 变化自动重建"与事实不符——删 1 篇加 1 篇 count 不变，
# 索引会永久陈旧）：每次取索引时拉取该库全量块的 id+内容，排序后整体算 SHA-256 指纹，
# 指纹与缓存一致才复用，任何增/删/换内容都会改变指纹并触发重建。
# 代价是每次检索多一次 chroma 全量 get（几百块 <10ms），远便宜于重复重建 BM25 索引
# （jieba 全量分词 + TF-IDF 矩阵，秒级）。
# 重建锁为 per-kb 粒度：一个知识库重建不再阻塞其他知识库的检索。
_bm25_cache: dict = {}
_bm25_locks: dict = {}
_locks_guard = threading.Lock()


def _get_kb_lock(kb_id: str) -> threading.Lock:
    """per-kb 重建锁：同一知识库的并发重建串行，不同知识库互不阻塞"""
    with _locks_guard:
        if kb_id not in _bm25_locks:
            _bm25_locks[kb_id] = threading.Lock()
        return _bm25_locks[kb_id]


def _kb_fingerprint(ids: list, docs: list) -> str:
    """知识库内容指纹：全部块的 id+内容按 id 排序后整体 SHA-256"""
    h = hashlib.sha256()
    for i, d in sorted(zip(ids, docs), key=lambda x: str(x[0])):
        h.update(str(i).encode("utf-8"))
        h.update(b"\x00")
        h.update((d or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _tokenize(text: str) -> list[str]:
    """jieba 中文分词，过滤空白（标点会被 jieba 切成独立 token 并保留：双侧同现、IDF 极低，对 BM25 无实害）"""
    return [t for t in jieba.lcut(text) if t.strip()]


def _get_bm25_index(kb_id: str) -> dict | None:
    """获取（或构建）知识库的 BM25 索引；空知识库返回 None
    【v3.21】按内容指纹判断新鲜度（不再信 count），重建持 per-kb 锁"""
    client = _get_chroma_client()
    current_count = client.get_or_create_collection(
        name=kb_id, metadata={"hnsw:space": "cosine"}
    ).count()
    if current_count == 0:
        with _get_kb_lock(kb_id):
            _bm25_cache.pop(kb_id, None)  # 知识库被清空：旧索引必须失效
        return None

    # 拉全量文档块（毕设级知识库几百条）：既用于指纹计算，也用于重建索引
    collection = client.get_collection(kb_id)
    data = collection.get(include=["documents", "metadatas"])
    docs = data.get("documents") or []
    if not docs:
        return None
    metadatas = data.get("metadatas") or [{}] * len(docs)
    ids = data.get("ids") or [f"unknown_{i}" for i in range(len(docs))]
    fingerprint = _kb_fingerprint(ids, docs)

    with _get_kb_lock(kb_id):
        cached = _bm25_cache.get(kb_id)
        if cached and cached["fingerprint"] == fingerprint:
            return cached  # double-check：等锁期间别的线程可能已按同指纹建好

        bm25 = BM25Okapi([_tokenize(d) for d in docs])
        entry = {"bm25": bm25, "docs": docs, "metadatas": metadatas,
                 "ids": ids, "count": current_count, "fingerprint": fingerprint}
        _bm25_cache[kb_id] = entry
        logger.info("BM25 索引已构建: kb=%s 文档块=%d 指纹=%s…", kb_id, current_count, fingerprint[:8])
        return entry


def bm25_search(question: str, kb_id: str, top_k: int) -> list[SearchResult]:
    """BM25 关键词检索，返回与向量检索同构的 SearchResult 列表"""
    entry = _get_bm25_index(kb_id)
    if entry is None:
        return []
    scores = entry["bm25"].get_scores(_tokenize(question))
    # 取分数最高的 top_k 条（分数为 0 的跳过）
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
    results = []
    for idx, score in ranked:
        if score <= 0:
            continue
        meta = entry["metadatas"][idx] or {}
        results.append(SearchResult(
            content=entry["docs"][idx],
            source=meta.get("doc_name", "未知文档"),
            score=round(float(score), 4),
            doc_id=meta.get("doc_id", "unknown"),
            dense_score=None,
            version=meta.get("version"),
            chunk_id=entry["ids"][idx] if idx < len(entry["ids"]) else None,
        ))
    return results


def _rrf_fuse(dense: list[SearchResult], sparse: list[SearchResult]) -> list[SearchResult]:
    """RRF 倒数排序融合：score = Σ 1/(k + rank)，只看排名不看原始分数量纲
    【v3.25】融合键改为块唯一 id（chunk_id，chroma 块 id）——旧键"doc_id+内容前50字"
    会让同文档内前 50 字相同的两个不同块互相覆盖；无 chunk_id 的旧数据回退旧键。
    键一次性算好存 r.chunk_id 之外的本地 dict，不再在排序时重复拼接。"""
    rrf_scores: dict[str, float] = {}
    blocks: dict[str, SearchResult] = {}
    keys: dict[int, str] = {}  # id(r) -> 融合键（一次性计算，避免排序时重复拼接/不一致）

    def _fuse_key(r: SearchResult) -> str:
        if r.chunk_id:
            return f"id::{r.chunk_id}"
        return f"{r.doc_id}::{r.content[:50]}"  # 旧数据兼容回退

    def _add(items: list[SearchResult]):
        for rank, r in enumerate(items, start=1):
            key = _fuse_key(r)
            keys[id(r)] = key
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            if key not in blocks or (r.dense_score or 0) > (blocks[key].dense_score or 0):
                # 同一块出现在两路时，保留向量分较高（信息更全）的那份
                blocks[key] = r

    _add(dense)
    _add(sparse)

    fused = sorted(blocks.values(),
                   key=lambda r: rrf_scores[keys[id(r)]], reverse=True)
    return fused


def hybrid_search(question: str, kb_id: str = None,
                  recall_k: int = None) -> list[SearchResult]:
    """
    混合检索主入口：向量召回 + BM25 召回 → RRF 融合排序
    :return: 融合排序后的候选列表（长度 ≤ 2*recall_k，交给 rerank 精选）
    """
    if kb_id is None:
        # 【v3.17】不再硬编码 "valorant"，跟随默认领域配置（多领域运行时一致）
        kb_id = DEFAULT_KB_ID
    if recall_k is None:
        recall_k = RAG_CONFIG["recall_k"]

    vector_on = RAG_CONFIG.get("enable_vector_search", True)
    hybrid_on = RAG_CONFIG.get("enable_hybrid_search", True)

    # 向量召回（dense_score 保留余弦相似度，供兜底阈值判断）
    dense: list[SearchResult] = []
    if vector_on:
        dense = search_vector(question, kb_id, recall_k)
        for r in dense:
            r.dense_score = r.score
        if not hybrid_on:
            return dense
    elif hybrid_on:
        # 向量关闭时 hybrid 开关无意义（融合的前提是两路并存），只走 BM25
        logger.info("向量检索已关闭(USE_VECTOR_RETRIEVAL=0)，本次仅 BM25 关键词检索")

    # BM25 关键词召回
    try:
        sparse = bm25_search(question, kb_id, recall_k)
    except Exception as e:
        logger.warning("BM25 召回失败(降级%s): %s", "空结果" if not vector_on else "纯向量", e)
        sparse = []

    if not vector_on:
        return sparse

    if not sparse:
        return dense
    if not dense:
        return sparse

    fused = _rrf_fuse(dense, sparse)
    logger.info("混合检索: 向量%d条 + 关键词%d条 → 融合%d条", len(dense), len(sparse), len(fused))
    return fused
