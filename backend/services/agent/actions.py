# 【W8-卡4】动作类工具的安全边界：知识库写入方案 store + 人工确认门
# ------------------------------------------------------------
# 设计（技术决策清单 D4 拍板 = 写操作进 Agent + 人工确认门）：
#   1. propose_kb_write 工具只生成"写入方案"（目标库/内容/理由），绝不执行写入；
#   2. 方案存内存：进程重启即全部失效（旧凭据天然作废），TTL 10 分钟过期，
#      一次性消费（确认即核销，防重放）；
#   3. 唯一的写入执行入口是 confirm_proposal：先在锁内核销凭据（存在 → 未过期
#      → 未用过 三关），再走与工单回流 feed_ticket_to_kb 同款的入库链路
#      （split_text → add_chunks → 文档清单注册），入库失败同样销毁凭据，
#      杜绝半成品重放；
#   4. HTTP 层的确认请求必须携带 X-Admin-Key（复用 v3.30 管理密码门，fail-closed：
#      未配置 ADMIN_PASSWORD 时确认接口全部 403）。
# ------------------------------------------------------------
import threading
import time
import uuid

from config.settings import resolve_kb_id
from schemas.models import DocumentChunk

PROPOSAL_TTL_SECONDS = 600  # 方案有效期 10 分钟：人工确认不该无限期有效，超时须重新生成

_lock = threading.Lock()
_proposals: dict = {}  # proposal_id -> 方案单据（内存 store；重启清空是特性不是缺陷）


class ProposalRejected(Exception):
    """写入方案确认被拒。status_code 供 HTTP 层直接转译：
    404 伪造凭据 / 410 已过期 / 409 重放或已被处理 / 502 入库失败"""
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_proposal(kb_id: str, content: str, reason: str = "") -> dict:
    """生成知识库写入方案（只登记，不执行任何写操作）
    :raises HTTPException: kb_id 非法/未知（resolve_kb_id 的白名单防线）"""
    content = (content or "").strip()
    if not content:
        raise ValueError("写入内容不能为空")
    resolved_kb = resolve_kb_id(kb_id)
    proposal = {
        "proposal_id": f"prop_{uuid.uuid4().hex[:12]}",
        "kb_id": resolved_kb,
        "content": content,
        "reason": (reason or "").strip() or "（未说明理由）",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": time.time() + PROPOSAL_TTL_SECONDS,
        "status": "pending",
    }
    now = time.time()
    with _lock:
        # 惰性清理：已确认/已过期超过一个 TTL 的旧条目顺手清掉，内存有界
        # （已确认条目要保留一段时间供重放识别为 409，不能确认即删）
        stale = [pid for pid, v in _proposals.items()
                 if v["status"] != "pending" and now > v["expires_at"] + PROPOSAL_TTL_SECONDS]
        for pid in stale:
            _proposals.pop(pid, None)
        _proposals[proposal["proposal_id"]] = proposal
    return proposal


def proposal_summary(proposal: dict) -> dict:
    """回传前端的方案摘要（content 只给预览，全文在管理员确认时以预览+字数呈现足够决策）"""
    return {
        "proposal_id": proposal["proposal_id"],
        "kb_id": proposal["kb_id"],
        "reason": proposal["reason"],
        "content_preview": proposal["content"][:200],
        "content_chars": len(proposal["content"]),
        "expires_at": proposal["expires_at"],
        "status": proposal["status"],
    }


def confirm_proposal(proposal_id: str) -> dict:
    """人工确认门：核销方案凭据并执行知识库写入（全系统唯一的 Agent 写入执行入口）
    :raises ProposalRejected: 伪造(404) / 过期(410) / 重放(409) / 入库失败(502)
    :return: {"doc_id", "kb_id", "chunk_count"}"""
    with _lock:
        proposal = _proposals.get(proposal_id)
        if proposal is None:
            raise ProposalRejected(404, f"写入方案不存在：{proposal_id}（凭据无效或已被清理）")
        if proposal["status"] != "pending":
            raise ProposalRejected(409, "该写入方案已被确认过，凭据已失效（防重放）")
        if time.time() > proposal["expires_at"]:
            _proposals.pop(proposal_id, None)
            raise ProposalRejected(410, "写入方案已过期（10 分钟有效期），请重新发起")
        # 先核销再执行：并发确认同一凭据时，第二个请求必然撞上 status!=pending 的 409
        proposal["status"] = "confirmed"

    # 已确认条目有意保留（create_proposal 惰性清理）：之后同凭据再来 = 409 重放，
    # 而不是含糊的 404；入库失败同样保留，禁止拿半成品凭据重试
    return execute_kb_write(proposal)


def execute_kb_write(proposal: dict) -> dict:
    """执行入库（与工单回流 feed_ticket_to_kb 同款链路：切分 → 向量入库 → 文档清单注册）"""
    from services.document_service import (split_text, _get_doc_list_lock,
                                           _load_doc_list, _save_doc_list)
    from services.vector_service import add_chunks

    kb_id = proposal["kb_id"]
    doc_id = f"agentkb_{proposal['proposal_id'][:12]}"
    doc_name = f"Agent写入_{proposal['created_at'].replace(' ', '_').replace(':', '')}.md"
    pieces = split_text(proposal["content"]) or [proposal["content"]]

    chunks = [DocumentChunk(
        content=p,
        metadata={"doc_id": doc_id, "doc_name": doc_name, "kb_id": kb_id,
                  "origin": "agent_proposed"},
    ) for p in pieces]
    if not add_chunks(chunks, kb_id):
        raise ProposalRejected(502, "向量入库失败，本次写入未生效（请重新发起方案）")

    # 记入文档清单：知识库管理页可见可删（origin 标记来源，与 ticket_feedback 同惯例）
    with _get_doc_list_lock(kb_id):
        doc_list = _load_doc_list(kb_id)
        doc_list.append({
            "doc_id": doc_id,
            "doc_name": doc_name,
            "upload_time": proposal["created_at"],
            "chunk_count": len(chunks),
            "origin": "agent_proposed",
        })
        _save_doc_list(kb_id, doc_list)
    return {"doc_id": doc_id, "kb_id": kb_id, "chunk_count": len(chunks)}
