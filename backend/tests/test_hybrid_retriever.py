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


# ---------------- 【W8-卡7】切分标题跟随 ----------------

class TestSplitTextHeadingAttachment:
    """切分前按标题预分段、每块前置所属标题（根治无主切片）"""

    def test_heading_attached_to_its_section_chunks(self):
        from services.document_service import split_text
        doc = (
            "## 芮娜 / Reyna\n" + "睥睨是芮娜的技能。" * 30 + "\n"
            "## 雷兹 / Raze\n" + "花车巡游是雷兹的技能。" * 30
        )
        chunks = split_text(doc, chunk_size=200, chunk_overlap=20)
        assert len(chunks) >= 2
        raze_chunks = [c for c in chunks if "雷兹" in c]
        assert raze_chunks, "雷兹段应至少切出一块"
        # 关键断言：每块雷兹内容都自带雷兹标题（不再有无主切片）
        assert all(c.startswith("## 雷兹 / Raze") for c in raze_chunks)
        # 芮娜的块不会混进雷兹正文
        assert all("花车巡游" not in c for c in chunks if c.startswith("## 芮娜"))

    def test_text_without_headings_unchanged(self):
        from services.document_service import split_text
        assert split_text("普通工单文本，没有标题。", chunk_size=100) == ["普通工单文本，没有标题。"]

    def test_leading_preamble_has_no_heading(self):
        from services.document_service import split_text
        doc = "文档导语。\n\n## 唯一标题\n正文内容。"
        chunks = split_text(doc, chunk_size=200)
        assert chunks[0].startswith("文档导语")
        assert any(c.startswith("## 唯一标题") for c in chunks)
