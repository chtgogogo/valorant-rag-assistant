# -*- coding: utf-8 -*-
"""把点踩（rating='down'）反馈记录导出为评测集候选，打通「用户反馈→评测资产」回流管线。

用法: py backend/scripts/export_feedback_to_eval.py
幂等: 已写入 badcase_candidates.jsonl 的 feedback_id 不会重复导出。
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.db"
OUT_PATH = PROJECT_ROOT / "backend" / "eval" / "badcase_candidates.jsonl"


def load_exported_ids() -> set[int]:
    """扫描已有 JSONL，收集已导出的 feedback_id（幂等去重依据）。"""
    exported: set[int] = set()
    if not OUT_PATH.exists():
        return exported
    with OUT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                exported.add(json.loads(line)["feedback_id"])
            except (json.JSONDecodeError, KeyError):
                continue  # 坏行跳过，不影响整体导出
    return exported


def main() -> int:
    if not DB_PATH.exists():
        print(f"未找到反馈库 {DB_PATH.name}，跳过导出")
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, session_id, question, answer FROM feedback "
            "WHERE rating = 'down' ORDER BY id").fetchall()
    finally:
        conn.close()

    if not rows:
        print("feedback.db 中无点踩记录，无需导出")
        return 0

    exported = load_exported_ids()
    exported_at = datetime.now().isoformat(timespec="seconds")
    new_lines = []
    for r in rows:
        if r["id"] in exported:
            continue
        new_lines.append(json.dumps({
            "feedback_id": r["id"],
            "question": r["question"],
            "reference_answer": r["answer"],
            "answer_status": "待人工复核",
            "source": "user_thumb_down",
            "session_id": r["session_id"],
            "exported_at": exported_at,
        }, ensure_ascii=False))

    if not new_lines:
        print(f"点踩记录 {len(rows)} 条均已导出过，本次新增 0 条")
        return 0

    with OUT_PATH.open("a", encoding="utf-8") as f:
        for line in new_lines:
            f.write(line + "\n")
    print(f"已导出 {len(new_lines)} 条点踩记录到 {OUT_PATH.name}（累计 {len(exported) + len(new_lines)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
