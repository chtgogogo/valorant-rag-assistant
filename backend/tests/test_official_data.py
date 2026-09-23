# 官方结构化数据直答与别名处理（读仓库内 knowledge_base/*.json，无网络依赖）
from services.official_data_service import (
    answer_official_data_query,
    expand_query_aliases,
    replace_aliases_with_official,
)


class TestOfficialDirectAnswer:
    def test_hero_list_question(self):
        ans = answer_official_data_query("无畏契约里有哪些英雄？")
        assert ans and "英雄" in ans

    def test_weapon_price_question(self):
        ans = answer_official_data_query("幻影多少钱？")
        assert ans and "Phantom" in ans and "信用点" in ans

    def test_unrelated_question_returns_none(self):
        assert answer_official_data_query("今天天气怎么样？") is None


class TestAliases:
    def test_expand_appends_official_names(self):
        out = expand_query_aliases("ak怎么样")
        assert "狂徒" in out and "Vandal" in out

    def test_expand_without_alias_unchanged(self):
        raw = "完全没有别名词的句子"
        assert expand_query_aliases(raw) == raw

    def test_replace_uses_official_form(self):
        assert replace_aliases_with_official("捷风的E技能") == "捷风 / Jett的E技能"

    def test_replace_unknown_alias_unchanged(self):
        raw = "暴徒和警长哪个好"  # "暴徒/警长"不在别名表，原样保留
        assert replace_aliases_with_official(raw) == raw
