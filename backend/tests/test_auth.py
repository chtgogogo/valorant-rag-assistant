# API Key 认证依赖：开关与密钥校验矩阵
import pytest
from fastapi import HTTPException

from utils import auth


class TestVerifyApiKey:
    def test_disabled_always_passes(self, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_ENABLED", False)
        assert auth.verify_api_key(x_api_key="anything") is None

    def test_enabled_missing_key_401(self, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "API_KEYS", ["real-key"])
        with pytest.raises(HTTPException) as ei:
            auth.verify_api_key(x_api_key="")
        assert ei.value.status_code == 401

    def test_enabled_wrong_key_401(self, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "API_KEYS", ["real-key"])
        with pytest.raises(HTTPException) as ei:
            auth.verify_api_key(x_api_key="wrong")
        assert ei.value.status_code == 401

    def test_enabled_correct_key_passes(self, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "API_KEYS", ["real-key"])
        assert auth.verify_api_key(x_api_key="real-key") is None
