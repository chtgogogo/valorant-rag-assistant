# 【v3.29/v3.30】管理鉴权与工单回流幂等测试
import pytest
from fastapi import HTTPException

from utils import auth
from utils.auth import verify_admin


class TestVerifyAdmin:
    def test_no_password_configured_403(self, monkeypatch):
        """fail-closed：未配置 ADMIN_PASSWORD 时一律 403（管理功能默认锁定）"""
        monkeypatch.setattr(auth, "ADMIN_PASSWORD", "")
        with pytest.raises(HTTPException) as ei:
            verify_admin(x_admin_key="")
        assert ei.value.status_code == 403
        with pytest.raises(HTTPException):
            verify_admin(x_admin_key="anything")

    def test_wrong_key_403(self, monkeypatch):
        monkeypatch.setattr(auth, "ADMIN_PASSWORD", "admin-secret")
        with pytest.raises(HTTPException) as ei:
            verify_admin(x_admin_key="wrong")
        assert ei.value.status_code == 403

    def test_correct_key_passes(self, monkeypatch):
        monkeypatch.setattr(auth, "ADMIN_PASSWORD", "admin-secret")
        assert verify_admin(x_admin_key="admin-secret") is None


class TestTicketFeedbackIdempotent:
    """同一工单重复回流（编辑答案重发）：doc_list 不产生重复记录"""

    def test_feed_twice_single_record(self, monkeypatch, tmp_path):
        import services.ticket_service as ts
        import services.document_service as ds
        import services.vector_service as vs

        # 隔离：临时向量库 + 假 embedding + 临时 doc_list 目录
        monkeypatch.setattr(vs, "VECTOR_DB_PATH", str(tmp_path / "chroma"))
        vs._chroma_client = None

        class FakeST:
            def encode(self, texts, **kw):
                import hashlib
                import numpy as np
                vecs = [[b / 255.0 for b in hashlib.sha256(t.encode()).digest()[:8]]
                        for t in texts]
                return np.array(vecs)  # 与真模型一致：numpy 数组（add_chunks 调 .tolist()）

        monkeypatch.setattr(vs, "_get_embedding_model", lambda: FakeST())
        monkeypatch.setattr(ds, "UPLOAD_PATH", str(tmp_path / "uploads"))

        ticket = {"id": "tk_testid12345", "question": "测试个案问题",
                  "kb_id": "lawtest", "answer": "初版答案", "resolved_at": "t"}

        doc_id_1 = ts.feed_ticket_to_kb({**ticket, "answer": "初版答案"})
        doc_id_2 = ts.feed_ticket_to_kb({**ticket, "answer": "修改后的答案"})

        assert doc_id_1 == doc_id_2  # 同一工单 doc_id 固定
        doc_list = ds._load_doc_list("lawtest")
        records = [d for d in doc_list if d["doc_id"] == doc_id_1]
        assert len(records) == 1, f"重复回流产生 {len(records)} 条重复注册记录（应恰好 1 条）"
        vs._chroma_client = None


class TestTicketDelete:
    """【v3.31】删除工单记录（已关闭工单的清理动作）"""

    def test_delete_closed_ticket(self, monkeypatch, tmp_path):
        import services.ticket_service as ts

        monkeypatch.setattr(ts, "_DB_PATH", str(tmp_path / "tickets.db"))
        ts._init_db()

        tid = ts.create_ticket("测试删除问题", "测试改写", 0.42, "valorant", "sess-del")
        closed = ts.close_ticket(tid)
        assert closed["status"] == "closed"

        deleted = ts.delete_ticket(tid)
        assert deleted["id"] == tid
        assert ts.get_ticket(tid) is None  # 记录已消失

    def test_delete_missing_ticket_raises(self, monkeypatch, tmp_path):
        import services.ticket_service as ts

        monkeypatch.setattr(ts, "_DB_PATH", str(tmp_path / "tickets.db"))
        ts._init_db()
        with pytest.raises(ValueError):
            ts.delete_ticket("tk_not_exist")
