# 敏感词过滤
from utils.sensitive import filter_sensitive


class TestFilterSensitive:
    def test_hit_replaces_with_stars(self):
        hit, out = filter_sensitive("你个傻逼")
        assert hit is True
        assert "傻逼" not in out
        assert "*" in out

    def test_clean_text_unchanged(self):
        hit, out = filter_sensitive("幻影怎么买")
        assert hit is False
        assert out == "幻影怎么买"
