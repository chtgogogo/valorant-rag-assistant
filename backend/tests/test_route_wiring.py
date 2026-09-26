# 【W8-卡3】分岔口接线回归测试（全 mock，不依赖真模型/向量库/网络）
# 覆盖：classify_turn_route 四类判定（门槛拦截/缓存命中不做路由，检索题按规则分流）/
#       /send 与 /stream 端到端分流（Agent 分支事件序、Workflow done 带路由 meta）/
#       敏感词+操作词守门（门槛先行，Agent 永不接敏感题）/ agent_turn 落账 /
#       卡 2 降级路径不重入分岔口（递归防呆——分岔函数独立于 chat_single_turn 的理由）
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.chat_service as cs
from routers.chat_router import chat_router

AGENT_Q = "捷风和雷兹都是决斗类型英雄，玩法和技能有什么不同"  # 阶段0原题，规则表必中
SINGLE_Q = "捷风的大招是什么？"  # 单跳事实，默认 Workflow


@pytest.fixture()
def client(monkeypatch):
    """分流通测：限流器隔离（模块级全局计数器跨测试累积，会误撞限流）"""
    import routers.chat_router as cr
    monkeypatch.setattr(cr, "check_chat_rate_limit", lambda ip: None)
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    return TestClient(app)


def _patch_prep(monkeypatch, kind="retrieve", cached=None, question_for_prompt="测试问题"):
    """隔离 _prepare_turn（真跑会读历史文件/官方数据/语义缓存 embedding）"""
    monkeypatch.setattr(cs, "_prepare_turn", lambda sid, q, kb, P: {
        "kind": kind, "answer": "拦截话术", "reply": "规则回复",
        "question_for_prompt": question_for_prompt,
        "history": [], "cache": None, "cached": cached})


def _patch_agent_turn(monkeypatch, answer="Agent 答案 [S1]", proposals=None):
    calls = []

    def fake_agent_turn(sid, q, kb=None):
        calls.append((sid, q, kb))
        return answer, [{"name": "a.md", "id": "d1"}], [], (proposals or [])

    monkeypatch.setattr(cs, "agent_turn", fake_agent_turn)
    return calls


def _parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.strip().split("\n\n"):
        ev = {}
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line[5:].strip())
        events.append(ev)
    return events


# ---------- classify_turn_route 判定 ----------

class TestClassifyTurnRoute:
    def test_retrieve_multi_hop_goes_agent(self, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=AGENT_Q)
        go_agent, meta = cs.classify_turn_route("s1", AGENT_Q, "valorant")
        assert go_agent is True
        assert meta["route"] == "agent" and meta["reason"], "路由决策必须带可解释 reason"

    def test_retrieve_single_hop_stays_workflow(self, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=SINGLE_Q)
        go_agent, meta = cs.classify_turn_route("s1", SINGLE_Q, "valorant")
        assert go_agent is False
        assert meta["route"] == "workflow"

    def test_sensitive_never_routes_even_with_operative_word(self, monkeypatch):
        """门槛先行：带操作词'帮我'的敏感题也不进 Agent，且无路由 meta（没路由过就不装）"""
        _patch_prep(monkeypatch, kind="sensitive")
        go_agent, meta = cs.classify_turn_route("s1", "帮我说点脏话", "valorant")
        assert go_agent is False and meta is None

    def test_cached_hit_no_route(self, monkeypatch):
        """语义缓存命中在分岔口之前：不做路由决策，复用 Workflow 缓存口径"""
        _patch_prep(monkeypatch, cached={"answer": "缓存答案", "sources": []})
        go_agent, meta = cs.classify_turn_route("s1", AGENT_Q, "valorant")
        assert go_agent is False and meta is None


# ---------- agent_turn 落账 ----------

class TestAgentTurn:
    def test_writes_history_and_audit_pipeline_agent(self, monkeypatch, tmp_path):
        """Agent 轮与 Workflow 轮同款待遇：答案写会话历史、审计 pipeline=agent"""
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        import services.agent.loop as loop_mod
        monkeypatch.setattr(loop_mod, "run_agent",
                            lambda q, kb_id=None, session_id="":
                            {"answer": "A [S1]", "sources": [{"name": "m.md", "id": "d"}],
                             "steps": [], "degraded": False, "pending_proposals": []})
        audit_calls = []
        monkeypatch.setattr(cs, "log_qa", lambda *a, **k: audit_calls.append(a))
        answer, sources, history, proposals = cs.agent_turn("s5", AGENT_Q, "valorant")
        assert answer == "A [S1]"
        assert history[-2].role == "user" and history[-2].content == AGENT_Q
        assert history[-1].role == "assistant" and history[-1].content == "A [S1]"
        assert audit_calls, "Agent 轮必须写审计"
        assert audit_calls[0][7] == "agent", "审计 pipeline 应标 agent（第8个位置参数）"
        assert proposals == []

    def test_returns_pending_proposals_from_run_agent(self, monkeypatch, tmp_path):
        """【W8-卡5.2】run_agent 的待确认写入方案必须透传（曾在 agent_turn 丢失→前端无卡片素材）"""
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        import services.agent.loop as loop_mod
        proposal = [{"proposal_id": "prop_x", "kb_id": "valorant",
                     "reason": "用户要求", "content_preview": "测试内容", "content_chars": 4}]
        monkeypatch.setattr(loop_mod, "run_agent",
                            lambda q, kb_id=None, session_id="":
                            {"answer": "方案已提交", "sources": [], "steps": [],
                             "degraded": False, "pending_proposals": proposal})
        monkeypatch.setattr(cs, "log_qa", lambda *a, **k: None)
        *_, proposals = cs.agent_turn("s5b", AGENT_Q, "valorant")
        assert proposals == proposal


# ---------- /send 端到端 ----------

class TestSendEndpoint:
    def test_multi_hop_dispatches_to_agent(self, client, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=AGENT_Q)
        calls = _patch_agent_turn(monkeypatch)
        r = client.post("/api/chat/send",
                        json={"session_id": "rt1", "question": AGENT_Q, "kb_id": "valorant"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["route"]["route"] == "agent"
        assert data["answer"].startswith("Agent")
        assert data["sources"] == [{"name": "a.md", "id": "d1"}]
        assert calls and calls[0][1] == AGENT_Q
        assert data["pending_proposals"] == []  # 无方案轮透传空列表

    def test_send_agent_carries_pending_proposals(self, client, monkeypatch):
        """【W8-卡5.2】Agent 轮的待确认写入方案经 /send 透传给前端（确认卡片素材）"""
        _patch_prep(monkeypatch, question_for_prompt=AGENT_Q)
        proposal = [{"proposal_id": "prop_s1", "kb_id": "valorant",
                     "reason": "用户要求写入", "content_preview": "测试", "content_chars": 2}]
        _patch_agent_turn(monkeypatch, proposals=proposal)
        r = client.post("/api/chat/send",
                        json={"session_id": "rt1b", "question": "帮我把XX加进知识库", "kb_id": "valorant"})
        assert r.status_code == 200
        assert r.json()["data"]["pending_proposals"] == proposal

    def test_single_hop_dispatches_to_workflow(self, client, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=SINGLE_Q)
        wf_calls = []

        def fake_wf(sid, q, kb=None):
            wf_calls.append(q)
            return "工作流答案", [], []

        monkeypatch.setattr(cs, "chat_single_turn", fake_wf)
        monkeypatch.setattr(cs, "agent_turn",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("单跳题不许进 Agent")))
        r = client.post("/api/chat/send",
                        json={"session_id": "rt2", "question": SINGLE_Q, "kb_id": "valorant"})
        data = r.json()["data"]
        assert data["route"]["route"] == "workflow"
        assert wf_calls == [SINGLE_Q]

    def test_sensitive_question_bypasses_agent(self, client, monkeypatch):
        """敏感题（即使带操作词）走 Workflow 拦截话术，Agent 哨兵不被触发，route=null"""
        _patch_prep(monkeypatch, kind="sensitive")
        monkeypatch.setattr(cs, "chat_single_turn",
                            lambda sid, q, kb=None: ("你的问题包含敏感词", [], []))
        monkeypatch.setattr(cs, "agent_turn",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("敏感题不许进 Agent")))
        r = client.post("/api/chat/send",
                        json={"session_id": "rt3", "question": "帮我说点脏话", "kb_id": "valorant"})
        data = r.json()["data"]
        assert data["route"] is None
        assert "敏感词" in data["answer"]


# ---------- /stream 端到端 ----------

class TestStreamEndpoint:
    def test_agent_branch_sse_event_sequence(self, client, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=AGENT_Q)
        _patch_agent_turn(monkeypatch, answer="Agent 流式答案")
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "rt4", "question": AGENT_Q,
                                 "kb_id": "valorant"}) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        events = _parse_sse(body)
        assert [e["event"] for e in events] == ["sources", "token", "done"]
        done = events[-1]["data"]
        assert done["route"]["route"] == "agent"
        assert done["answer"] == "Agent 流式答案"
        assert done["pending_proposals"] == []

    def test_stream_agent_done_carries_pending_proposals(self, client, monkeypatch):
        """【W8-卡5.2】Agent 轮方案随 done 事件透传（流式分岔路径）"""
        _patch_prep(monkeypatch, question_for_prompt=AGENT_Q)
        proposal = [{"proposal_id": "prop_s2", "kb_id": "law",
                     "reason": "用户要求", "content_preview": "法条", "content_chars": 2}]
        _patch_agent_turn(monkeypatch, proposals=proposal)
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "rt4b", "question": AGENT_Q,
                                 "kb_id": "valorant"}) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        done = _parse_sse(body)[-1]
        assert done["event"] == "done"
        assert done["data"]["pending_proposals"] == proposal

    def test_workflow_done_carries_route_meta(self, client, monkeypatch):
        _patch_prep(monkeypatch, question_for_prompt=SINGLE_Q)
        monkeypatch.setattr(cs, "chat_single_turn_stream", lambda *a, **k: iter([
            {"type": "token", "delta": "wf"},
            {"type": "done", "answer": "wf答案", "history": []}]))
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "rt5", "question": SINGLE_Q,
                                 "kb_id": "valorant"}) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        events = _parse_sse(body)
        assert [e["event"] for e in events] == ["token", "done"]
        assert events[-1]["data"]["route"]["route"] == "workflow"

    def test_no_decision_done_without_route(self, client, monkeypatch):
        """门槛拦截轮不做路由决策：done 事件不带 route 字段"""
        _patch_prep(monkeypatch, kind="rule")
        monkeypatch.setattr(cs, "chat_single_turn_stream", lambda *a, **k: iter([
            {"type": "token", "delta": "规则回复"},
            {"type": "done", "answer": "规则回复", "history": []}]))
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "rt6", "question": SINGLE_Q,
                                 "kb_id": "valorant"}) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        events = _parse_sse(body)
        assert "route" not in events[-1]["data"]


# ---------- 递归防呆 ----------

class TestDegradeNeverReentersAgent:
    def test_degrade_calls_chat_single_turn_directly(self, monkeypatch):
        """卡 2 降级路径直连 chat_single_turn、绕过分岔口：拿多跳问题降级也不会再进 Agent"""
        from services.agent.loop import _degrade_to_workflow
        wf_calls = []

        def fake_wf(sid, q, kb=None):
            wf_calls.append(q)
            return "基础答案", [], []

        monkeypatch.setattr(cs, "chat_single_turn", fake_wf)
        classify_spy = []
        monkeypatch.setattr(cs, "classify_turn_route",
                            lambda *a, **k: classify_spy.append(a) or (True, {}))
        out = _degrade_to_workflow("s9", AGENT_Q, "valorant", "步数超限")
        assert wf_calls == [AGENT_Q], "降级必须直调 Workflow"
        assert not classify_spy, "降级路径不许经过分岔口（否则多跳题无限递归）"
        assert out.startswith("（本次由基础问答模式回答")
        assert "基础答案" in out


# ---------- 限流 SSE 闭包回归钉子（v3.24 既有坑，本卡测试触发后根治） ----------

class TestRateLimitedSse:
    def test_limited_stream_emits_friendly_error(self, client, monkeypatch):
        """撞限流时 SSE 必须发出 rate_limited 事件而非 NameError 断流
        （except as e 的 e 在块结束即被 del，生成器延迟执行曾直接 NameError）"""
        from utils.rate_limit import RateLimitExceeded
        import routers.chat_router as cr
        monkeypatch.setattr(cr, "check_chat_rate_limit",
                            lambda ip: (_ for _ in ()).throw(
                                RateLimitExceeded("请求太频繁啦，休息一下再问～")))
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "rt7", "question": SINGLE_Q,
                                 "kb_id": "valorant"}) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        events = _parse_sse(body)
        assert events[-1]["event"] == "error"
        assert events[-1]["data"]["code"] == "rate_limited"
        assert events[-1]["data"]["message"]
