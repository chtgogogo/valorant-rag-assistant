# 配置层：密钥延迟校验、领域配置运行时取用
import pytest

import config.settings as st


class TestRequireApiKey:
    def test_returns_key_when_present(self):
        assert st.require_api_key() == st.LLM_CONFIG["api_key"]

    def test_raises_with_guidance_when_missing(self, monkeypatch):
        monkeypatch.setitem(st.LLM_CONFIG, "api_key", None)
        with pytest.raises(ValueError, match="ZHIPU_API_KEY"):
            st.require_api_key()


class TestGetProfile:
    def test_known_domain_resolved(self):
        profile = st.get_profile("ecommerce")
        assert profile.get("app_name")

    def test_unknown_domain_falls_back_to_default(self):
        assert st.get_profile("no-such-kb") is st.DOMAIN_PROFILE
