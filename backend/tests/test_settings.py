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

    def test_unknown_domain_raises_404(self):
        # 【v3.20】未知 kb_id 不再静默回退默认领域（静默回退会掩盖客户端传错参数），改抛 404
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            st.get_profile("no-such-kb")
        assert ei.value.status_code == 404

    def test_none_falls_back_to_default(self):
        # None（调用方不传 kb_id 的内部旧路径）仍回退默认领域
        assert st.get_profile(None) is st.DOMAIN_PROFILE
