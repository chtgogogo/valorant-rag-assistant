# 【v3.20】安全漏洞清零回归测试：session_id 白名单 / kb_id 全链路校验 /
# 鉴权常量时间比较与生产模式强制开启 / SSE 断连兜底 / OOM 降级判空
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import services.chat_service as cs
from services.device_manager import degrade_to_cpu


class TestSessionIdWhitelist:
    """session_id 拼进历史文件路径，必须白名单校验，防 ../../ 读写清任意 json"""

    @pytest.mark.parametrize("bad", [
        "../../x",            # 经典穿越
        "..\\evil",           # Windows 反斜杠穿越
        "",                   # 空
        "a b",                # 空格
        "a/b",                # 路径分隔符
        "x" * 65,             # 超长
        None,                 # None
    ])
    def test_get_history_path_rejects_invalid(self, bad):
        with pytest.raises(HTTPException) as ei:
            cs._get_history_path(bad)
        assert ei.value.status_code == 400

    def test_normal_session_id_untouched(self, tmp_path, monkeypatch):
        # 正常 id（web_时间戳）不受影响，历史读写照常
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        cs.append_history("web_1789642281651", "你好", "你好呀")
        history = cs.load_history("web_1789642281651")
        assert [m.content for m in history] == ["你好", "你好呀"]
        cs.clear_history("web_1789642281651")

    @pytest.mark.parametrize("api", ["load_history", "clear_history", "rollback_history"])
    def test_entrypoints_blocked(self, tmp_path, monkeypatch, api):
        # 三个入口（读/清/回滚）全部被白名单拦截，绝不触达任意路径
        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        evil = "../outside"
        with pytest.raises(HTTPException):
            if api == "load_history":
                cs.load_history(evil)
            elif api == "clear_history":
                cs.clear_history(evil)
            else:
                cs.rollback_history(evil, 0)
        # 穿越目标文件绝不能被创建/删除：在 history_path 之外放一个标记文件验证完好
        outside = tmp_path.parent / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        assert outside.read_text(encoding="utf-8") == "{}"


class TestChatRequestPattern:
    """双保险：请求模型层 session_id 带 pattern 约束，非法值在 pydantic 校验层就拒绝"""

    def test_invalid_session_id_rejected(self):
        from schemas.models import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(session_id="../../x", question="q")

    def test_valid_session_id_accepted(self):
        from schemas.models import ChatRequest
        req = ChatRequest(session_id="web_1789642281651", question="q")
        assert req.session_id == "web_1789642281651"


class TestKbIdGuard:
    """kb_id 白名单 + 存在性校验：未知/非法 kb_id 明确报错，不再静默回退默认领域"""

    def test_invalid_format_400(self):
        from config.settings import resolve_kb_id
        for bad in ["../../x", "", "a/b", "x" * 65, " "]:
            with pytest.raises(HTTPException) as ei:
                resolve_kb_id(bad)
            assert ei.value.status_code == 400

    def test_unknown_kb_404(self):
        from config.settings import resolve_kb_id
        with pytest.raises(HTTPException) as ei:
            resolve_kb_id("no_such_kb")
        assert ei.value.status_code == 404

    def test_none_falls_back_to_default(self):
        from config.settings import resolve_kb_id, DEFAULT_KB_ID
        assert resolve_kb_id(None) == DEFAULT_KB_ID

    def test_configured_kb_passes(self):
        from config.settings import resolve_kb_id
        assert resolve_kb_id("valorant") == "valorant"

    def test_chat_service_unknown_kb_404_no_silent_fallback(self):
        # chat_single_turn 对未知 kb_id 直接 404（P=get_profile 在最先执行，无副作用）
        with pytest.raises(HTTPException) as ei:
            cs.chat_single_turn("web_test", "随便问点啥", "no_such_kb")
        assert ei.value.status_code == 404

    def test_get_profile_unknown_404(self):
        from config.settings import get_profile
        with pytest.raises(HTTPException) as ei:
            get_profile("no_such_kb")
        assert ei.value.status_code == 404


class TestAuthHardening:
    """鉴权加固：常量时间比较 + 生产模式强制开启（fail-closed）"""

    def test_non_ascii_key_no_crash(self, monkeypatch):
        from utils import auth
        monkeypatch.setattr(auth, "AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "API_KEYS", ["密钥-测试"])
        with pytest.raises(HTTPException) as ei:
            auth.verify_api_key(x_api_key="密钥-错误")
        assert ei.value.status_code == 401
        assert auth.verify_api_key(x_api_key="密钥-测试") is None

    def test_production_forces_auth_on(self, monkeypatch):
        from config.settings import compute_auth_enabled
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUTH_ENABLED", "0")
        assert compute_auth_enabled() is True

    def test_dev_default_off(self, monkeypatch):
        from config.settings import compute_auth_enabled
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("AUTH_ENABLED", "0")
        assert compute_auth_enabled() is False

    def test_explicit_on(self, monkeypatch):
        from config.settings import compute_auth_enabled
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("AUTH_ENABLED", "1")
        assert compute_auth_enabled() is True


class TestSseDisconnect:
    """SSE 断连兜底：客户端中途断开也要保存历史、写审计（不刷错误日志）"""

    @staticmethod
    def _patch_pipeline(monkeypatch, tmp_path):
        from schemas.models import SearchResult
        from types import SimpleNamespace

        monkeypatch.setitem(cs.CHAT_CONFIG, "history_path", str(tmp_path))
        monkeypatch.setattr(cs, "filter_sensitive", lambda q: (False, q))
        monkeypatch.setattr(cs, "get_profile", lambda kb_id=None: {
            "custom_rules": {}, "fallback_answer": "兜底", "refuse_answer": "拒答",
            "system_prompt": "sys", "query_rewrite_prompt": ""})
        monkeypatch.setattr(cs, "answer_official_data_query", lambda q: None)
        monkeypatch.setattr(cs, "replace_aliases_with_official", lambda q: q)
        monkeypatch.setitem(cs.CACHE_CONFIG, "enabled", False)
        results = [SearchResult(content="资料", source="s.md", score=0.9, doc_id="d1")]
        monkeypatch.setattr(cs, "_retrieve", lambda q, h, kb, profile=None: (q, results))
        monkeypatch.setattr(cs, "_critic_refine",
                            lambda q, rw, rs, kb: (rw, rs, []))

        class FakeChain:
            def stream(self, _):
                yield SimpleNamespace(content="第一段")
                yield SimpleNamespace(content="第二段")

        class FakePrompt:
            def __or__(self, other):
                return FakeChain()

        monkeypatch.setattr(cs, "_build_rag_messages", lambda *a, **k: FakePrompt())
        audit_calls = []
        monkeypatch.setattr(cs, "log_qa", lambda *a, **k: audit_calls.append(a))
        return audit_calls

    def test_disconnect_midstream_saves_history_and_audit(self, monkeypatch, tmp_path):
        audit_calls = self._patch_pipeline(monkeypatch, tmp_path)
        gen = cs.chat_single_turn_stream("web_disc_test", "测试断连", "valorant")
        ev1 = next(gen)   # sources
        ev2 = next(gen)   # 第一个 token（"第一段"）
        assert ev1["type"] == "sources"
        assert ev2["delta"] == "第一段"
        gen.close()       # 模拟客户端断连（GeneratorExit）

        history = cs.load_history("web_disc_test")
        # 断连前已产出的部分答案要落历史：user 问题 + assistant 部分答案
        assert history[-2].content == "测试断连"
        assert history[-1].content == "第一段"
        assert audit_calls, "断连后必须补写审计记录"

    def test_disconnect_before_any_answer_saves_nothing(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch, tmp_path)
        gen = cs.chat_single_turn_stream("web_disc_empty", "测试断连", "valorant")
        next(gen)         # sources（还没有答案产出）
        gen.close()

        assert cs.load_history("web_disc_empty") == []

    def test_normal_completion_not_double_saved(self, monkeypatch, tmp_path):
        audit_calls = self._patch_pipeline(monkeypatch, tmp_path)
        events = list(cs.chat_single_turn_stream("web_norm", "完整走完", "valorant"))
        assert events[-1]["type"] == "done"
        history = cs.load_history("web_norm")
        assert history[-1].content == "第一段第二段"   # 完整答案
        assert len(history) == 2                        # 不因 finally 重复追加
        assert len(audit_calls) == 1                    # 审计只写一条


class TestDegradeNone:
    """加载期 OOM 降级：degrade_to_cpu(None) 必须安全跳过（加载期还没有模型实例）"""

    def test_none_noop(self):
        degrade_to_cpu(None)  # 不抛即通过
