# 混合检索纯函数：jieba 分词、RRF 融合排序
from schemas.models import SearchResult
from services.hybrid_retriever import _rrf_fuse, _tokenize


def _r(content: str, doc_id: str = "d1", dense: float = None) -> SearchResult:
    return SearchResult(content=content, source="s.md", score=1.0,
                        doc_id=doc_id, dense_score=dense)


class TestTokenize:
    def test_chinese_words_segmented(self):
        tokens = _tokenize("幻影的伤害")
        assert "幻影" in tokens or "的" in tokens  # jieba 可把"幻影"整体切出
        assert all(t.strip() for t in tokens)

    def test_blank_filtered(self):
        tokens = _tokenize("a b")
        assert "a" in tokens and "b" in tokens
        assert all(t.strip() for t in tokens)

    def test_punctuation_kept_as_token(self):
        # 现状快照：jieba 把中文标点切成独立 token 且 _tokenize 不过滤——标点双侧同现、
        # IDF 极低，对 BM25 无实害；若未来改为过滤，需重跑检索评测确认指标不动
        assert "，" in _tokenize("a，b")


class TestRrfFuse:
    def test_merge_and_rank(self):
        # "A" 两路都出现（rank1 + rank2）应排在只出现一路的 B/C 前
        dense = [_r("A"), _r("B")]
        sparse = [_r("C"), _r("A")]
        fused = _rrf_fuse(dense, sparse)
        assert len(fused) == 3
        assert fused[0].content == "A"

    def test_single_list_keeps_order(self):
        dense = [_r("A"), _r("B"), _r("C")]
        fused = _rrf_fuse(dense, [])
        assert [f.content for f in fused] == ["A", "B", "C"]

    def test_common_block_keeps_higher_dense_score(self):
        a_dense = _r("A", dense=0.9)
        a_sparse = _r("A", dense=0.5)
        fused = _rrf_fuse([a_dense], [a_sparse])
        assert len(fused) == 1
        assert fused[0].dense_score == 0.9
