# 【新增 v3.8】用户反馈闭环：对每条 AI 回复点赞/点踩，落库供 badcase 回流
# ------------------------------------------------------------
# 业务闭环设计：
#   1. 前端在 assistant 消息气泡下点 👍/👎 → POST /api/feedback
#   2. 同一 question+answer 重复评价时覆盖原记录（更新 rating，不插新行）
#   3. 落库记录作为 badcase 回流的数据源（点踩=优先回流候选）
# 存储：SQLite 单文件（data/feedback.db），与 tickets.db 同模式，毕设量级无需引入 ORM
# ------------------------------------------------------------
import os
import sqlite3
import threading

from config.settings import DATA_DIR

_lock = threading.Lock()
_DB_PATH = str(DATA_DIR / "feedback.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,                   -- 来源会话
    question    TEXT,                   -- 用户问题
    answer      TEXT,                   -- 被评价的 AI 回答
    rating      TEXT CHECK(rating IN ('up','down')),  -- up=点赞 / down=点踩
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _lock:
        conn = _conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


_init_db()


def save_feedback(session_id: str, question: str, answer: str, rating: str) -> str:
    """记录评价；同 question+answer 已有记录则覆盖（更新 rating 与时间），返回动作 'updated'/'created'"""
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT id FROM feedback WHERE question=? AND answer=? LIMIT 1",
                (question, answer)).fetchone()
            if row:
                conn.execute(
                    "UPDATE feedback SET rating=?, created_at=datetime('now','localtime') WHERE id=?",
                    (rating, row["id"]))
                action = "updated"
            else:
                conn.execute(
                    "INSERT INTO feedback (session_id, question, answer, rating) VALUES (?,?,?,?)",
                    (session_id, question, answer, rating))
                action = "created"
            conn.commit()
        finally:
            conn.close()
    return action


def list_recent(limit: int = 50) -> list[dict]:
    """最近 N 条反馈，默认最新在前（管理页只读查询用）"""
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]
