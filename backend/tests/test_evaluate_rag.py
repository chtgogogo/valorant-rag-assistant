# 评测脚本纯函数：拒答判定、忠实度判分解析（v3.16 起 evaluate_rag 可被导入）
from schemas.models import SearchResult
from scripts.evaluate_rag import judge_refusal, parse_faithfulness


def _r(score: float = 0.9, content: str = "c", source: str = "s.md") -> SearchResult:
    return SearchResult(content=content, source=source, score=score, doc_id="d1")


class TestJudgeRefusal:
    def test_empty_sources_is_refusal(self):
        assert judge_refusal("随便什么回答", []) is True

    def test_refusal_marker_hit(self):
        assert judge_refusal("抱歉，暂时不会这个问题", [_r()]) is True

    def test_normal_answer_not_refusal(self):
        assert judge_refusal("幻影价格 2900 信用点", [_r()]) is False


class TestParseFaithfulness:
    def test_plain_json(self):
        out = parse_faithfulness('{"faithful": true, "unsupported": []}')
        assert out["faithful"] is True
        assert out["unsupported"] == []

    def test_json_with_surrounding_text(self):
        out = parse_faithfulness('评审结果：{"faithful": false, "unsupported": ["价格数字"]} 以上。')
        assert out["faithful"] is False
        assert out["unsupported"] == ["价格数字"]

    def test_unparseable_returns_none(self):
        assert parse_faithfulness("无法解析成 JSON")["faithful"] is None

    def test_empty_returns_none(self):
        assert parse_faithfulness("")["faithful"] is None
