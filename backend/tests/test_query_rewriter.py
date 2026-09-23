# 查询改写：指代特征识别（纯规则，不调 LLM）
from services.query_rewriter import _needs_rewrite


class TestNeedsRewrite:
    def test_short_question_needs_rewrite(self):
        assert _needs_rewrite("那枪呢") is True

    def test_anaphora_hint_triggers(self):
        assert _needs_rewrite("它的伤害是多少") is True

    def test_complete_question_skipped(self):
        assert _needs_rewrite("幻影和狂徒的伤害对比怎么样") is False
