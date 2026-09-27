# 【v3.34】Agent 失败对策补完回归测试（全 mock/临时目录，不依赖真模型/网络）
# 依据 agent-failure-playbook 41 死法审计的五处缺口，逐项闭环验证：
#   A. Agent 端点接并发闸门：/chat 队满 429、/stream SSE gate_full、还席防泄漏（CONC）
#   B. 决策 LLM 异常 → 降级 Workflow，异常不许抛给用户（ERR）
#   C. search 工具 kb_id 白名单：编造/跨域指库给可读错误且不触达检索管线（SEC-5）
#   D. Agent 轨迹 JSONL TTL 清理（存储治理补项）
#   E. 同参重复调用无进展提示（LOOP-2 最小对策：状态指纹）
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import services.agent.loop as loop_mod
import services.agent.tools as tools_mod
import services.chat_service as cs
from routers.agent_router import agent_router
from services.agent.loop import run_agent
from services.agent.tools import execute_tool
from utils.concurrency_gate import ConcurrencyGate
from utils.ops_maintenance import cleanup_old_traces


def _r(content="捷风的技能有瞬云", source="英雄介绍.md", doc_id="d1", score=0.9):
    from schemas.models import SearchResult
    return SearchResult(content=content, source=source, score=score, doc_id=doc_id)


def _tool_call_ai(query="雷兹 技能", call_id="call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "search_knowledge_base", "args": {"query": query}, "id": call_id}])


# ======================= A. Agent 端点并发闸门接线 =======================

@pytest.fixture()
def agent_client(monkeypatch):
    import routers.agent_router as ar
    monkeypatch.setattr(ar, "check_chat_rate_limit", lambda ip: None)
    app = FastAPI()
    app.include_router(agent_router, prefix="/api/agent")
    return TestClient(app)


class TestAgentGateWiring:
    def _stub_run_agent(self, monkeypatch):
        monkeypatch.setattr(loop_mod, "run_agent", lambda *a, **k: {
            "answer": "Agent答案", "sources": [], "steps": [],
            "degraded": False, "pending_proposals": []})

    def test_chat_returns_429_when_gate_full(self, agent_client, monkeypatch):
        import routers.agent_router as ar
        full_gate = ConcurrencyGate(1, 0, 0.1)
        full_gate.enter()  # 占死唯一席位
        monkeypatch.setattr(ar, "get_generation_gate", lambda: full_gate)
        self._stub_run_agent(monkeypatch)  # 若闸门失效会真进业务桩——429 即拦在闸门的证明

        resp = agent_client.post("/api/agent/chat",
                                 json={"session_id": "s1", "question": "雷兹技能", "kb_id": "valorant"})
        assert resp.status_code == 429
        assert "稍等" in resp.json()["detail"]

    def test_stream_gate_full_is_sse_error_event(self, agent_client, monkeypatch):
        import routers.agent_router as ar
        full_gate = ConcurrencyGate(1, 0, 0.1)
        full_gate.enter()
        monkeypatch.setattr(ar, "get_generation_gate", lambda: full_gate)
        self._stub_run_agent(monkeypatch)

        resp = agent_client.post("/api/agent/chat/stream",
                                 json={"session_id": "s1", "question": "雷兹技能", "kb_id": "valorant"})
        assert "gate_full" in resp.text and "error" in resp.text, "队满以 SSE error 事件收尾"

    def test_stream_releases_seat_after_done(self, agent_client, monkeypatch):
        """正常消费完流：席位必须归还（finally 还席，防席位泄漏）"""
        import routers.agent_router as ar
        gate = ConcurrencyGate(1, 1, 1)
        monkeypatch.setattr(ar, "get_generation_gate", lambda: gate)
        self._stub_run_agent(monkeypatch)

        resp = agent_client.post("/api/agent/chat/stream",
                                 json={"session_id": "s1", "question": "雷兹技能", "kb_id": "valorant"})
        assert "done" in resp.text
        assert gate.stats() == {"inflight": 0, "waiting": 0}


# ======================= B. 决策 LLM 异常 → 降级 Workflow =======================

class _BoomLLM:
    """决策模型假实例：invoke 必炸（模拟 429 限流/断网/超时）"""

    def bind_tools(self, spec):
        return self

    def invoke(self, messages):
        raise RuntimeError("429 rate limit")


class TestDecisionLlmDegrade:
    def test_decide_llm_failure_degrades_to_workflow(self, monkeypatch):
        """决策模型故障兑现卡2降级承诺：回落 Workflow（其自带 v3.18 限流兜底）"""
        monkeypatch.setattr(loop_mod, "make_llm", lambda thinking, timeout: _BoomLLM())
        monkeypatch.setattr(cs, "chat_single_turn", lambda sid, q, kb: ("兜底答案", [], []))

        out = run_agent("雷兹技能", kb_id="valorant")
        assert out["degraded"] is True
        assert "决策模型调用失败" in out["answer"]
        assert "兜底答案" in out["answer"]

    def test_degrade_failure_still_returns_readable_text(self, monkeypatch):
        """连 Workflow 也挂：返回可读文案而不是异常上抛（/chat 不 500、/stream 不裸断）"""
        monkeypatch.setattr(loop_mod, "make_llm", lambda thinking, timeout: _BoomLLM())

        def boom(sid, q, kb):
            raise RuntimeError("workflow 也炸")
        monkeypatch.setattr(cs, "chat_single_turn", boom)

        out = run_agent("雷兹技能", kb_id="valorant")
        assert out["degraded"] is True
        assert "决策模型调用失败" in out["answer"] and "workflow 也炸" in out["answer"]


# ======================= C. search 工具 kb_id 白名单 =======================

class TestSearchKbWhitelist:
    def test_malformed_kb_rejected_without_touching_pipeline(self, monkeypatch):
        """编造/路径类 kb_id：可读错误，检索管线一次都不许跑（防 get_or_create 空 collection）"""

        def boom(*a, **k):
            raise AssertionError("白名单拦截后不应触达检索管线")
        monkeypatch.setattr(tools_mod, "hybrid_search", boom)
        ok, text, sources = execute_tool("search_knowledge_base",
                                         {"query": "雷兹", "kb_id": "../evil"},
                                         default_kb_id="valorant")
        assert ok is False and "kb_id" in text and sources == []

    def test_unknown_kb_rejected(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda *a, **k: [])
        ok, text, _ = execute_tool("search_knowledge_base",
                                   {"query": "雷兹", "kb_id": "no_such_kb"},
                                   default_kb_id="valorant")
        assert ok is False and "不存在" in text

    def test_default_kb_passes(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        ok, text, sources = execute_tool("search_knowledge_base",
                                         {"query": "雷兹"}, default_kb_id="valorant")
        assert ok is True and "[S1]" in text


# ======================= D. Agent 轨迹 TTL 清理 =======================

class TestTraceCleanup:
    def test_expired_traces_removed_fresh_kept(self, tmp_path):
        old = tmp_path / "old.jsonl"
        old.write_text('{"type":"meta"}\n', encoding="utf-8")
        fresh = tmp_path / "fresh.jsonl"
        fresh.write_text('{"type":"meta"}\n', encoding="utf-8")
        keep = tmp_path / "notes.txt"  # 非 jsonl 不动
        keep.write_text("x", encoding="utf-8")
        past = time.time() - 40 * 86400
        os.utime(old, (past, past))

        assert cleanup_old_traces(str(tmp_path), 30) == 1
        assert not old.exists()
        assert fresh.exists() and keep.exists()

    def test_retention_zero_disables(self, tmp_path):
        f = tmp_path / "a.jsonl"
        f.write_text("x", encoding="utf-8")
        assert cleanup_old_traces(str(tmp_path), 0) == 0
        assert f.exists()

    def test_missing_dir_safe(self, tmp_path):
        assert cleanup_old_traces(str(tmp_path / "nope"), 30) == 0


# ======================= E. 同参重复调用无进展提示（LOOP-2） =======================

class _ScriptDecideLLM:
    """按脚本依次返回响应"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, spec):
        return self

    def invoke(self, messages):
        return self.script.pop(0)


class TestRepeatCallNotice:
    def test_same_args_repeat_gets_progress_notice(self, monkeypatch):
        """同参重复检索：第 2 次起 ToolMessage 前置"结果不会变化"提示；换词重查不触发"""
        decide = _ScriptDecideLLM([
            _tool_call_ai(query="雷兹", call_id="c1"),
            _tool_call_ai(query="雷兹", call_id="c2"),       # 同参重复 → 应带提示
            _tool_call_ai(query="雷兹 大招", call_id="c3"),  # 换词 → 不带提示
            AIMessage(content="", tool_calls=[]),
        ])

        class _FakeAnswerLLM:
            def invoke(self, messages):
                return AIMessage(content="最终答案 [S1]")

        monkeypatch.setattr(loop_mod, "make_llm",
                            lambda thinking, timeout: decide if not thinking else _FakeAnswerLLM())
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)

        out = run_agent("雷兹", kb_id="valorant")
        assert out["degraded"] is False
        assert "结果不会变化" not in out["steps"][0]["summary"], "首次调用不提示"
        assert "结果不会变化" in out["steps"][1]["summary"], "同参重复第 2 次起提示"
        assert "结果不会变化" not in out["steps"][2]["summary"], "换词重查不算无进展"
