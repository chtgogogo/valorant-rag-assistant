# 【v3.23】空来源拦截测试：answer 非空但 sources 意外为空 → 不输出答案，
# 转低置信兜底并记 error_class=empty_sources（官方直答/缓存命中/敏感词路径白名单不受影响）
import pytest

import services.chat_service as cs
from schemas.models import SearchResult


@pytest.fixture()
def pipeline_env(monkeypatch, tmp_path):
    """通用管线 mock：检索有结果但 _build_sources 被清空（模拟空来源异常）"""
    results = [SearchResult(content="资料", source="s.md", score=0.9, doc_id="d1")]
    audit_calls = []

    monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
    monkeypatch.setattr(cs, "filter_sensitive", lambda q: (False, q))
    monkeypatch.setattr(cs, "get_profile", lambda kb_id=None: {
        "custom_rules": {}, "fallback_answer": "【兜底】", "refuse_answer": "拒答",
        "system_prompt": "sys", "query_rewrite_prompt": ""})
    monkeypatch.setattr(cs, "answer_official_data_query", lambda q: None)
    monkeypatch.setattr(cs, "replace_aliases_with_official", lambda q: q)
    monkeypatch.setitem(cs.CACHE_CONFIG, "enabled", False)
    monkeypatch.setattr(cs, "_retrieve", lambda q, h, kb, profile=None, usage_ledger=None: (q, results))
    monkeypatch.setattr(cs, "_critic_refine",
                        lambda q, rw, rs, kb, usage_ledger=None: (rw, rs, []))
    monkeypatch.setattr(cs, "_should_fallback", lambda rs: False)  # 检索判定"有结果"
    monkeypatch.setattr(cs, "_build_sources", lambda rs: [])       # 但来源构建异常清空
    monkeypatch.setattr(cs, "_auto_create_ticket", lambda *a, **k: "（工单已建）")
    monkeypatch.setattr(cs, "log_qa", lambda *a, **k: audit_calls.append(k if k else a))
    return audit_calls


class TestEmptySourcesGuard:
    def test_nonstream_guards(self, pipeline_env, monkeypatch):
        """非流式：生成后不输出幻觉答案，改走兜底 + error_class=empty_sources"""
        from langchain_core.runnables import RunnableLambda
        from types import SimpleNamespace
        monkeypatch.setattr(cs, "get_llm",
                            lambda: RunnableLambda(lambda _: SimpleNamespace(content="幻觉答案")))
        answer, sources, _history = cs.chat_single_turn("web_es", "正常问题", "valorant")
        assert answer.startswith("【兜底】"), "空来源时不得输出生成答案"
        assert sources == []
        audit = pipeline_env[-1]
        if isinstance(audit, dict):
            assert audit.get("error_class") == "empty_sources"
        else:
            assert any(x == "empty_sources" for x in audit if isinstance(x, str))

    def test_stream_guards(self, pipeline_env):
        """流式：不生成（不产出 token 后再改口），直接转兜底话术"""
        events = list(cs.chat_single_turn_stream("web_es_s", "正常问题", "valorant"))
        tokens = [e["delta"] for e in events if e["type"] == "token"]
        assert tokens and "".join(tokens).startswith("【兜底】"), "空来源时不得流出生成答案"

    def test_normal_path_not_affected(self, monkeypatch, tmp_path):
        """白名单回归：来源正常时照常生成，拦截不触发"""
        results = [SearchResult(content="资料", source="s.md", score=0.9, doc_id="d1")]
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        monkeypatch.setattr(cs, "filter_sensitive", lambda q: (False, q))
        monkeypatch.setattr(cs, "get_profile", lambda kb_id=None: {
            "custom_rules": {}, "fallback_answer": "【兜底】", "refuse_answer": "拒答",
            "system_prompt": "sys", "query_rewrite_prompt": ""})
        monkeypatch.setattr(cs, "answer_official_data_query", lambda q: None)
        monkeypatch.setattr(cs, "replace_aliases_with_official", lambda q: q)
        monkeypatch.setitem(cs.CACHE_CONFIG, "enabled", False)
        monkeypatch.setattr(cs, "_retrieve", lambda q, h, kb, profile=None, usage_ledger=None: (q, results))
        monkeypatch.setattr(cs, "_critic_refine",
                            lambda q, rw, rs, kb, usage_ledger=None: (rw, rs, []))
        monkeypatch.setattr(cs, "_should_fallback", lambda rs: False)

        class FakeChain:
            def stream(self, _):
                from types import SimpleNamespace
                yield SimpleNamespace(content="正常答案")

            def invoke(self, _):
                from types import SimpleNamespace
                return SimpleNamespace(content="正常答案")

        class FakePrompt:
            def __or__(self, other):
                return FakeChain()

        monkeypatch.setattr(cs, "_build_rag_messages", lambda *a, **k: FakePrompt())

        answer, sources, _ = cs.chat_single_turn("web_ok", "正常问题", "valorant")
        assert answer == "正常答案"
        assert sources  # 正常来源不被误伤
