# 【W8-卡4】动作类工具 + 人工确认门测试
# 覆盖任务卡三条验收红线：
#   ① 未确认绝不落库（工具层 + 端到端两层验证）
#   ② 确认后正常落库（向量库 + 文档清单双断言）
#   ③ 凭据伪造 404 / 过期 410 / 重放 409 / 密码错与未配置密码 403 全部拒绝
# 另覆盖：query_tickets 只读工具（格式化/过滤/夹紧）、run_agent 返回 pending_proposals
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import services.document_service as ds
import services.vector_service as vs
from routers.agent_router import agent_router
from services.agent import actions as actions_mod
from services.agent.actions import ProposalRejected, confirm_proposal, create_proposal
from services.agent.loop import run_agent
from services.agent.tools import execute_tool
from utils import auth as auth_mod


# ---------- 公共夹具 ----------

@pytest.fixture(autouse=True)
def _clean_proposals():
    """方案 store 是模块级内存 dict：每个用例前后清空，互不串扰"""
    actions_mod._proposals.clear()
    yield
    actions_mod._proposals.clear()


@pytest.fixture
def fake_kb(monkeypatch, tmp_path):
    """隔离的临时知识库：假 embedding（确定性向量）+ 临时 chroma + 临时文档清单"""
    import hashlib

    import numpy as np

    monkeypatch.setattr(vs, "VECTOR_DB_PATH", str(tmp_path / "chroma"))
    vs._chroma_client = None

    class FakeST:
        def encode(self, texts, **kw):
            vecs = [[b / 255.0 for b in hashlib.sha256(t.encode()).digest()[:8]]
                    for t in texts]
            return np.array(vecs)  # 与真模型一致：numpy 数组（add_chunks 调 .tolist()）

    monkeypatch.setattr(vs, "_get_embedding_model", lambda: FakeST())
    monkeypatch.setattr(ds, "UPLOAD_PATH", str(tmp_path / "uploads"))
    yield tmp_path
    vs._chroma_client = None  # 防止持久化客户端带脏目录漏进其他测试


def _chunk_count(kb_id="valorant") -> int:
    client = vs._get_chroma_client()
    try:
        return client.get_or_create_collection(kb_id).count()
    except Exception:
        return 0


# ---------- 工具层：query_tickets ----------

class TestQueryTickets:
    def test_formats_rows(self, monkeypatch):
        import services.ticket_service as ts
        rows = [{"id": "tk_aaa", "status": "open", "question": "捷风大招是什么",
                 "created_at": "2026-09-27 10:00:00", "answer": ""},
                {"id": "tk_bbb", "status": "resolved", "question": "雷兹怎么玩",
                 "created_at": "2026-09-27 09:00:00", "answer": "用位移接爆闪"}]
        monkeypatch.setattr(ts, "list_tickets", lambda status=None, limit=10: rows)
        ok, text, sources = execute_tool("query_tickets", {"status": "all"}, default_kb_id="valorant")
        assert ok is True and sources == []
        assert "tk_aaa" in text and "tk_bbb" in text and "2 条工单" in text

    def test_status_passthrough_and_answer_summary(self, monkeypatch):
        import services.ticket_service as ts
        captured = {}

        def fake_list(status=None, limit=10):
            captured["status"], captured["limit"] = status, limit
            return [{"id": "tk_c", "status": "open", "question": "q",
                     "created_at": "t", "answer": ""}]

        monkeypatch.setattr(ts, "list_tickets", fake_list)
        execute_tool("query_tickets", {"status": "open", "limit": 5}, default_kb_id="valorant")
        assert captured == {"status": "open", "limit": 5}

    def test_bad_status_rejected_without_query(self, monkeypatch):
        import services.ticket_service as ts
        monkeypatch.setattr(ts, "list_tickets",
                            lambda status=None, limit=10: pytest.fail("非法 status 不应触达数据库"))
        ok, text, _ = execute_tool("query_tickets", {"status": "hacked"}, default_kb_id="valorant")
        assert ok is False and "status" in text

    def test_empty_result(self, monkeypatch):
        import services.ticket_service as ts
        monkeypatch.setattr(ts, "list_tickets", lambda status=None, limit=10: [])
        ok, text, _ = execute_tool("query_tickets", {}, default_kb_id="valorant")
        assert ok is True and "没有符合条件的工单" in text

    def test_limit_clamped(self, monkeypatch):
        import services.ticket_service as ts
        captured = {}
        monkeypatch.setattr(ts, "list_tickets",
                            lambda status=None, limit=10: (captured.update(limit=limit), [])[1])
        execute_tool("query_tickets", {"limit": 9999}, default_kb_id="valorant")
        assert captured["limit"] == 50


# ---------- 工具层：propose_kb_write（只产方案，绝不落库） ----------

class TestProposeTool:
    def test_empty_content_rejected(self):
        ok, text, _ = execute_tool("propose_kb_write", {"content": "  "},
                                   default_kb_id="valorant")
        assert ok is False and "content" in text

    def test_unknown_kb_rejected(self):
        ok, text, _ = execute_tool("propose_kb_write",
                                   {"content": "捷风测试内容", "kb_id": "bogus_kb"},
                                   default_kb_id="valorant")
        assert ok is False and "bogus_kb" in text

    def test_propose_creates_pending_proposal_not_write(self, fake_kb):
        ctx = {}
        ok, text, sources = execute_tool(
            "propose_kb_write",
            {"content": "【捷风·新技能】旋风：在原地生成气流护盾。", "reason": "用户要求补充技能资料"},
            default_kb_id="valorant", ctx=ctx)
        assert ok is True and sources == []
        assert "尚未写入" in text and "管理员" in text
        # store 里恰好一条 pending 方案，ctx 收到摘要（回传前端的素材）
        assert len(actions_mod._proposals) == 1
        (proposal,) = actions_mod._proposals.values()
        assert proposal["status"] == "pending" and proposal["kb_id"] == "valorant"
        assert len(ctx["pending_proposals"]) == 1
        assert ctx["pending_proposals"][0]["proposal_id"] == proposal["proposal_id"]
        # 红线①：propose 之后向量库与文档清单都必须是空的
        assert _chunk_count() == 0
        assert ds._load_doc_list("valorant") == []


# ---------- 确认门服务层 ----------

class TestConfirmGate:
    def _propose(self, content="【测试】捷风的旋风护盾持续 8 秒。") -> dict:
        return create_proposal(kb_id="valorant", content=content, reason="测试")

    def test_fake_id_404(self):
        with pytest.raises(ProposalRejected) as ei:
            confirm_proposal("prop_does_not_exist")
        assert ei.value.status_code == 404

    def test_expired_410(self):
        proposal = self._propose()
        actions_mod._proposals[proposal["proposal_id"]]["expires_at"] = 0  # 直接过期
        with pytest.raises(ProposalRejected) as ei:
            confirm_proposal(proposal["proposal_id"])
        assert ei.value.status_code == 410

    def test_confirm_writes_kb(self, fake_kb):
        proposal = self._propose()
        result = confirm_proposal(proposal["proposal_id"])
        assert result["kb_id"] == "valorant"
        assert result["chunk_count"] >= 1
        assert result["doc_id"].startswith("agentkb_")
        # 红线②：确认后向量库有块、文档清单有注册记录（知识库管理页可见可删）
        assert _chunk_count() > 0
        doc_list = ds._load_doc_list("valorant")
        assert any(d["doc_id"] == result["doc_id"] and d["origin"] == "agent_proposed"
                   for d in doc_list)

    def test_replay_409_after_confirmed(self, fake_kb):
        proposal = self._propose()
        confirm_proposal(proposal["proposal_id"])
        with pytest.raises(ProposalRejected) as ei:  # 同一凭据第二次 = 重放
            confirm_proposal(proposal["proposal_id"])
        assert ei.value.status_code == 409

    def test_manual_status_conflict_409(self):
        proposal = self._propose()
        actions_mod._proposals[proposal["proposal_id"]]["status"] = "confirmed"
        with pytest.raises(ProposalRejected) as ei:
            confirm_proposal(proposal["proposal_id"])
        assert ei.value.status_code == 409


# ---------- 端到端：Agent 对话产出方案 → HTTP 确认门 ----------

class _FakeDecideLLM:
    """决策模型脚本：第一轮调 propose_kb_write，第二轮停止（进终答）"""
    def __init__(self):
        self.invocations = 0

    def bind_tools(self, spec):
        return self

    def invoke(self, messages):
        self.invocations += 1
        if self.invocations == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "propose_kb_write",
                 "args": {"content": "【捷风·新技能】旋风：原地生成气流护盾，持续 8 秒。",
                          "reason": "用户要求把技能补充进知识库"},
                 "id": "call_p1"}])
        return AIMessage(content="", tool_calls=[])


@pytest.fixture
def agent_client(monkeypatch, tmp_path, fake_kb):
    """挂 agent_router 的测试客户端：假决策/终答模型 + 关限流 + 管理密码已知"""
    import services.agent.loop as loop_mod

    decide = _FakeDecideLLM()

    class _FakeAnswerLLM:
        def invoke(self, messages):
            return AIMessage(content="已为管理员生成写入方案，等待确认。")

    monkeypatch.setattr(loop_mod, "make_llm",
                        lambda thinking, timeout: decide if not thinking else _FakeAnswerLLM())
    monkeypatch.setattr("routers.agent_router.check_chat_rate_limit", lambda ip: None)
    monkeypatch.setattr(auth_mod, "ADMIN_PASSWORD", "admin-secret")

    app = FastAPI()
    app.include_router(agent_router, prefix="/api/agent")
    client = TestClient(app)
    client.headers.update({"X-Admin-Key": "admin-secret"})
    return client


class TestEndToEndConfirmGate:
    def test_propose_then_confirm_then_replay(self, agent_client):
        # ① 对话：Agent 调 propose 生成方案，响应带 pending_proposals
        r = agent_client.post("/api/agent/chat", json={
            "session_id": "sess-e2e", "kb_id": "valorant",
            "question": "把捷风的新技能加进知识库"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["pending_proposals"]) == 1
        pid = data["pending_proposals"][0]["proposal_id"]
        assert data["pending_proposals"][0]["kb_id"] == "valorant"
        # 红线①（端到端）：方案在响应里，但库里什么都没有
        assert _chunk_count() == 0

        # ② 错密码确认 → 403
        r = agent_client.post("/api/agent/kb-write/confirm", json={"proposal_id": pid},
                              headers={"X-Admin-Key": "wrong-password"})
        assert r.status_code == 403
        assert _chunk_count() == 0  # 密码错绝不落库

        # ③ 正确管理密码确认 → 200 落库
        r = agent_client.post("/api/agent/kb-write/confirm", json={"proposal_id": pid})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["doc_id"].startswith("agentkb_") and body["chunk_count"] >= 1
        assert _chunk_count() > 0 and ds._load_doc_list("valorant")

        # ④ 同一凭据重放 → 409
        r = agent_client.post("/api/agent/kb-write/confirm", json={"proposal_id": pid})
        assert r.status_code == 409

    def test_confirm_fake_id_404(self, agent_client):
        r = agent_client.post("/api/agent/kb-write/confirm",
                              json={"proposal_id": "prop_forged"})
        assert r.status_code == 404

    def test_confirm_without_admin_password_403(self, agent_client, monkeypatch):
        """fail-closed：服务端未配置 ADMIN_PASSWORD 时确认接口全部 403"""
        monkeypatch.setattr(auth_mod, "ADMIN_PASSWORD", "")
        r = agent_client.post("/api/agent/kb-write/confirm",
                              json={"proposal_id": "prop_x"})
        assert r.status_code == 403


# ---------- 循环层：run_agent 返回 pending_proposals ----------

class TestRunAgentCarriesProposals:
    def test_pending_proposals_returned(self, fake_kb, monkeypatch):
        import services.agent.loop as loop_mod
        decide = _FakeDecideLLM()

        class _FakeAnswerLLM:
            def invoke(self, messages):
                return AIMessage(content="方案已提交，等待管理员确认。")

        monkeypatch.setattr(loop_mod, "make_llm",
                            lambda thinking, timeout: decide if not thinking else _FakeAnswerLLM())
        out = run_agent("把捷风新技能加进知识库", kb_id="valorant")
        assert out["degraded"] is False
        assert len(out["pending_proposals"]) == 1
        assert out["pending_proposals"][0]["status"] == "pending"
        assert _chunk_count() == 0  # 循环结束（未确认）依然零落库
