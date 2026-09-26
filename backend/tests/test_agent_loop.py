# 【W8-卡1】Agent 工具注册表与主循环：纯 mock 测试（不依赖真实模型/向量库）
# 覆盖：工具分发（未知工具/空参数/内部异常）、循环三种结局（终答/步数超限/工具失败不炸）、
#       消息回填正确性（ToolMessage 带对 tool_call_id）、来源去重汇总
import pytest
from langchain_core.messages import AIMessage
from schemas.models import SearchResult

from config.settings import RAG_CONFIG

import services.agent.loop as loop_mod
import services.agent.tools as tools_mod
from services.agent.loop import run_agent
from services.agent.tools import execute_tool


def _r(content="捷风的技能有瞬云", source="英雄介绍.md", doc_id="d1", score=0.9) -> SearchResult:
    return SearchResult(content=content, source=source, score=score, doc_id=doc_id)


# ---------- tools 层 ----------

class TestExecuteTool:
    def test_unknown_tool_returns_error_string(self):
        ok, text, sources = execute_tool("no_such_tool", {}, default_kb_id="valorant")
        assert ok is False
        assert "不存在" in text
        assert sources == []

    def test_empty_query_rejected(self):
        ok, text, _ = execute_tool("search_knowledge_base", {"query": "  "}, default_kb_id="k")
        assert ok is False and "query" in text

    def test_internal_error_contained(self, monkeypatch):
        # 检索管线炸了也不许抛出循环：转错误字符串（ERR-3 结构化状态）
        def boom(*a, **k):
            raise RuntimeError("chroma 连接失败")
        monkeypatch.setattr(tools_mod, "hybrid_search", boom)
        ok, text, sources = execute_tool("search_knowledge_base",
                                         {"query": "雷兹"}, default_kb_id="valorant")
        assert ok is False and "工具执行失败" in text and "chroma" in text

    def test_search_formats_sources(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        ok, text, sources = execute_tool("search_knowledge_base",
                                         {"query": "捷风 技能"}, default_kb_id="valorant")
        assert ok is True
        assert "[S1]" in text and "英雄介绍.md" in text
        assert sources == [{"name": "英雄介绍.md", "id": "d1"}]


# ---------- loop 层 ----------

class FakeDecideLLM:
    """按脚本依次返回响应；记录每次收到的消息序列供断言回填正确性"""
    def __init__(self, script: list[AIMessage]):
        self.script = list(script)
        self.invocations: list[list] = []

    def bind_tools(self, spec):
        return self

    def invoke(self, messages):
        self.invocations.append(list(messages))
        return self.script.pop(0)


class FakeAnswerLLM:
    def __init__(self, content="最终答案：晚安焰火是火箭筒 [S1]"):
        self.content = content
        self.last_prompt = None

    def invoke(self, messages):
        self.last_prompt = messages[-1].content
        return AIMessage(content=self.content)


@pytest.fixture
def patch_llms(monkeypatch):
    """make_llm 按 thinking 参数返回不同假实例：False=决策脚本，True=终答"""
    holder = {}

    def _install(script: list[AIMessage], answer: str = "最终答案 [S1]"):
        decide = FakeDecideLLM(script)
        answer_llm = FakeAnswerLLM(answer)
        holder["decide"], holder["answer"] = decide, answer_llm
        monkeypatch.setattr(loop_mod, "make_llm",
                            lambda thinking, timeout: decide if not thinking else answer_llm)
        return holder

    return _install


def _tool_call_ai(query="雷兹 技能", call_id="call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "search_knowledge_base", "args": {"query": query}, "id": call_id}])


class TestRunAgent:
    def test_tool_then_answer(self, patch_llms, monkeypatch):
        patch_llms([_tool_call_ai(), AIMessage(content="", tool_calls=[])])
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)

        out = run_agent("捷风和雷兹的区别", kb_id="valorant")

        assert out["degraded"] is False
        assert "最终答案" in out["answer"]  # FakeAnswerLLM 的固定文案，证明走了开思考终答
        assert len(out["steps"]) == 2  # 一次 tool_call + 一次 final
        assert out["steps"][0]["type"] == "tool_call" and out["steps"][0]["ok"] is True
        assert out["steps"][1]["type"] == "final"
        assert out["sources"] == [{"name": "英雄介绍.md", "id": "d1"}]

    def test_tool_result_backfilled(self, patch_llms, monkeypatch):
        """工具结果必须以 ToolMessage 回填且 tool_call_id 对上——回错 id 模型会拒收"""
        holder = patch_llms([_tool_call_ai(query="雷兹", call_id="call_9"),
                             AIMessage(content="", tool_calls=[])])
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        run_agent("雷兹", kb_id="valorant")
        msgs = holder["decide"].invocations[-1]
        tool_msgs = [m for m in msgs if type(m).__name__ == "ToolMessage"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "call_9"
        assert "[S1]" in tool_msgs[0].content  # 回填的是格式化检索结果（mock 固定内容）

    def test_no_tools_direct_answer_falls_to_no_material_notice(self, patch_llms):
        """模型一步都不调工具且终答 LLM 也拿不到资料：应如实说没资料，不编造"""
        patch_llms([AIMessage(content="", tool_calls=[])], answer="")
        out = run_agent("你好", kb_id="valorant")
        assert out["degraded"] is False
        assert "未检索到相关资料" in out["answer"]

    def test_max_steps_exhausted_degrades_to_workflow(self, patch_llms, monkeypatch, tmp_path):
        """步数超限 → 降级回 Workflow（mock）+ 说明前缀 + degraded=True（卡 2）"""
        monkeypatch.setitem(RAG_CONFIG, "agent_trace_dir", str(tmp_path))
        patch_llms([_tool_call_ai(query=f"q{i}", call_id=f"c{i}") for i in range(10)])
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        monkeypatch.setattr(loop_mod, "_degrade_to_workflow",
                            lambda sid, q, kb, reason: f"（降级{reason}）基础答案")
        out = run_agent("无限检索", kb_id="valorant")
        assert out["degraded"] is True
        assert "基础答案" in out["answer"] and "降级" in out["answer"]
        assert out["steps"][-1]["type"] == "degraded"

    def test_tool_failure_keeps_loop_alive(self, patch_llms, monkeypatch, tmp_path):
        """工具失败（ok=False）不炸循环：错误串回填，模型看到后停止调用并终答"""
        monkeypatch.setitem(RAG_CONFIG, "agent_trace_dir", str(tmp_path))
        patch_llms([_tool_call_ai(), AIMessage(content="", tool_calls=[])])
        monkeypatch.setattr(loop_mod, "execute_tool",
                            lambda *a, **k: (False, "（工具执行失败：mock）", []))
        out = run_agent("触发失败", kb_id="valorant")
        assert out["degraded"] is False
        assert out["steps"][0]["ok"] is False
        assert out["sources"] == []

    def test_circuit_breaker_disables_after_consecutive_fails(self, patch_llms, monkeypatch, tmp_path):
        """卡2熔断：同一工具连续失败 2 次 → 停用，第三次调用不再执行真工具"""
        monkeypatch.setitem(RAG_CONFIG, "agent_trace_dir", str(tmp_path))
        patch_llms([_tool_call_ai(query="a", call_id="c1"),
                    _tool_call_ai(query="b", call_id="c2"),
                    _tool_call_ai(query="c", call_id="c3"),
                    AIMessage(content="", tool_calls=[])])
        calls = []
        monkeypatch.setattr(loop_mod, "execute_tool",
                            lambda name, args, default_kb_id="": calls.append(name)
                            or (False, "（工具执行失败：mock）", []))
        out = run_agent("连续失败", kb_id="valorant")
        assert len(calls) == 2  # 第三次调用被熔断拦截，不再执行真工具
        # 第三次的 ToolMessage 明确告知模型工具已停用
        third = out["steps"][2]["summary"]
        assert "停用" in third

    def test_all_tools_disabled_degrades(self, patch_llms, monkeypatch, tmp_path):
        """卡2死局：模型仍要工具但全部被停用 → 直接降级"""
        monkeypatch.setitem(RAG_CONFIG, "agent_trace_dir", str(tmp_path))
        patch_llms([_tool_call_ai(query="a", call_id="c1"),
                    _tool_call_ai(query="b", call_id="c2"),
                    _tool_call_ai(query="c", call_id="c3")])
        monkeypatch.setattr(loop_mod, "execute_tool",
                            lambda *a, **k: (False, "（工具执行失败：mock）", []))
        monkeypatch.setattr(loop_mod, "_degrade_to_workflow",
                            lambda sid, q, kb, reason: f"（降级：{reason}）")
        out = run_agent("全熔断", kb_id="valorant")
        assert out["degraded"] is True
        assert "熔断" in out["answer"]

    def test_trace_jsonl_written(self, patch_llms, monkeypatch, tmp_path):
        """卡2轨迹：一次执行一个 JSONL 文件，meta/tool/final 行齐全可回放"""
        monkeypatch.setitem(RAG_CONFIG, "agent_trace_dir", str(tmp_path))
        patch_llms([_tool_call_ai(), AIMessage(content="", tool_calls=[])])
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        run_agent("轨迹验证", kb_id="valorant")
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        lines = [__import__("json").loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]
        types = [l["type"] for l in lines]
        assert types[0] == "meta" and "tool_call" in types and "final" in types
        assert all("ts" in l for l in lines)

    def test_sources_deduped_across_steps(self, patch_llms, monkeypatch):
        """两次检索命中同一文档时，来源只留一份"""
        holder = patch_llms([_tool_call_ai(query="a", call_id="c1"),
                             _tool_call_ai(query="b", call_id="c2"),
                             AIMessage(content="", tool_calls=[])])
        monkeypatch.setattr(tools_mod, "hybrid_search", lambda q, kb_id=None: [_r()])
        monkeypatch.setattr(tools_mod, "rerank", lambda q, c, top_k=None: c)
        out = run_agent("多轮检索", kb_id="valorant")
        assert len(out["sources"]) == 1
