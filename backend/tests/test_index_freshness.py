# 【v3.21】索引新鲜度与版本管理回归测试：
# BM25 指纹缓存键（删1加1 count 不变不再永久陈旧）/ doc_list 原子写+文件锁 /
# 损坏注册表不静默当空 / 文档版本元数据 / sources 带 version
# chroma 全程用临时目录，embedding 用确定性假模型——不污染真实知识库、不加载真模型
import hashlib
import json
import threading

import numpy as np
import pytest

import services.document_service as ds
import services.vector_service as vs
import services.hybrid_retriever as hr
import services.chat_service as cs


class FakeEmbed:
    """确定性假 embedding：相同文本同向量、不同文本不同向量（可区分 A/B 文档）"""

    def encode(self, texts, show_progress_bar=False):
        vecs = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            vecs.append([b / 255.0 for b in digest[:8]])
        return np.array(vecs, dtype=float)


@pytest.fixture()
def isolated_kb(monkeypatch, tmp_path):
    """隔离环境：临时 chroma + 假 embedding + 临时上传目录，结束后恢复单例"""
    monkeypatch.setattr(vs, "VECTOR_DB_PATH", str(tmp_path / "chroma"))
    vs._chroma_client = None
    monkeypatch.setattr(vs, "_get_embedding_model", lambda: FakeEmbed())
    monkeypatch.setattr(ds, "UPLOAD_PATH", str(tmp_path / "uploads"))
    hr._bm25_cache.clear()
    yield tmp_path
    vs._chroma_client = None
    hr._bm25_cache.clear()


def _make_doc(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestBm25Freshness:
    def test_same_count_replace_is_fresh(self, isolated_kb):
        """核心场景：上传A→删A→上传B（count 不变）→ BM25 必须看到 B 而不是 A
        （附两个背景文档：BM25 语料 ≥3 块才 IDF>0，单块语料分数为负会被过滤，与本测试无关）"""
        kb = "freshkb"
        for i, filler in enumerate(["烟雾弹是无畏契约中的战术道具，可以遮挡视野。",
                                    "急停是无畏契约中的基础射击技巧，先停再打更准。"]):
            path_c = _make_doc(isolated_kb, f"c{i}.md", filler)
            ds.upload_and_process(path_c, f"c{i}.md", kb)

        path_a = _make_doc(isolated_kb, "a.md", "幻影是无畏契约中的一把突击步枪，射速快伤害高。")
        path_b = _make_doc(isolated_kb, "b.md", "暴徒是无畏契约中的一把步枪，弹道非常稳定。")

        ds.upload_and_process(path_a, "a.md", kb)
        assert any("幻影" in r.content for r in hr.bm25_search("幻影 突击步枪", kb, top_k=3)), \
            "前置断言：A 在库中可被检索（同时建立旧代码赖以误判的 count 缓存）"
        docs = ds.list_documents(kb)
        a = next(d for d in docs if d["doc_name"] == "a.md")
        ds.delete_document(a["doc_id"], kb)

        ds.upload_and_process(path_b, "b.md", kb)
        assert len(ds.list_documents(kb)) == 3  # 删1加1，count 回到原值

        hits_old = hr.bm25_search("幻影 突击步枪", kb, top_k=3)
        assert all("幻影" not in r.content for r in hits_old), \
            "已删除文档 A 的内容仍能被 BM25 检索到——索引陈旧"

        hits_new = hr.bm25_search("暴徒 弹道", kb, top_k=3)
        assert any("暴徒" in r.content for r in hits_new), "新上传文档 B 必须能被 BM25 检索到"


class TestDocListRobustness:
    def test_missing_file_returns_empty(self, isolated_kb):
        assert ds._load_doc_list("no_such_kb_xyz") == []

    def test_corrupt_file_backed_up_not_silent(self, isolated_kb, caplog):
        """注册表损坏：error 日志 + 备份坏文件，绝不无声无息当空（否则知识库凭空消失）"""
        path = ds._get_doc_list_path("corruptkb")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"broken json')

        import logging
        with caplog.at_level(logging.ERROR):
            doc_list = ds._load_doc_list("corruptkb")

        assert doc_list == []                       # 自愈为空，但绝不静默：
        assert any("corruptkb" in r.message or "corruptkb" in str(r.args)
                   for r in caplog.records if r.levelno >= logging.ERROR)
        backups = [n for n in __import__("os").listdir(__import__("os").path.dirname(path))
                   if n.startswith("doc_list_corruptkb.json.corrupt")]
        assert backups, "损坏文件必须被备份留存"

    def test_concurrent_record_append_no_loss(self, isolated_kb):
        """per-kb 文件锁：并发上传追加记录互不覆盖，一条都不能丢"""
        kb = "lockkb"

        def worker(i: int):
            record = {"doc_id": f"doc_{i}", "doc_name": f"f{i}.md",
                      "upload_time": "t", "chunk_count": 1, "version": 1}
            ds._append_doc_record(kb, record)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        doc_list = ds._load_doc_list(kb)
        assert len(doc_list) == 10, f"并发写丢记录：仅剩 {len(doc_list)} 条"
        assert {d["doc_id"] for d in doc_list} == {f"doc_{i}" for i in range(10)}
        # 原子写：文件内容始终是合法 JSON，且无 .tmp 残留
        import os
        leftovers = [n for n in os.listdir(os.path.dirname(ds._get_doc_list_path(kb)))
                     if n.endswith(".tmp")]
        assert not leftovers


class TestVersionMetadata:
    def test_reupload_increments_version(self, isolated_kb):
        kb = "verkb"
        p1 = _make_doc(isolated_kb, "same.md", "第一版内容：捷风技能介绍。")
        p2 = _make_doc(isolated_kb, "same.md", "第二版内容：捷风技能与大招介绍。")

        ds.upload_and_process(p1, "same.md", kb)
        chunks2 = ds.upload_and_process(p2, "same.md", kb)

        doc_list = ds.list_documents(kb)
        assert len(doc_list) == 2
        versions = sorted(d["version"] for d in doc_list)
        assert versions == [1, 2], "同名文档重复上传递增 version"
        assert all(d.get("updated_at") for d in doc_list), "每条记录要有 updated_at"
        assert all(c.metadata.get("version") == 2 for c in chunks2), \
            "新上传的块 metadata 携带本次 version"

    def test_search_results_carry_version(self, isolated_kb):
        """检索结果（BM25/向量）与最终 sources 都要带 version 字段
        （背景文档让 BM25 语料 ≥3 块，规避单块语料 IDF 为负的无关边界）"""
        kb = "srcver"
        for i, filler in enumerate(["蜂刺是一把冲锋枪，价格便宜射速快。",
                                    "警长是一把手枪，爆头伤害极高。"]):
            filler_path = _make_doc(isolated_kb, f"f{i}.md", filler)
            ds.upload_and_process(filler_path, f"f{i}.md", kb)
        p = _make_doc(isolated_kb, "v.md", "猎枭是无畏契约中的侦查型英雄。")
        ds.upload_and_process(p, "v.md", kb)

        bm25_hits = hr.bm25_search("猎枭 侦查", kb, top_k=3)
        assert bm25_hits and bm25_hits[0].version == 1, "BM25 结果带 version"

        vec_hits = vs.search_vector("猎枭是无畏契约中的侦查型英雄。", kb, top_k=3)
        assert vec_hits and vec_hits[0].version == 1, "向量检索结果带 version"

        from schemas.models import SearchResult
        sources = cs._build_sources([SearchResult(content="x", source="v.md",
                                                  score=0.9, doc_id="d1", version=3)])
        assert sources[0]["version"] == 3, "回答 sources 带 version"
