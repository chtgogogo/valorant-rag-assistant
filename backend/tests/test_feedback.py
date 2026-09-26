# 【W8-卡6】点踩上下文捕获回归测试：context_json/meta_json 落库与还原 /
# 旧格式（v3.8 四参）调用兼容 / rating 过滤（坏例闭环入口）/ 覆盖更新同步刷新上下文 /
# 旧库（无新列）自动迁移且旧数据保留 / router 层超传截断与旧格式 body 兼容
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.feedback_service as fs
from routers.feedback_router import feedback_router


@pytest.fixture()
def fb_db(monkeypatch, tmp_path):
    """隔离：临时 feedback.db，重跑建表+迁移逻辑"""
    monkeypatch.setattr(fs, "_DB_PATH", str(tmp_path / "feedback.db"))
    fs._init_db()
    yield tmp_path / "feedback.db"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(feedback_router, prefix="/api/feedback")
    return TestClient(app)


def _three_turns():
    return [
        {"role": "user", "content": "捷风怎么玩"},
        {"role": "assistant", "content": "捷风是决斗者，擅长 vertical 打法"},
        {"role": "user", "content": "那雷兹呢"},
        {"role": "assistant", "content": "雷兹偏爆发 satchel 位移"},
        {"role": "user", "content": "捷风和雷兹都是决斗类型英雄，玩法和技能有什么不同"},
        {"role": "assistant", "content": "晚安焰火提供团队视野（被点踩的回答）"},
    ]


class TestContextCapture:
    def test_save_with_context_and_meta_roundtrip(self, fb_db):
        """点踩存最近 3 轮上下文 + meta：库中可查且字段完整（验收标准 1）"""
        meta = {"domain": "valorant", "path": "workflow", "trace_id": None}
        fs.save_feedback("s1", "捷风和雷兹有什么不同", "晚安焰火提供团队视野", "down",
                         context=_three_turns(), meta=meta)
        items = fs.list_recent()
        assert len(items) == 1
        rec = items[0]
        assert rec["context"] == _three_turns()  # JSON 还原为结构化列表，顺序与内容一致
        assert rec["meta"] == meta
        assert "context_json" not in rec and "meta_json" not in rec  # 原始 JSON 串不外露

    def test_legacy_call_compatible(self, fb_db):
        """旧版调用（只传 4 参）不崩：context/meta 落 NULL，查询原样透传（验收标准 2）"""
        assert fs.save_feedback("s1", "q", "a", "up") == "created"
        rec = fs.list_recent()[0]
        assert rec["context"] is None and rec["meta"] is None

    def test_overwrite_refreshes_context(self, fb_db):
        """同 QA 重复评价覆盖原记录：rating 与上下文一并刷新，不插新行"""
        fs.save_feedback("s1", "q", "a", "down")
        fs.save_feedback("s1", "q", "a", "up", context=[{"role": "user", "content": "q"}],
                         meta={"domain": "law"})
        items = fs.list_recent()
        assert len(items) == 1
        assert items[0]["rating"] == "up"
        assert items[0]["context"] == [{"role": "user", "content": "q"}]
        assert items[0]["meta"] == {"domain": "law"}


class TestRatingFilter:
    def test_filter_down_and_up(self, fb_db):
        """list_recent 支持 rating=down 过滤：坏例闭环入口（只看踩/只看赞）"""
        fs.save_feedback("s1", "q1", "a1", "up")
        fs.save_feedback("s2", "q2", "a2", "down")
        fs.save_feedback("s3", "q3", "a3", "down")
        downs = fs.list_recent(rating="down")
        assert [r["question"] for r in downs] == ["q3", "q2"]  # 只含点踩，最新在前
        ups = fs.list_recent(rating="up")
        assert [r["question"] for r in ups] == ["q1"]
        assert len(fs.list_recent()) == 3  # 不过滤时全部返回


class TestLegacyDbMigration:
    def test_migrate_old_db_keeps_rows(self, monkeypatch, tmp_path):
        """v3.8 老库（5 列、已有数据）升级：自动补两列，旧数据保留且 context=None"""
        import sqlite3
        db = tmp_path / "feedback.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, question TEXT, answer TEXT,
                rating TEXT CHECK(rating IN ('up','down')),
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            INSERT INTO feedback (session_id, question, answer, rating)
            VALUES ('old', '旧问题', '旧回答', 'down');
        """)
        conn.commit()
        conn.close()

        monkeypatch.setattr(fs, "_DB_PATH", str(db))
        fs._init_db()  # 迁移：ALTER TABLE 补列，不丢数据

        items = fs.list_recent()
        assert len(items) == 1
        assert items[0]["question"] == "旧问题" and items[0]["rating"] == "down"
        assert items[0]["context"] is None and items[0]["meta"] is None
        # 迁移后的库可直接写入新格式
        fs.save_feedback("s2", "新问题", "新回答", "down",
                         context=[{"role": "user", "content": "新问题"}], meta={"domain": "law"})
        assert len(fs.list_recent()) == 2


class TestRouter:
    def test_post_truncates_context_to_three_turns(self, fb_db, client):
        """router 层兜底截断：前端误传 4 轮（8 条）只留最近 3 轮"""
        four_turns = [{"role": "user", "content": f"q{i}"} for i in range(8)]
        resp = client.post("/api/feedback", json={
            "session_id": "s1", "question": "q", "answer": "a", "rating": "down",
            "context": four_turns, "meta": {"domain": "valorant", "path": "workflow"}})
        assert resp.status_code == 200
        assert resp.json()["data"]["action"] == "created"
        ctx = fs.list_recent()[0]["context"]
        assert ctx == four_turns[-6:]  # 最近 3 轮 = 6 条

    def test_post_legacy_body_compatible(self, fb_db, client):
        """旧格式 body（只有 4 字段）经 router 也不报错"""
        resp = client.post("/api/feedback", json={
            "session_id": "s1", "question": "q", "answer": "a", "rating": "up"})
        assert resp.status_code == 200
        rec = fs.list_recent()[0]
        assert rec["context"] is None and rec["meta"] is None
