# 【新增 v3.0】问答审计日志：企业合规刚需
# ------------------------------------------------------------
# 每次问答追加一行 JSON（JSONL 格式），记录：谁问了什么、检索命中
# 了什么、答了什么、耗时多久。按月分文件，出问题可回溯定位。
# 位置：backend/data/audit/audit_YYYYMM.jsonl
# ------------------------------------------------------------
import json
import os
import time

from config.settings import DATA_DIR

AUDIT_DIR = os.path.join(DATA_DIR, "audit")


def log_qa(session_id: str, question: str, rewritten_question: str,
           sources: list, answer: str, latency_ms: float,
           kb_id: str = "", pipeline: str = ""):
    """追加一条问答审计记录，写入失败只打日志，绝不影响主流程"""
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
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[审计日志] 写入失败(不影响问答): {e}")


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
