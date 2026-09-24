# 【v3.22】可观测性回归测试：审计新字段（error_class/重排分数/召回数/token）/
# 哈希链（追加衔接/篡改检出/旧格式兼容）/ Token 记账（真实 usage 优先+估算标注）/
# audit_report 统计正确性（假 jsonl 驱动）
import json

import pytest

import utils.audit as au
from utils.audit import (log_qa, verify_chain, record_call, summarize_usage,
                         usage_from_response, estimate_tokens)


@pytest.fixture()
def audit_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(au, "AUDIT_DIR", str(tmp_path / "audit"))
    au._last_self_hash = None  # 每个用例独立链
    yield tmp_path / "audit"
    au._last_self_hash = None


def _read_all(base) -> list:
    recs = []
    p = base / "audit_202609.jsonl"  # 测试运行当月文件名
    for f in base.glob("audit_*.jsonl"):
        with open(f, "r", encoding="utf-8") as fh:
            recs.extend(json.loads(line) for line in fh if line.strip())
    return recs


class TestAuditNewFields:
    def test_log_qa_writes_new_fields(self, audit_dir):
        log_qa("s1", "q", "q", [{"name": "a"}], "答", 123.0, "valorant", "hybrid+rerank",
               error_class="refusal", rerank_top_score=0.8123, retrieved_count=5,
               token_usage={"prompt_tokens": 100, "completion_tokens": 20,
                            "total_tokens": 120, "estimated": False, "calls": 2,
                            "by_stage": {"rewrite": 40, "generate": 80}})
        rec = _read_all(audit_dir)[0]
        assert rec["error_class"] == "refusal"
        assert rec["rerank_top_score"] == 0.8123
        assert rec["retrieved_count"] == 5
        assert rec["token_usage"]["total_tokens"] == 120
        assert rec["token_usage"]["by_stage"]["rewrite"] == 40

    def test_default_error_class_none(self, audit_dir):
        log_qa("s1", "q", "q", [], "答", 10, "valorant", "vector")
        assert _read_all(audit_dir)[0]["error_class"] == "none"


class TestHashChain:
    def test_chain_links_and_verifies(self, audit_dir):
        for i in range(5):
            log_qa(f"s{i}", f"q{i}", f"q{i}", [], f"a{i}", 10.0 * i, "valorant", "hybrid")
        recs = _read_all(audit_dir)
        assert len(recs) == 5
        for prev, cur in zip(recs, recs[1:]):
            assert cur["prev_hash"] == prev["self_hash"], "相邻记录 prev_hash 必须衔接上一条 self_hash"
        assert verify_chain(recs) == [], "完整链校验应零断链"

    def test_tamper_detected(self, audit_dir):
        log_qa("s1", "q", "q", [], "原始答案", 10, "valorant", "hybrid")
        log_qa("s1", "q2", "q2", [], "答2", 10, "valorant", "hybrid")
        recs = _read_all(audit_dir)
        recs[0]["answer"] = "被篡改的答案"
        breaks = verify_chain(recs)
        assert breaks, "篡改记录内容必须被哈希链检出"

    def test_old_format_records_skipped(self, audit_dir):
        """向后兼容：旧记录（无哈希字段）跳过校验，链从其后第一条新记录重新衔接"""
        path = audit_dir / "audit_202609.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        old_rec = {"time": "2026-09-01 10:00:00", "session_id": "old", "answer": "旧格式",
                   "latency_ms": 5, "pipeline": "hybrid"}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_rec, ensure_ascii=False) + "\n")

        log_qa("s1", "q", "q", [], "答", 10, "valorant", "hybrid")
        recs = _read_all(audit_dir)
        assert len(recs) == 2
        assert "self_hash" not in recs[0]
        assert recs[1]["prev_hash"] == "", "旧记录后第一条新记录 prev_hash 为空串（重新起链）"
        assert verify_chain(recs) == []

    def test_chain_resumes_after_restart(self, audit_dir):
        """进程重启（链尾缓存清零）后从文件尾恢复链，不重置为 GENESIS"""
        log_qa("s1", "q", "q", [], "答", 10, "valorant", "hybrid")
        first = _read_all(audit_dir)[0]["self_hash"]
        au._last_self_hash = None  # 模拟进程重启
        log_qa("s1", "q2", "q2", [], "答2", 10, "valorant", "hybrid")
        recs = _read_all(audit_dir)
        assert recs[1]["prev_hash"] == first, "重启后链必须从文件尾恢复衔接"


class TestUsageAccounting:
    def test_usage_from_response(self):
        from types import SimpleNamespace
        resp = SimpleNamespace(content="答",
                               response_metadata={"token_usage": {
                                   "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        assert usage_from_response(resp) == {"prompt_tokens": 100,
                                             "completion_tokens": 20, "total_tokens": 120}
        assert usage_from_response(SimpleNamespace(content="x", response_metadata={})) is None

    def test_record_real_vs_estimated(self):
        ledger = {"entries": []}
        record_call(ledger, "generate", "glm-4.7-flash",
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        record_call(ledger, "rewrite", "glm-4.7-flash", None, est_prompt=8, est_completion=2)
        s = summarize_usage(ledger)
        assert s["total_tokens"] == 25
        assert s["prompt_tokens"] == 18 and s["completion_tokens"] == 7
        assert s["estimated"] is True, "任一次估算 → 整体 estimated=True（诚实标注口径）"
        assert s["calls"] == 2
        assert s["by_stage"] == {"generate": 15, "rewrite": 10}

    def test_summarize_empty_ledger(self):
        assert summarize_usage({"entries": []}) is None
        assert summarize_usage(None) is None

    def test_estimate_tokens_mixed_text(self):
        cn = estimate_tokens("英雄技能介绍")
        assert 3 <= cn <= 6  # 中文 ~0.85 token/字
        assert estimate_tokens("") == 0

    def test_call_llm_with_retry_records_usage(self):
        """封装层记账：真实 usage 的响应 → 记真实值；全程不触网"""
        from types import SimpleNamespace
        import services.chat_service as cs
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnableLambda

        def fake_invoke(_):
            return SimpleNamespace(content="答案内容",
                                   response_metadata={"token_usage": {
                                       "prompt_tokens": 50, "completion_tokens": 10,
                                       "total_tokens": 60}})

        ledger = {"entries": []}
        prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
        monkey_model = RunnableLambda(fake_invoke)
        cs._call_llm_with_retry(prompt, {"question": "q"}, model=monkey_model,
                                usage_ledger=ledger)
        s = summarize_usage(ledger)
        assert s["total_tokens"] == 60 and s["estimated"] is False
        assert s["by_stage"] == {"generate": 60}


class TestAuditReport:
    @staticmethod
    def _fake_records() -> list:
        return [
            {"time": "2026-09-24 10:00:00", "latency_ms": 100, "error_class": "none",
             "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}},
            {"time": "2026-09-24 11:00:00", "latency_ms": 300, "error_class": "refusal",
             "token_usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90}},
            {"time": "2026-09-25 12:00:00", "latency_ms": 200, "error_class": "rate_limit",
             "token_usage": {"prompt_tokens": 90, "completion_tokens": 0, "total_tokens": 90,
                             "estimated": True}},
            {"time": "2026-09-25 13:00:00", "latency_ms": 50, "error_class": "none"},  # 无 token（缓存命中）
        ]

    def test_stats(self):
        from scripts.audit_report import build_report
        stats = build_report(self._fake_records())
        assert stats["total"] == 4
        assert stats["refusals"] == 1
        assert stats["refusal_rate"] == 0.25
        assert stats["error_dist"] == {"none": 2, "refusal": 1, "rate_limit": 1}
        assert stats["latency_p50"] == 200   # 最近秩法：4 样本 P50=第3小的值
        assert stats["latency_p95"] == 300
        assert stats["token_total"] == 330
        assert stats["token_per_turn"] == 110.0
        assert stats["token_records"] == 3
        assert stats["estimated_records"] == 1
        assert set(stats["by_date"]) == {"2026-09-24", "2026-09-25"}
        assert stats["by_date"]["2026-09-24"]["requests"] == 2
        assert stats["chain_breaks"] == []

    def test_empty_records(self):
        from scripts.audit_report import build_report
        stats = build_report([])
        assert stats["total"] == 0 and stats["refusal_rate"] == 0.0
