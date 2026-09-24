# 【新增 v3.19】语义缓存：把"问题→答案"按语义相似度缓存起来
# ------------------------------------------------------------
# 解决什么：免费档大模型慢且限流频繁，而大量提问是相似/重复问题
# （"暴徒和幻影区别" vs "幻影跟暴徒有什么不同"）。命中缓存时跳过
# 改写→混合检索→重排→大模型生成全程，毫秒级返回。
#
# 为什么不用 ChromaDB 存缓存：缓存条目少（百级）、要 TTL/淘汰/统计，
# 内存 dict + 余弦相似度足够；重启丢失可接受（用几次自然回暖）。
#
# 失效策略（双保险）：
#   1. epoch 标记文件：任何知识库变更（上传/删除/重建索引，含离线脚本）
#      touch backend/data/kb_epoch，缓存在下次查询时发现 mtime 变化即全量清空
#      ——离线重建脚本是独立进程，直接调不到本进程内存，只能走文件信号
#   2. TTL + 每知识库条数上限（FIFO 淘汰），防陈旧与膨胀
#
# 只缓存带来源的成功回答（sources 非空）：低置信兜底/生成失败不缓存，
# 保证缓存里永远是"有依据的答案"。
# ------------------------------------------------------------
import os
import time
import threading
import logging

import numpy as np

logger = logging.getLogger(__name__)


class SemanticCache:
    """进程内语义缓存：encode_fn 注入（生产=共享 embedding 模型，测试=桩函数）"""

    def __init__(self, encode_fn, threshold: float = 0.92, ttl_seconds: float = 86400,
                 max_entries: int = 512, epoch_path: str = None):
        self._encode = encode_fn
        self._threshold = float(threshold)
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._epoch_path = epoch_path
        self._lock = threading.Lock()
        self._encode_lock = threading.Lock()  # embedding 模型首次加载/推理串行化，防并发竞态
        self._buckets: dict[str, list] = {}   # kb_id -> [ {text, vec, answer, sources, ts} ]
        self._hits = 0
        self._misses = 0
        self._epoch_mtime = self._read_epoch_mtime()

    # ---------------- epoch（知识库变更信号） ----------------
    def _read_epoch_mtime(self) -> float:
        try:
            if self._epoch_path and os.path.exists(self._epoch_path):
                return os.path.getmtime(self._epoch_path)
        except OSError:
            pass
        return 0.0

    def _check_epoch(self):
        if not self._epoch_path:
            return
        mtime = self._read_epoch_mtime()
        if mtime != self._epoch_mtime:
            logger.info("[语义缓存] 检测到知识库变更（epoch mtime %.3f → %.3f），清空全部缓存",
                        self._epoch_mtime, mtime)
            self._buckets.clear()
            self._epoch_mtime = mtime

    # ---------------- 向量 ----------------
    def _embed(self, text: str) -> np.ndarray:
        with self._encode_lock:
            vec = self._encode(text)
        v = np.asarray(vec, dtype="float32").reshape(-1)
        norm = float(np.linalg.norm(v)) or 1.0
        return v / norm

    # ---------------- 核心 API ----------------
    def lookup(self, text: str, kb_id: str):
        """命中返回 {"answer","sources","sim"}，未命中返回 None"""
        with self._lock:
            self._check_epoch()
            now = time.time()
            bucket = self._buckets.get(kb_id) or []
            alive = [e for e in bucket if now - e["ts"] <= self._ttl]
            if len(alive) != len(bucket):
                self._buckets[kb_id] = alive
                bucket = alive
            if not bucket:
                self._misses += 1
                return None
            q = self._embed(text)
            best, best_sim = None, -1.0
            for e in bucket:
                sim = float(np.dot(q, e["vec"]))
                if sim > best_sim:
                    best, best_sim = e, sim
            if best_sim >= self._threshold:
                self._hits += 1
                best["ts"] = now  # 命中续期，热点答案不被 TTL 淘汰
                return {"answer": best["answer"], "sources": best["sources"],
                        "sim": round(best_sim, 4)}
            self._misses += 1
            return None

    def put(self, text: str, kb_id: str, answer: str, sources: list):
        """只缓存带来源的成功回答；空回答/兜底（sources 为空）直接忽略"""
        if not answer or not sources:
            return
        with self._lock:
            self._check_epoch()
            vec = self._embed(text)
            bucket = self._buckets.setdefault(kb_id, [])
            bucket.append({"text": text, "vec": vec, "answer": answer,
                           "sources": sources, "ts": time.time()})
            while len(bucket) > self._max:
                bucket.pop(0)

    def invalidate_kb(self, kb_id: str):
        with self._lock:
            self._buckets.pop(kb_id, None)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {"hits": self._hits, "misses": self._misses,
                    "hit_rate": round(self._hits / total, 4) if total else 0.0,
                    "entries": {k: len(v) for k, v in self._buckets.items()}}


# ---------------- 模块级单例（生产路径懒加载） ----------------
_cache: SemanticCache | None = None
_cache_lock = threading.Lock()  # 【v3.25】并发首调只建一次实例


def _build_cache_instance() -> SemanticCache:
    """构造生产缓存实例（encode 复用向量服务的共享 embedding 模型，懒加载）"""
    from config.settings import CACHE_CONFIG
    from services import vector_service

    def _encode(text: str):
        model = vector_service._get_embedding_model()
        return model.encode([text], show_progress_bar=False,
                            normalize_embeddings=True)[0]

    return SemanticCache(
        _encode,
        threshold=CACHE_CONFIG["threshold"],
        ttl_seconds=CACHE_CONFIG["ttl_minutes"] * 60,
        max_entries=CACHE_CONFIG["max_entries"],
        epoch_path=CACHE_CONFIG["epoch_path"],
    )


def get_semantic_cache() -> SemanticCache:
    """生产单例（【v3.25】double-checked locking：并发首调只创建一次实例）"""
    global _cache
    if _cache is None:  # 先查（无锁快路径）
        with _cache_lock:
            if _cache is None:  # 再查（等锁期间别的线程可能已建好）
                _cache = _build_cache_instance()
    return _cache


def touch_kb_epoch():
    """知识库内容变更后调用：mtime 信号让所有缓存实例在下次查询时自动失效。
    适用于 进程内（上传/删除文档）与 离线脚本（重建索引，跨进程只能靠文件）两种场景。"""
    from config.settings import CACHE_CONFIG
    path = CACHE_CONFIG["epoch_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        os.utime(path, None)
