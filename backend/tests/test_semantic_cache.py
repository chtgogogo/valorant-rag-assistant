# 语义缓存（v3.19）：命中/阈值/隔离/失效/淘汰/统计
import hashlib
import os
import threading
import time

import numpy as np
import pytest

from services.semantic_cache import SemanticCache, get_semantic_cache


def _unit(v) -> np.ndarray:
    arr = np.asarray(v, dtype="float32")
    return arr / (np.linalg.norm(arr) or 1.0)


def make_encoder(vectors: dict):
    """桩编码器：命中映射表的文本返回指定向量，其余返回确定性随机单位向量"""
    def _encode(text: str):
        if text in vectors:
            return vectors[text]
        digest = hashlib.md5(text.encode("utf-8")).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        return _unit(rng.normal(size=16))
    return _encode


# 受控向量：与 A 的余弦相似度分别约为 0.96 / 0.70
VEC_A = _unit([1.0, 0.0, 0.0])
VEC_A_SIM096 = _unit([0.96, 0.28, 0.0])
VEC_A_SIM070 = _unit([0.70, 0.7141, 0.0])
VEC_B = _unit([0.0, 1.0, 0.0])


def make_cache(vectors: dict, **kwargs) -> SemanticCache:
    kwargs.setdefault("threshold", 0.92)
    kwargs.setdefault("epoch_path", None)
    return SemanticCache(make_encoder(vectors), **kwargs)


class TestLookupAndPut:
    def test_exact_text_hit(self):
        cache = make_cache({"暴徒和幻影的区别": VEC_A})
        cache.put("暴徒和幻影的区别", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        hit = cache.lookup("暴徒和幻影的区别", "valorant")
        assert hit is not None
        assert hit["answer"] == "答案"
        assert hit["sim"] == 1.0

    def test_similar_text_hit_above_threshold(self):
        cache = make_cache({"暴徒和幻影的区别": VEC_A, "幻影跟暴徒有什么不同": VEC_A_SIM096})
        cache.put("暴徒和幻影的区别", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        hit = cache.lookup("幻影跟暴徒有什么不同", "valorant")
        assert hit is not None and hit["answer"] == "答案"
        assert hit["sim"] >= 0.92

    def test_below_threshold_miss(self):
        cache = make_cache({"暴徒和幻影的区别": VEC_A, "今天天气怎么样": VEC_A_SIM070})
        cache.put("暴徒和幻影的区别", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        assert cache.lookup("今天天气怎么样", "valorant") is None

    def test_kb_isolation(self):
        cache = make_cache({"q": VEC_A})
        cache.put("q", "valorant", "答案A", [{"name": "s", "id": "d", "score": 0.9}])
        assert cache.lookup("q", "ecommerce") is None
        assert cache.lookup("q", "valorant") is not None


class TestGuardAndExpiry:
    def test_empty_sources_not_cached(self):
        cache = make_cache({"q": VEC_A})
        cache.put("q", "valorant", "", [{"name": "s", "id": "d", "score": 0.9}])   # 空回答
        cache.put("q", "valorant", "兜底话术", [])                                    # 空来源（兜底）
        assert cache.lookup("q", "valorant") is None

    def test_ttl_expiry(self):
        cache = make_cache({"q": VEC_A}, ttl_seconds=0.01)
        cache.put("q", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        time.sleep(0.05)
        assert cache.lookup("q", "valorant") is None

    def test_invalidate_kb(self):
        cache = make_cache({"q": VEC_A})
        cache.put("q", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        cache.invalidate_kb("valorant")
        assert cache.lookup("q", "valorant") is None


class TestEpochInvalidation:
    def test_epoch_mtime_change_clears_cache(self, tmp_path):
        epoch = tmp_path / "kb_epoch"
        epoch.write_text("", encoding="utf-8")
        cache = make_cache({"q": VEC_A}, epoch_path=str(epoch))
        cache.put("q", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        assert cache.lookup("q", "valorant") is not None

        # 模拟知识库重建（离线脚本 touch epoch 文件）：mtime 前移 → 缓存必须失效
        st = os.stat(epoch)
        os.utime(epoch, (st.st_atime + 10, st.st_mtime + 10))
        assert cache.lookup("q", "valorant") is None
        # 失效后重新写入可以正常命中（缓存重建）
        cache.put("q", "valorant", "新答案", [{"name": "s", "id": "d", "score": 0.9}])
        assert cache.lookup("q", "valorant")["answer"] == "新答案"


class TestEvictionAndStats:
    def test_max_entries_fifo_eviction(self):
        cache = make_cache({"q1": VEC_A, "q2": _unit([0.6, 0.8, 0.0]), "q3": VEC_B}, max_entries=2)
        for q in ("q1", "q2", "q3"):
            cache.put(q, "valorant", f"答案{q}", [{"name": "s", "id": "d", "score": 0.9}])
        assert cache.lookup("q1", "valorant") is None    # 最老的 q1 被淘汰
        assert cache.lookup("q3", "valorant") is not None

    def test_stats_counters(self):
        cache = make_cache({"q1": VEC_A, "q2": VEC_A_SIM070})
        cache.put("q1", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
        cache.lookup("q1", "valorant")                    # hit
        cache.lookup("q2", "valorant")                    # miss（低于阈值）
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["entries"] == {"valorant": 1}


class TestConcurrency:
    def test_parallel_put_lookup_smoke(self):
        vectors = {f"q{i}": _unit([1.0, 0.01 * i, 0.0]) for i in range(8)}
        cache = make_cache(vectors, max_entries=64)

        errors: list = []

        def worker(i: int):
            try:
                q = f"q{i}"
                cache.put(q, "valorant", f"答案{i}", [{"name": "s", "id": "d", "score": 0.9}])
                cache.lookup(q, "valorant")
                cache.stats()
            except Exception as e:  # pragma: no cover - 仅在竞态出现时触发
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestProductionSingleton:
    def test_singleton_lazy_no_model_load(self):
        # 只验证可创建且统计归零路径可用（encode 懒加载，不会在此触发模型下载）
        cache = get_semantic_cache()
        assert isinstance(cache, SemanticCache)
        assert set(cache.stats()) >= {"hits", "misses", "hit_rate", "entries"}


@pytest.mark.parametrize("threshold,sim_vec,expect_hit", [
    (0.92, VEC_A_SIM096, True),
    (0.97, VEC_A_SIM096, False),   # 阈值调高后同样的相似度不再命中
])
def test_threshold_configurable(threshold, sim_vec, expect_hit):
    cache = make_cache({"q1": VEC_A, "q2": sim_vec}, threshold=threshold)
    cache.put("q1", "valorant", "答案", [{"name": "s", "id": "d", "score": 0.9}])
    hit = cache.lookup("q2", "valorant")
    assert (hit is not None) is expect_hit
