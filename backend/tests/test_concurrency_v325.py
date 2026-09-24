# 【v3.25】并发与稳定性回归测试：三处模型单例 double-checked locking /
# RRF 融合键（前 50 字相同但 id 不同的块不互相覆盖）/ 非流式 LLM 失败口径
import threading

import pytest

import services.chat_service as cs
from schemas.models import SearchResult


class TestSingletonLocks:
    """并发首调模型单例：实例只创建一次（无锁时多线程会重复加载 1-2GB 模型并竞态覆盖）"""

    def test_semantic_cache_singleton(self, monkeypatch):
        import services.semantic_cache as sc

        created = []
        orig = sc._cache
        sc._cache = None

        def slow_factory():
            # 模拟模型加载耗时：放大竞态窗口
            import time as _t
            _t.sleep(0.05)
            inst = sc.SemanticCache(lambda t: [1.0, 0.0], epoch_path=None)
            created.append(inst)
            return inst

        monkeypatch.setattr(sc, "_build_cache_instance", slow_factory)
        threads = [threading.Thread(target=sc.get_semantic_cache) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(created) == 1, f"并发首调创建了 {len(created)} 个缓存实例（应恰好 1 个）"
        sc._cache = orig  # 恢复

    def test_embedding_model_singleton(self, monkeypatch):
        import services.vector_service as vs

        created = []
        orig_model, orig_client = vs._embedding_model, vs._chroma_client
        vs._embedding_model = None

        class FakeST:
            def encode(self, texts, **kw):
                return [[0.1] * 4]

        def slow_load(model_name, device=None, **kw):
            import time as _t
            _t.sleep(0.05)
            m = FakeST()
            created.append(m)
            return m

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "sentence_transformers":
                from types import SimpleNamespace
                return SimpleNamespace(SentenceTransformer=slow_load)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        threads = [threading.Thread(target=vs._get_embedding_model) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(created) == 1, f"并发首调创建了 {len(created)} 个 embedding 模型（应恰好 1 个）"
        vs._embedding_model = orig_model
        vs._chroma_client = orig_client


class TestRrfFuseKey:
    """RRF 融合键：唯一块 id 为准——前 50 字相同但 id 不同的块不得互相覆盖"""

    @staticmethod
    def _r(doc_id, content, score, chunk_id=None, dense=None):
        return SearchResult(content=content, source="s.md", score=score, doc_id=doc_id,
                            chunk_id=chunk_id, dense_score=dense)

    def test_same_prefix_diff_chunk_survive(self):
        a1 = self._r("docA", "重复开头的文本" + "甲的独特内容", 0.9, chunk_id="docA_0")
        a2 = self._r("docB", "重复开头的文本" + "乙的独特内容", 0.8, chunk_id="docB_0")
        fused = __import__("services.hybrid_retriever", fromlist=["_rrf_fuse"])._rrf_fuse([a1], [a2])
        assert len(fused) == 2, "前 50 字相同但 chunk_id 不同的块被互相覆盖了"

    def test_same_block_two_routes_merged(self):
        # 同一块出现在两路（向量+BM25）应合并为一条，RRF 分数累加
        r1 = self._r("docA", "同一块内容内容", 0.9, chunk_id="docA_0", dense=0.8)
        r2 = self._r("docA", "同一块内容内容", 0.7, chunk_id="docA_0")
        fused = __import__("services.hybrid_retriever", fromlist=["_rrf_fuse"])._rrf_fuse([r1], [r2])
        assert len(fused) == 1

    def test_fallback_key_without_chunk_id(self):
        # 旧数据无 chunk_id：回退 doc_id::content 前缀键（同块两路仍合并）
        r1 = self._r("docA", "无chunk_id的内容", 0.9, dense=0.8)
        r2 = self._r("docA", "无chunk_id的内容", 0.7)
        fused = __import__("services.hybrid_retriever", fromlist=["_rrf_fuse"])._rrf_fuse([r1], [r2])
        assert len(fused) == 1


class TestNonstreamFailureAccounting:
    """非流式 LLM 失败口径：兜底文案不写进历史（与流式 v3.10 对齐），审计标失败类"""

    def test_llm_failure_not_in_history(self, monkeypatch, tmp_path):
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        monkeypatch.setattr(cs, "filter_sensitive", lambda q: (False, q))
        monkeypatch.setattr(cs, "get_profile", lambda kb_id=None: {
            "custom_rules": {}, "fallback_answer": "【兜底】", "refuse_answer": "拒答",
            "system_prompt": "sys", "query_rewrite_prompt": ""})
        monkeypatch.setattr(cs, "answer_official_data_query", lambda q: None)
        monkeypatch.setattr(cs, "replace_aliases_with_official", lambda q: q)
        monkeypatch.setitem(cs.CACHE_CONFIG, "enabled", False)
        results = [SearchResult(content="资料", source="s.md", score=0.9, doc_id="d1")]
        monkeypatch.setattr(cs, "_retrieve", lambda q, h, kb, profile=None, usage_ledger=None: (q, results))
        monkeypatch.setattr(cs, "_critic_refine",
                            lambda q, rw, rs, kb, usage_ledger=None: (rw, rs, []))
        monkeypatch.setattr(cs, "_should_fallback", lambda rs: False)
        monkeypatch.setattr(cs, "_auto_create_ticket", lambda *a, **k: "")

        import types as _t
        from langchain_core.runnables import RunnableLambda

        def boom(_):
            raise Exception("Error code: 429")
        monkeypatch.setattr(cs, "get_llm", lambda: RunnableLambda(boom))
        monkeypatch.setattr(cs, "get_llm_fallback", lambda: None)
        monkeypatch.setitem(cs.LLM_CONFIG, "fallback_model", "")

        audit_calls = []
        monkeypatch.setattr(cs, "log_qa", lambda *a, **k: audit_calls.append(a))

        answer, sources, history = cs.chat_single_turn("web_fail", "问题", "valorant")

        assert answer.startswith("抱歉")  # 前端仍收到友好文案
        assert all(h.content != answer for h in history), "失败兜底文案不得写进对话历史"
        assert audit_calls, "失败也要留审计"
