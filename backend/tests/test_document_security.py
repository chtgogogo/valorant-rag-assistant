# 文档接口输入安全：路径穿越防护（v3.14 修复用例转正为回归测试，防退化）
import os

import pytest

from routers import document_router as dr


class TestSafeFilename:
    """客户端文件名清洗：任何路径成分都要剥掉"""

    @pytest.mark.parametrize("raw,expected", [
        ("../../evil.md", "evil.md"),
        ("..\\..\\win.md", "win.md"),
        ("/etc/passwd.md", "passwd.md"),
        ("正常文档.md", "正常文档.md"),
        ("  spaced.md  ", "spaced.md"),
    ])
    def test_strips_path_components(self, raw, expected):
        assert dr._safe_filename(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",           # 空
        "..",         # 纯点
        ".",          # 纯点
        ".hidden.md", # 点开头
        "a" * 201,    # 超长
    ])
    def test_rejects_invalid(self, raw):
        with pytest.raises(ValueError):
            dr._safe_filename(raw)


class TestCheckKbId:
    """kb_id 会拼进注册表路径，必须过白名单正则"""

    @pytest.mark.parametrize("ok", ["valorant", "ecommerce", "kb-1", "a", "x" * 64])
    def test_accepts_valid(self, ok):
        dr._check_kb_id(ok)  # 不抛即通过

    @pytest.mark.parametrize("bad", [
        "../../x",
        "",
        None,
        "x" * 65,
        "a/b",
        "a\\b",
        "a b",
    ])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            dr._check_kb_id(bad)


class TestEnsureWithinUpload:
    """双保险：最终落盘路径必须仍在上传目录内（realpath 边界）"""

    def test_inside_upload_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "UPLOAD_PATH", str(tmp_path))
        target = dr._ensure_within_upload(str(tmp_path / "a.md"))
        assert target == os.path.realpath(str(tmp_path / "a.md"))

    def test_outside_upload_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "UPLOAD_PATH", str(tmp_path))
        with pytest.raises(ValueError):
            dr._ensure_within_upload(str(tmp_path.parent / "evil.md"))
