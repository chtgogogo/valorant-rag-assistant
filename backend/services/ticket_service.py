# 【新增 v3.5】客服工单与知识回流：答不上 → 自动建工单 → 人工填标准答案 → 回流知识库
# ------------------------------------------------------------
# 业务闭环设计：
#   1. chat 链路低置信兜底时自动 create_ticket（失败不阻塞主流程）
#   2. 人工在工单页填标准答案 → resolve_ticket
#   3. resolve 时可选回流：问题+标准答案组成 FAQ 块入库（复用 add_chunks），
#      下次同类问题重排分数达标即直接命中 —— 知识库越用越厚
# 存储：SQLite 单文件（data/tickets.db），毕设量级无需引入 ORM
# ------------------------------------------------------------
import os
import sqlite3
import threading
import time
import uuid

from schemas.models import DocumentChunk
from config.settings import TICKET_CONFIG

_lock = threading.Lock()
_DB_PATH = TICKET_CONFIG["db_path"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,       -- 工单号 tk_XXXXXXXXXXXX
    session_id  TEXT,                   -- 来源会话
    question    TEXT NOT NULL,          -- 用户原始问题
    rewritten   TEXT,                   -- 改写后的检索问题
    top1_score  REAL,                   -- 兜底时的最高置信分（便于人工判断差多少）
    kb_id       TEXT,                   -- 应归入的知识库
    status      TEXT DEFAULT 'open',    -- open / resolved / closed
    answer      TEXT DEFAULT '',        -- 人工标准答案
    fed_back    INTEGER DEFAULT 0,      -- 是否已回流知识库 0/1
    doc_id      TEXT DEFAULT '',        -- 回流生成的文档ID
    created_at  TEXT,
    resolved_at TEXT
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


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def create_ticket(question: str, rewritten: str, top1_score: float,
                  kb_id: str, session_id: str = "") -> str:
    """创建工单，返回工单号。任何失败抛给调用方处理（chat 链路会静默降级）"""
    ticket_id = f"tk_{uuid.uuid4().hex[:12]}"
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO tickets (id, session_id, question, rewritten, top1_score, kb_id, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'open', ?)",
                (ticket_id, session_id, question, rewritten, top1_score, kb_id, _now()))
            conn.commit()
        finally:
            conn.close()
    return ticket_id


def has_recent_duplicate(session_id: str, question: str, minutes: int) -> bool:
    """冷却防刷：同会话近 N 分钟内同问题已建过单则不再重复建（minutes<=0 视为不限制）"""
    if not session_id or minutes <= 0:
        return False
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - minutes * 60))
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM tickets WHERE session_id=? AND question=? AND created_at>=? LIMIT 1",
                (session_id, question, cutoff)).fetchone()
        finally:
            conn.close()
    return row is not None


def list_tickets(status: str = None, limit: int = 100) -> list[dict]:
    """工单列表，默认最新在前，可按状态筛选"""
    sql = "SELECT * FROM tickets"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id: str) -> dict | None:
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def feed_ticket_to_kb(ticket: dict) -> str:
    """把人工标准答案回流入知识库：组成 FAQ 块，复用向量入库链路。
    :return: 生成的 doc_id"""
    from services.document_service import split_text, _load_doc_list, _save_doc_list
    from services.vector_service import add_chunks

    kb_id = ticket["kb_id"]
    doc_name = f"工单回流_{ticket['id'][:11]}.md"
    # 问题+答案组成一条自包含的 FAQ 文本，检索时用户问法与【问题】部分天然接近
    text = (f"【用户问题】{ticket['question']}\n\n"
            f"【标准答案】{ticket['answer']}\n\n"
            f"（来源：人工客服工单，处理时间 {ticket.get('resolved_at') or _now()}）")
    pieces = split_text(text) or [text]
    doc_id = f"tkdoc_{ticket['id'][:12]}"
    chunks = [DocumentChunk(
        content=p,
        metadata={"doc_id": doc_id, "doc_name": doc_name, "kb_id": kb_id},
    ) for p in pieces]
    if not add_chunks(chunks, kb_id):
        raise RuntimeError("向量入库失败，工单未回流")
    # 记入文档清单，知识库管理页可见可删
    doc_list = _load_doc_list(kb_id)
    doc_list.append({
        "doc_id": doc_id,
        "doc_name": doc_name,
        "upload_time": _now(),
        "chunk_count": len(chunks),
        "origin": "ticket_feedback",
    })
    _save_doc_list(kb_id, doc_list)
    return doc_id


def resolve_ticket(ticket_id: str, answer: str, feedback: bool = True) -> dict:
    """人工处理工单：填标准答案并关闭；feedback=True 时同步回流知识库
    :return: {'ticket': 更新后工单, 'doc_id': 回流文档ID(未回流为 '')}"""
    ticket = get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"工单不存在: {ticket_id}")
    if not answer.strip():
        raise ValueError("标准答案不能为空")

    doc_id = ""
    if feedback:
        doc_id = feed_ticket_to_kb({**ticket, "answer": answer, "resolved_at": _now()})

    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE tickets SET status='resolved', answer=?, fed_back=?, doc_id=?, resolved_at=? WHERE id=?",
                (answer, 1 if feedback else 0, doc_id, _now(), ticket_id))
            conn.commit()
        finally:
            conn.close()
    return {"ticket": get_ticket(ticket_id), "doc_id": doc_id}


def close_ticket(ticket_id: str) -> dict:
    """关闭工单（不填答案、不回流：重复问题/无效反馈场景）"""
    ticket = get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"工单不存在: {ticket_id}")
    with _lock:
        conn = _conn()
        try:
            conn.execute("UPDATE tickets SET status='closed', resolved_at=? WHERE id=?",
                         (_now(), ticket_id))
            conn.commit()
        finally:
            conn.close()
    return get_ticket(ticket_id)


def ticket_stats() -> dict:
    """闭环概览：各状态数量 + 回流率（评测与演示用）"""
    with _lock:
        conn = _conn()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
            open_n = conn.execute("SELECT COUNT(*) c FROM tickets WHERE status='open'").fetchone()["c"]
            resolved = conn.execute("SELECT COUNT(*) c FROM tickets WHERE status='resolved'").fetchone()["c"]
            fed = conn.execute("SELECT COUNT(*) c FROM tickets WHERE fed_back=1").fetchone()["c"]
        finally:
            conn.close()
    return {"total": total, "open": open_n, "resolved": resolved,
            "closed": total - open_n - resolved, "fed_back": fed}
