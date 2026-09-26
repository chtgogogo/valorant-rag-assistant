# 【W8-卡5】SSE 流式轨迹测试：run_agent 的 on_event 回调（不传时行为不变）/
# 事件协议序列（tool_call → tool_result → final_answer）/ 降级事件带原因 /
# /api/agent/chat/stream 端到端（SSE 帧格式 + done 收尾 + 异常转 error 事件不悬挂）
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import services.agent.loop as loop_mod
import services.agent.tools as tools_mod
from routers.agent_router import agent_router
from services.agent.loop import run_agent

from tests.test_agent_loop import FakeAnswerLLM, FakeDecideLLM, _r, _tool_call_ai


@pytest.fixture
def patch_env(monkeypatch, tmp_path):
    """LLM/检索全 mock + 轨迹目录隔离"""
    monkeypatch.setitem(loop_mod.RAG_CONFIG, "agent_trace_dir", str(tmp_path / "traces"))

    def _install(script, answer="最终答案 [S1]"):
        decide = FakeDecideLLM(script)
        answer_llm = FakeAnswerLLM(answer)
        monkeypatch.setattr(loop_mod, "make_llm",
                            lambda thinking, timeout: decide if not thinking else answer_llm)
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        return decide

    return _install


class TestOnEventCallback:
    def test_event_sequence_tool_call_result_final(self, patch_env):
        """一次检索 + 终答：事件序列 = tool_call → tool_result → final_answer（D2 协议）"""
        patch_env([_tool_call_ai(query="雷兹 技能"), AIMessage(content="", tool_calls=[])])
        events = []
        out = run_agent("捷风和雷兹的区别", kb_id="valorant", on_event=events.append)
        types = [e["type"] for e in events]
        assert types == ["tool_call", "tool_result", "final_answer"]
        assert events[0]["tool"] == "search_knowledge_base"
        assert events[0]["args"]["query"] == "雷兹 技能"
        assert events[1]["ok"] is True and events[1]["summary"]
        assert events[2]["answer"] == out["answer"]  # 终答事件带全文
        assert events[2]["sources"] == out["sources"]

    def test_no_callback_unchanged(self, patch_env):
        """不传 on_event：返回结构与旧行为一致（卡 1 兼容零破坏）"""
        patch_env([AIMessage(content="", tool_calls=[])])
        out = run_agent("你好", kb_id="valorant")
        assert out["degraded"] is False and "answer" in out

    def test_degraded_event_carries_reason(self, patch_env, monkeypatch):
        """护栏降级：degraded 事件带 reason 与降级答案（卡 5 验收 3 数据源）"""
        patch_env([_tool_call_ai()] * 5)  # 一直要工具，1 步上限必超
        monkeypatch.setattr(loop_mod, "_degrade_to_workflow",
                            lambda s, q, k, reason: "（降级说明）" + reason)
        events = []
        out = run_agent("捷风和雷兹的区别", kb_id="valorant", max_steps=1, on_event=events.append)
        assert out["degraded"] is True
        assert events[-1]["type"] == "degraded"
        assert "1 步内未得出答案" in events[-1]["reason"]
        assert events[-1]["answer"] == out["answer"]


# ---------- SSE 端点 ----------

@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(agent_router, prefix="/api/agent")
    return TestClient(app)


def _parse_sse(body: str):
    """SSE 文本 → [(event_type, payload_dict)]"""
    frames = []
    for chunk in body.split("\n\n"):
        ev = data = None
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if ev and data is not None:
            frames.append((ev, data))
    return frames


class TestStreamEndpoint:
    def test_sse_frames_and_done(self, monkeypatch, client, tmp_path):
        """端到端：回调事件逐帧推送、done 收尾带完整答案（轨迹逐行出现的数据基础）"""
        monkeypatch.setitem(loop_mod.RAG_CONFIG, "agent_trace_dir", str(tmp_path / "traces"))

        def fake_run(question, kb_id, session_id="agent-anon", on_event=None, **kw):
            if on_event:
                on_event({"type": "tool_call", "step": 1, "tool": "search_knowledge_base",
                          "args": {"query": "捷风"}})
                on_event({"type": "tool_result", "step": 1, "tool": "search_knowledge_base",
                          "ok": True, "summary": "捷风是决斗者…"})
                on_event({"type": "final_answer", "answer": "答案全文", "sources": []})
            return {"answer": "答案全文", "sources": [], "steps": [], "degraded": False}

        monkeypatch.setattr(loop_mod, "run_agent", fake_run)
        with client.stream("POST", "/api/agent/chat/stream",
                           json={"question": "捷风和雷兹的区别", "kb_id": "valorant",
                                 "session_id": "s1"}) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            frames = _parse_sse("".join(resp.iter_text()))
        types = [t for t, _ in frames]
        assert types == ["tool_call", "tool_result", "final_answer", "done"]
        assert frames[-1][1]["answer"] == "答案全文"

    def test_agent_exception_becomes_error_event(self, monkeypatch, client, tmp_path):
        """Agent 本体炸了：SSE 推结构化 error 事件并正常收流，不留悬挂连接（验收 2 的后端底座）"""
        monkeypatch.setitem(loop_mod.RAG_CONFIG, "agent_trace_dir", str(tmp_path / "traces"))
        monkeypatch.setattr(loop_mod, "run_agent",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模型超时")))
        with client.stream("POST", "/api/agent/chat/stream",
                           json={"question": "q", "kb_id": "valorant",
                                 "session_id": "s1"}) as resp:
            frames = _parse_sse("".join(resp.iter_text()))
        assert [t for t, _ in frames] == ["error"]
        assert "模型超时" in frames[0][1]["message"]
