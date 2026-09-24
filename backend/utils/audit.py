# 【新增 v3.0】问答审计日志：企业合规刚需
# ------------------------------------------------------------
# 每次问答追加一行 JSON（JSONL 格式），记录：谁问了什么、检索命中
# 了什么、答了什么、耗时多久。按月分文件，出问题可回溯定位。
# 位置：backend/data/audit/audit_YYYYMM.jsonl
# 【v3.22】可观测性扩展（全部向后兼容，只加字段不改旧字段）：
#   - error_class：失败分类（rate_limit/timeout/empty_answer/generation_error/refusal/none）
#   - rerank_top_score / retrieved_count：重排 top1 分数与最终召回块数
#   - token_usage：本次问答全部 LLM 调用的 token 用量（总和 + 分环节；取不到
#     真实 usage 时用估算并把 estimated 置 True）
#   - prev_hash / self_hash：简版哈希链，逐条 SHA-256 链式衔接，防篡改可校验
# ------------------------------------------------------------
import hashlib
import json
import os
import threading
import time

from config.settings import DATA_DIR

AUDIT_DIR = os.path.join(DATA_DIR, "audit")

_chain_lock = threading.Lock()
_last_self_hash: str | None = None  # 进程内链尾缓存（None 时首次写入前从文件恢复）


# -------------------------- Token 用量记账（v3.22） --------------------------

def usage_from_response(resp) -> dict | None:
    """从 langchain AI 响应（AIMessage）提取智谱返回的真实 usage；取不到返回 None"""
    md = getattr(resp, "response_metadata", None) or {}
    tu = md.get("token_usage") or {}
    if tu.get("total_tokens"):
        return {
            "prompt_tokens": int(tu.get("prompt_tokens") or 0),
            "completion_tokens": int(tu.get("completion_tokens") or 0),
            "total_tokens": int(tu.get("total_tokens") or 0),
        }
    return None


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中文按 ~0.85 token/字，ASCII 按 ~0.25 token/字符（仅供估算口径）"""
    if not text:
        return 0
    cn = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cn
    return max(1, round(cn * 0.85 + other * 0.25))


def record_call(ledger: dict | None, stage: str, model: str,
                usage: dict | None = None,
                est_prompt: int = 0, est_completion: int = 0):
    """向问答级账本记一次 LLM 调用：有真实 usage 记真实值，否则记估算值并标 estimated"""
    if ledger is None:
        return
    if usage:
        ledger["entries"].append({"stage": stage, "model": model,
                                  "prompt_tokens": usage["prompt_tokens"],
                                  "completion_tokens": usage["completion_tokens"],
                                  "total_tokens": usage["total_tokens"],
                                  "estimated": False})
    else:
        ledger["entries"].append({"stage": stage, "model": model,
                                  "prompt_tokens": est_prompt,
                                  "completion_tokens": est_completion,
                                  "total_tokens": est_prompt + est_completion,
                                  "estimated": True})


def summarize_usage(ledger: dict | None) -> dict | None:
    """账本汇总：总和 + 分环节；任何一次估算则整体 estimated=True（诚实标注口径）"""
    if not ledger or not ledger.get("entries"):
        return None
    by_stage: dict = {}
    prompt = completion = total = 0
    for e in ledger["entries"]:
        prompt += e["prompt_tokens"]
        completion += e["completion_tokens"]
        total += e["total_tokens"]
        by_stage[e["stage"]] = by_stage.get(e["stage"], 0) + e["total_tokens"]
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimated": any(e["estimated"] for e in ledger["entries"]),
        "calls": len(ledger["entries"]),
        "by_stage": by_stage,
    }


# -------------------------- 审计写入（含哈希链） --------------------------

def _load_last_hash(audit_path: str) -> str:
    """从当月文件尾部恢复链尾哈希（进程重启后续链）；文件空/损坏/旧格式返回空串"""
    last = ""
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("self_hash"):
                    last = rec["self_hash"]
    except OSError:
        pass
    return last


def _record_digest(record: dict) -> str:
    """对记录关键内容（除哈希字段本身）做规范化序列化"""
    core = {k: v for k, v in record.items() if k not in ("prev_hash", "self_hash")}
    return json.dumps(core, ensure_ascii=False, sort_keys=True)


def _compute_self_hash(record: dict, prev_hash: str) -> str:
    return hashlib.sha256((_record_digest(record) + "|" + prev_hash).encode("utf-8")).hexdigest()


def log_qa(session_id: str, question: str, rewritten_question: str,
           sources: list, answer: str, latency_ms: float,
           kb_id: str = "", pipeline: str = "",
           error_class: str = "none",
           rerank_top_score: float | None = None,
           retrieved_count: int | None = None,
           token_usage: dict | None = None):
    """追加一条问答审计记录，写入失败只打日志，绝不影响主流程
    【v3.22】同时落 error_class/重排分数/召回数/token 用量，并接入哈希链"""
    global _last_self_hash
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        month = time.strftime("%Y%m")
        path = os.path.join(AUDIT_DIR, f"audit_{month}.jsonl")
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session_id,
            "kb_id": kb_id,
            "question": question,
            "rewritten_question": rewritten_question,  # 查询改写结果（无改写则与原问题相同）
            "sources": sources,
            "answer": answer,
            "latency_ms": round(latency_ms),
            "pipeline": pipeline,  # 本次问答走的检索管线（hybrid+rerank / vector）
            "error_class": error_class,  # 【v3.22】失败分类
        }
        if rerank_top_score is not None:
            record["rerank_top_score"] = round(float(rerank_top_score), 4)
        if retrieved_count is not None:
            record["retrieved_count"] = int(retrieved_count)
        if token_usage is not None:
            record["token_usage"] = token_usage
        # 【v3.22】哈希链：prev_hash=上一条 self_hash，self_hash 对本条内容+prev 链式计算
        with _chain_lock:
            if _last_self_hash is None:
                _last_self_hash = _load_last_hash(path)
            record["prev_hash"] = _last_self_hash
            record["self_hash"] = _compute_self_hash(record, _last_self_hash)
            _last_self_hash = record["self_hash"]
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[审计日志] 写入失败(不影响问答): {e}")


def verify_chain(records: list) -> list:
    """校验审计哈希链，返回断链位置描述列表（空列表=链完整）。
    向后兼容：无 self_hash 的旧记录跳过校验，链从其后第一条有哈希的记录重新衔接。"""
    breaks = []
    prev = ""
    for i, rec in enumerate(records):
        if not rec.get("self_hash"):
            continue  # 旧格式记录（无哈希字段），不参与校验
        expect_prev = rec.get("prev_hash", "")
        actual_self = _compute_self_hash(rec, expect_prev)
        if actual_self != rec["self_hash"]:
            breaks.append(f"第{i + 1}条记录 self_hash 校验失败（内容被篡改或记录不完整）")
        if expect_prev != prev:
            breaks.append(f"第{i + 1}条记录 prev_hash 与前一条 self_hash 断链")
        prev = rec["self_hash"]
    return breaks


def read_recent_audit(days: int = 7) -> list[dict]:
    """读取最近 N 天的审计记录（运维排查用）"""
    records = []
    if not os.path.isdir(AUDIT_DIR):
        return records
    cutoff = time.time() - days * 86400
    for name in sorted(os.listdir(AUDIT_DIR), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(AUDIT_DIR, name), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    ts = time.mktime(time.strptime(rec["time"], "%Y-%m-%d %H:%M:%S"))
                    if ts >= cutoff:
                        records.append(rec)
        except Exception:
            continue
    return records
