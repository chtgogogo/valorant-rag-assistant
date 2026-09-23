# 对话服务纯函数：兜底判定、去重、错误分类、懒加载实例、提示词组装
import pytest
from schemas.models import ChatMessage, SearchResult

import services.chat_service as cs
from config.settings import RAG_CONFIG


def _r(score: float, dense: float = None, degraded: bool = False,
       content: str = "c", doc_id: str = "d1") -> SearchResult:
    return SearchResult(content=content, source="s.md", score=score, doc_id=doc_id,
                        dense_score=dense, rerank_degraded=degraded)


class TestShouldFallback:
    def test_empty_results_falls_back(self):
        assert cs._should_fallback([]) is True

    def test_low_rerank_score_falls_back(self, monkeypatch):
        monkeypatch.setitem(RAG_CONFIG, "enable_rerank", True)
        assert cs._should_fallback([_r(0.4)]) is True

    def test_high_rerank_score_passes(self, monkeypatch):
        monkeypatch.setitem(RAG_CONFIG, "enable_rerank", True)
        assert cs._should_fallback([_r(0.7)]) is False

    def test_rerank_off_uses_cosine(self, monkeypatch):
        monkeypatch.setitem(RAG_CONFIG, "enable_rerank", False)
        assert cs._should_fallback([_r(0.9, dense=0.2)]) is True   # score 高但余弦低
        assert cs._should_fallback([_r(0.1, dense=0.8)]) is False

    def test_degraded_rerank_uses_cosine(self, monkeypatch):
        # bug②：重排失败降级后 score 已是召回量纲（如 BM25 的 8.5），
        # 必须退回余弦阈值判定——0.2 < 0.35 应兜底（修复前按 8.5 比会漏兜底）
        monkeypatch.setitem(RAG_CONFIG, "enable_rerank", True)
        assert cs._should_fallback([_r(8.5, dense=0.2, degraded=True)]) is True


class TestDedupeResults:
    def test_same_doc_same_content_deduped(self):
        out = cs._dedupe_results([_r(0.9, content="x"), _r(0.8, content="x")])
        assert len(out) == 1
        assert out[0].score == 0.9  # 保留先出现的

    def test_same_content_diff_doc_kept(self):
        out = cs._dedupe_results([_r(0.9, content="x", doc_id="a"),
                                  _r(0.8, content="x", doc_id="b")])
        assert len(out) == 2


class TestLlmErrorEvent:
    @pytest.mark.parametrize("raw,code", [
        ("Error code: 429", "rate_limited"),
        ("账户速率限制 1302", "rate_limited"),
        ("Request timed out after 30s", "timeout"),
        ("Error code: 500 Internal", "server_error"),
        ("Connection error.", "unavailable"),
    ])
    def test_classification(self, raw, code):
        event = cs._llm_error_event(Exception(raw))
        assert event["type"] == "error"
        assert event["code"] == code
        assert event["message"]


class TestLazyLlm:
    def test_missing_key_raises_on_first_use(self, monkeypatch):
        # v3.15 懒加载契约：import 不炸，首次取实例时才校验密钥
        monkeypatch.setitem(cs.LLM_CONFIG, "api_key", None)  # 共享 dict，require_api_key 同步可见
        cs._llm_instances.pop("llm", None)
        with pytest.raises(ValueError):
            cs.get_llm()


class TestBuildRagMessages:
    def test_braces_in_context_do_not_break_template(self):
        # bug①：知识库内容含 JSON 示例/花括号时不得被 langchain 当占位符解析
        results = [_r(0.9, content='配置示例：{"max_tokens": 1024} 以及 {bad_var}')]
        prompt = cs._build_rag_messages([], results, "这个配置是什么")
        msgs = prompt.format_messages(question="这个配置是什么")
        assert "{bad_var}" in msgs[0].content

    def test_history_with_braces_survives(self):
        prompt = cs._build_rag_messages(
            [ChatMessage(role="user", content="看这个 {token}")],
            [_r(0.9, content="普通内容")], "继续")
        msgs = prompt.format_messages(question="继续")
        assert any("{token}" in m.content for m in msgs)
