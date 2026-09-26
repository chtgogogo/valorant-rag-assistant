# 【v3.33】上线加固轻量批回归测试（全 mock/临时目录，不依赖真模型/网络）
# 覆盖：并发闸门（进/排/满/超时/还席）+ /send 与 /stream 接线（429 与 SSE error）
#       BM25 epoch 快路径（不变零指纹计算 / 变更恰好一次重校验后回快路径）
#       会话过期清理 / 审计无损归档轮转 / 上传容量配额
import gzip
import hashlib
import json
import os
import threading
import time

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.chat_service as cs
import services.document_service as ds
import services.hybrid_retriever as hr
import services.vector_service as vs
from routers.chat_router import chat_router
from utils.concurrency_gate import ConcurrencyGate, GateFull
from utils.ops_maintenance import cleanup_expired_sessions, rotate_old_audits


# ======================= 并发闸门（单元） =======================

class TestConcurrencyGate:
    def test_admit_then_reject_when_full_and_readmit_after_release(self):
        gate = ConcurrencyGate(max_inflight=1, max_queue=0, wait_timeout=0.1)
        with gate:
            with pytest.raises(GateFull):
                gate.enter()
        assert gate.stats() == {"inflight": 0, "waiting": 0}
        with gate:  # 还席后可再次进入
            pass

    def test_queue_is_bounded(self):
        """inflight=1、队列=1：第 2 个进队等待，第 3 个应立刻被拒（不无限排队）"""
        gate = ConcurrencyGate(max_inflight=1, max_queue=1, wait_timeout=5)
        entered_first = threading.Event()
        release = threading.Event()

        def first():
            with gate:
                entered_first.set()
                release.wait(5)

        t = threading.Thread(target=first, daemon=True)
        t.start()
        assert entered_first.wait(5)

        def queued():
            gate.enter()
            gate.leave()  # 拿到席位立即归还，便于终态断言

        queued_t = threading.Thread(target=queued, daemon=True)
        queued_t.start()
        time.sleep(0.2)  # 等 2 号线程进队
        assert gate.stats()["waiting"] == 1

        with pytest.raises(GateFull):
            gate.enter()  # 队列已满：立即拒，不等待

        release.set()
        t.join(5)
        queued_t.join(5)
        assert gate.stats()["inflight"] == 0, "排队者拿到席位并在退出后归还"

    def test_wait_timeout_rejects(self):
        gate = ConcurrencyGate(max_inflight=1, max_queue=1, wait_timeout=0.3)
        with gate:
            outcome = {}

            def queued():
                try:
                    gate.enter()
                    outcome["ok"] = True
                except GateFull as e:
                    outcome["full"] = e.message

            t = threading.Thread(target=queued, daemon=True)
            t.start()
            t.join(5)
        assert "full" in outcome, "占用者不放席时，排队者应在超时后被拒"

    def test_gate_full_message_is_user_facing(self):
        assert "稍等" in GateFull("当前提问的人比较多，队列已满，请稍等几秒再试～").message


# ======================= 路由接线（/send 与 /stream） =======================

AGENT_Q = "捷风和雷兹都是决斗类型英雄，玩法和技能有什么不同"
SINGLE_Q = "捷风的大招是什么？"


@pytest.fixture()
def client(monkeypatch):
    import routers.chat_router as cr
    monkeypatch.setattr(cr, "check_chat_rate_limit", lambda ip: None)
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    return TestClient(app)


class TestGateWiring:
    def _stub_workflow(self, monkeypatch):
        monkeypatch.setattr(cs, "classify_turn_route", lambda sid, q, kb: (q == AGENT_Q, None))
        monkeypatch.setattr(cs, "chat_single_turn", lambda sid, q, kb: ("答案", [], []))
        monkeypatch.setattr(cs, "agent_turn",
                            lambda sid, q, kb: ("Agent答案", [], [], []))

    def test_send_returns_429_when_gate_full(self, client, monkeypatch):
        import routers.chat_router as cr
        full_gate = ConcurrencyGate(1, 0, 0.1)
        full_gate.enter()  # 占死唯一席位
        monkeypatch.setattr(cr, "get_generation_gate", lambda: full_gate)
        self._stub_workflow(monkeypatch)  # 若闸门失效会真进业务桩——用返回差异证明拦在闸门

        resp = client.post("/api/chat/send", json={"session_id": "s1", "question": SINGLE_Q, "kb_id": "valorant"})
        assert resp.status_code == 429
        assert "稍等" in resp.json()["detail"]

    def test_send_passes_through_with_capacity(self, client, monkeypatch):
        import routers.chat_router as cr
        monkeypatch.setattr(cr, "get_generation_gate", lambda: ConcurrencyGate(2, 2, 1))
        self._stub_workflow(monkeypatch)

        resp = client.post("/api/chat/send", json={"session_id": "s1", "question": SINGLE_Q, "kb_id": "valorant"})
        assert resp.status_code == 200
        assert resp.json()["data"]["answer"] == "答案"

    def test_stream_gate_full_is_sse_error_event(self, client, monkeypatch):
        import routers.chat_router as cr
        full_gate = ConcurrencyGate(1, 0, 0.1)
        full_gate.enter()
        monkeypatch.setattr(cr, "get_generation_gate", lambda: full_gate)
        self._stub_workflow(monkeypatch)

        resp = client.post("/api/chat/stream", json={"session_id": "s1", "question": SINGLE_Q, "kb_id": "valorant"})
        assert "gate_full" in resp.text and "error" in resp.text, "队满以 SSE error 事件收尾"

    def test_stream_releases_seat_after_client_done(self, client, monkeypatch):
        """正常消费完流：席位必须归还（断流走 GeneratorExit 也走 finally 归还）"""
        import routers.chat_router as cr
        gate = ConcurrencyGate(2, 2, 1)
        monkeypatch.setattr(cr, "get_generation_gate", lambda: gate)
        monkeypatch.setattr(cs, "classify_turn_route", lambda sid, q, kb: (False, None))

        def fake_stream(sid, q, kb):
            for ev in ({"type": "sources", "sources": []}, {"type": "token", "delta": "答"},
                       {"type": "done", "answer": "答", "history": []}):
                yield dict(ev)

        monkeypatch.setattr(cs, "chat_single_turn_stream", fake_stream)
        resp = client.post("/api/chat/stream", json={"session_id": "s1", "question": SINGLE_Q, "kb_id": "valorant"})
        assert "event: done" in resp.text
        assert gate.stats()["inflight"] == 0, "流结束后席位必须归还，否则一次断流就永久占用一个名额"


# ======================= BM25 epoch 快路径 =======================

class FakeEmbed:
    def encode(self, texts, show_progress_bar=False):
        vecs = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            vecs.append([b / 255.0 for b in digest[:8]])
        return np.array(vecs, dtype=float)


@pytest.fixture()
def isolated_kb(monkeypatch, tmp_path):
    monkeypatch.setattr(vs, "VECTOR_DB_PATH", str(tmp_path / "chroma"))
    vs._chroma_client = None
    monkeypatch.setattr(vs, "_get_embedding_model", lambda: FakeEmbed())
    monkeypatch.setattr(ds, "UPLOAD_PATH", str(tmp_path / "uploads"))
    hr._bm25_cache.clear()
    yield tmp_path
    vs._chroma_client = None
    hr._bm25_cache.clear()


class TestBm25EpochFastPath:
    @staticmethod
    def _make_doc(tmp_path, name, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _seed(self, tmp_path, kb):
        # 语料 ≥3 块：rank_bm25 小语料 IDF 为负会被分数过滤（见 test_index_freshness 同款注释）
        for i, t in enumerate(["烟雾弹是无畏契约中的战术道具，可以遮挡视野。",
                               "急停是无畏契约中的基础射击技巧，先停再打更准。",
                               "蜂刺是一把便宜的冲锋枪，射速很快。"]):
            ds.upload_and_process(self._make_doc(tmp_path, f"f{i}.md", t), f"f{i}.md", kb)

    def test_unchanged_kb_skips_full_fingerprint_scan(self, isolated_kb, monkeypatch):
        """核心收益证明：知识库没变时，查询路径不再做全量拉块+指纹计算（O(N)→O(1)）
        ——把指纹函数换成会爆炸的桩，若仍被调用则测试失败"""
        kb = "fastkb"
        self._seed(isolated_kb, kb)
        assert hr.bm25_search("烟雾 遮挡", kb, top_k=3), "前置：建好索引"

        def _must_not_run(*a, **k):
            raise AssertionError("知识库未变更时不应重新计算全量指纹")
        monkeypatch.setattr(hr, "_kb_fingerprint", _must_not_run)
        assert hr.bm25_search("烟雾 遮挡", kb, top_k=3), "快路径必须正常返回结果"

    def test_epoch_change_triggers_exactly_one_revalidation(self, isolated_kb, monkeypatch):
        """变更信号触发 → 恰好一次全量校验（内容没变则不重建），随后回到快路径"""
        kb = "watermark"
        self._seed(isolated_kb, kb)
        assert hr.bm25_search("烟雾 遮挡", kb, top_k=3)

        from services.semantic_cache import touch_kb_epoch
        touch_kb_epoch()

        calls = []
        real_fingerprint = hr._kb_fingerprint

        def counting(*a, **k):
            calls.append(1)
            return real_fingerprint(*a, **k)
        monkeypatch.setattr(hr, "_kb_fingerprint", counting)

        assert hr.bm25_search("烟雾 遮挡", kb, top_k=3)  # 慢路径：校验一次，指纹相同不重建
        assert hr.bm25_search("烟雾 遮挡", kb, top_k=3)  # 水位已刷新：回快路径
        assert len(calls) == 1, f"两次查询应只做一次全量校验，实际 {len(calls)} 次"

    def test_same_count_swap_still_detected_after_watermark_refresh(self, isolated_kb):
        """v3.21 语义不回退：水位刷新后发生的删1加1（count 不变）仍能被发现"""
        kb = "swapkb"
        self._seed(isolated_kb, kb)
        path_a = self._make_doc(isolated_kb, "a.md", "幻影是无畏契约中的一把突击步枪，射速快伤害高。")
        path_b = self._make_doc(isolated_kb, "b.md", "暴徒是无畏契约中的一把步枪，弹道非常稳定。")
        ds.upload_and_process(path_a, "a.md", kb)
        assert hr.bm25_search("幻影 突击步枪", kb, top_k=3), "前置：A 可检索且索引已建"

        docs = ds.list_documents(kb)
        a = next(d for d in docs if d["doc_name"] == "a.md")
        ds.delete_document(a["doc_id"], kb)
        ds.upload_and_process(path_b, "b.md", kb)  # 删1加1，count 回到原值
        assert len(ds.list_documents(kb)) == 4  # 3 背景 + A→B 换血，count 不变

        assert all("幻影" not in r.content for r in hr.bm25_search("幻影 突击步枪", kb, top_k=3)), \
            "已删内容仍可检索 = 指纹裁决被快路径绕过了"
        assert any("暴徒" in r.content for r in hr.bm25_search("暴徒 弹道", kb, top_k=3)), \
            "新增内容必须能被检索到"


# ======================= 存储治理：会话清理 / 审计归档 / 上传配额 =======================

class TestSessionCleanup:
    def test_expired_removed_fresh_kept(self, tmp_path):
        old = tmp_path / "web_old.json"
        fresh = tmp_path / "web_new.json"
        note = tmp_path / "ignore.txt"
        old.write_text("[]", encoding="utf-8")
        fresh.write_text("[]", encoding="utf-8")
        note.write_text("x", encoding="utf-8")
        expired = time.time() - 40 * 86400
        os.utime(old, (expired, expired))

        removed = cleanup_expired_sessions(str(tmp_path), retention_days=30)
        assert removed == 1
        assert not old.exists() and fresh.exists() and note.exists()

    def test_empty_dir_and_zero_retention_are_safe(self, tmp_path):
        assert cleanup_expired_sessions(str(tmp_path / "nope"), 30) == 0
        assert cleanup_expired_sessions(str(tmp_path), 0) == 0, "retention<=0 视为关闭清理"


class TestAuditRotation:
    def test_old_month_archived_lossless_recent_kept(self, tmp_path):
        old = tmp_path / "audit_202401.jsonl"
        recent = tmp_path / "audit_209912.jsonl"
        other = tmp_path / "unrelated.jsonl"
        payload = '{"time": "2024-01-01 00:00:00", "self_hash": "abc"}\n' * 3
        old.write_bytes(payload.encode("utf-8"))  # 二进制写入：排除 Windows 文本模式 \n→\r\n 干扰
        recent.write_text("{}", encoding="utf-8")
        other.write_text("{}", encoding="utf-8")

        archived = rotate_old_audits(str(tmp_path), retention_months=6)

        assert archived == ["audit_202401.jsonl.gz"]
        assert not old.exists(), "归档后原文件应移除（否则磁盘没省下来）"
        assert gzip.decompress((tmp_path / "audit_202401.jsonl.gz").read_bytes()).decode("utf-8") == payload, \
            "gzip 必须无损：解压后与原字节一致（哈希链可复验）"
        assert recent.exists() and other.exists(), "保留期内与不匹配命名的文件不动"

    def test_stale_half_archive_is_redone(self, tmp_path):
        """上轮中断留下 0 字节半成品归档：本轮删掉重做，而不是永久卡死"""
        old = tmp_path / "audit_202001.jsonl"
        old.write_text("{}", encoding="utf-8")
        (tmp_path / "audit_202001.jsonl.gz").write_bytes(b"")

        archived = rotate_old_audits(str(tmp_path), retention_months=6)
        assert archived == ["audit_202001.jsonl.gz"]
        assert (tmp_path / "audit_202001.jsonl.gz").stat().st_size > 0

    def test_zero_retention_disables(self, tmp_path):
        (tmp_path / "audit_202001.jsonl").write_text("{}", encoding="utf-8")
        assert rotate_old_audits(str(tmp_path), 0) == []


class TestUploadQuota:
    def test_doc_count_limit_rejects_before_heavy_work(self, isolated_kb, monkeypatch):
        monkeypatch.setattr(ds, "UPLOAD_QUOTA", {"max_docs_per_kb": 1, "max_total_mb": 0})
        kb = "quotakb"
        p1 = self._doc(isolated_kb, "one.md", "第一篇：蜂刺是一把冲锋枪。")
        p2 = self._doc(isolated_kb, "two.md", "第二篇：警长是一把手枪。")
        ds.upload_and_process(p1, "one.md", kb)
        with pytest.raises(ValueError, match="上限"):
            ds.upload_and_process(p2, "two.md", kb)
        assert len(ds.list_documents(kb)) == 1, "超限文档不得入库"

    def test_total_size_limit_rejects(self, isolated_kb, monkeypatch):
        monkeypatch.setattr(ds, "UPLOAD_QUOTA", {"max_docs_per_kb": 0, "max_total_mb": 0.0001})  # ~105 字节
        uploads_dir = isolated_kb / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        (uploads_dir / "dummy.bin").write_bytes(b"x" * 1024)  # 上传目录里已有 1KB，超过 ~105B 阈值
        p = self._doc(isolated_kb, "doc.md", "正文：猎枭是无畏契约中的侦查型英雄。")
        with pytest.raises(ValueError, match="空间已满"):
            ds.upload_and_process(p, "doc.md", "sizekb")

    @staticmethod
    def _doc(tmp_path, name, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return str(p)
