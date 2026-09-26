# 【新增 v3.8】用户反馈闭环：对每条 AI 回复点赞/点踩，落库供 badcase 回流
# ------------------------------------------------------------
# 业务闭环设计：
#   1. 前端在 assistant 消息气泡下点 👍/👎 → POST /api/feedback
#   2. 同一 question+answer 重复评价时覆盖原记录（更新 rating，不插新行）
#   3. 落库记录作为 badcase 回流的数据源（点踩=优先回流候选）
# 【W8-卡6】点踩上下文捕获：context_json 存最近 ≤3 轮完整对话、meta_json 存
#   领域/回答路径/trace_id——坏例分析时可见"之前问了什么导致这次答歪"，
#   Agent 时代可凭 trace_id 回放每一步。分析流程见 docs/坏例闭环流程-点踩分析.md
# 存储：SQLite 单文件（data/feedback.db），与 tickets.db 同模式，毕设量级无需引入 ORM
# ------------------------------------------------------------
import json
import os
import sqlite3
import threading

from config.settings import DATA_DIR

_lock = threading.Lock()
_DB_PATH = str(DATA_DIR / "feedback.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,                   -- 来源会话
    question     TEXT,                   -- 用户问题
    answer       TEXT,                   -- 被评价的 AI 回答
    rating       TEXT CHECK(rating IN ('up','down')),  -- up=点赞 / down=点踩
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    context_json TEXT,                   -- 【W8-卡6】最近 ≤3 轮完整对话（JSON 数组），旧数据为 NULL
    meta_json    TEXT                    -- 【W8-卡6】领域/kb_id/回答路径/trace_id（JSON 对象），旧数据为 NULL
);
"""

# 【W8-卡6】v3.8 老库升级：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，
# 缺列时 ALTER 补齐（旧数据留 NULL，向后兼容）
_MIGRATE_COLUMNS = ("context_json", "meta_json")


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
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(feedback)")}
            for col in _MIGRATE_COLUMNS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE feedback ADD COLUMN {col} TEXT")
            conn.commit()
        finally:
            conn.close()


_init_db()


def save_feedback(session_id: str, question: str, answer: str, rating: str,
                  context: list | None = None, meta: dict | None = None) -> str:
    """记录评价；同 question+answer 已有记录则覆盖（更新 rating/上下文/时间），返回动作 'updated'/'created'。

    context: 最近 ≤3 轮完整对话（[{role, content}, ...]，含本轮），旧调用不传落 NULL
    meta: 领域/回答路径/trace_id 等附加上下文（dict），trace_id 预留关联卡 2 Agent 轨迹
    """
    context_json = json.dumps(context, ensure_ascii=False) if context is not None else None
    meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT id FROM feedback WHERE question=? AND answer=? LIMIT 1",
                (question, answer)).fetchone()
            if row:
                conn.execute(
                    "UPDATE feedback SET rating=?, created_at=datetime('now','localtime'),"
                    " context_json=?, meta_json=? WHERE id=?",
                    (rating, context_json, meta_json, row["id"]))
                action = "updated"
            else:
                conn.execute(
                    "INSERT INTO feedback (session_id, question, answer, rating, context_json, meta_json)"
                    " VALUES (?,?,?,?,?,?)",
                    (session_id, question, answer, rating, context_json, meta_json))
                action = "created"
            conn.commit()
        finally:
            conn.close()
    return action


def list_recent(limit: int = 50, rating: str | None = None) -> list[dict]:
    """最近 N 条反馈，默认最新在前（管理页只读查询用）；rating='down' 时只看点踩坏例"""
    with _lock:
        conn = _conn()
        try:
            if rating:
                rows = conn.execute(
                    "SELECT * FROM feedback WHERE rating=? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (rating, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM feedback ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,)).fetchall()
        finally:
            conn.close()
    items = [dict(r) for r in rows]
    for item in items:  # JSON 列还原为结构化数据，空值原样透传（旧数据/旧调用不报错）
        item["context"] = json.loads(item.pop("context_json")) if item["context_json"] else None
        item["meta"] = json.loads(item.pop("meta_json")) if item["meta_json"] else None
    return items
