# -*- coding: utf-8 -*-
# 【新增 v3.33】一键数据备份脚本：把"丢了就回不去"的运行时数据打成一个 zip
# ------------------------------------------------------------
# 报告第五道坎：单机无备份，向量库/审计卷一坏全丢。本脚本把全部运行时
# 数据卷快照成单个压缩包，便于定时任务跑 + scp 拉走异地存放：
#   - chroma_db/        向量库（最核心，重建成本 = 全量重新入库）
#   - audit/            审计日志（含已归档 .gz；哈希链字节级保留）
#   - chat_history/     会话历史
#   - uploads/ + doc_list_*.json  上传原文 + 文档注册表（两者必须成对恢复）
#   - tickets.db / feedback.db    工单与反馈库（人工答案回流的知识资产）
#   - kb_epoch          缓存失效信号
# 不备份 agent_traces/（调试轨迹，可再生）。
#
# 用法（建议挂每日定时任务）：
#   cd backend
#   python scripts/backup_data.py                    # 备到 data/backups/，保留最近 7 份
#   python scripts/backup_data.py --dest D:/backup   # 备到外部目录（异地盘/挂载卷）
#   python scripts/backup_data.py --keep 14          # 多保留几份
# ------------------------------------------------------------
import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

# 脚本直接运行时，将 backend 目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DATA_DIR

BACKUP_NAME_PREFIX = "data_backup_"
# (数据卷相对 DATA_DIR 的路径, 是否整个目录打包)；目录不存在时跳过并提示
DIR_TARGETS = ["chroma_db", "audit", "chat_history", "uploads"]
FILE_TARGETS = ["tickets.db", "feedback.db", "kb_epoch"]


def _iter_doc_lists() -> list[Path]:
    return sorted(Path(DATA_DIR).glob("doc_list_*.json"))


def create_backup(dest_dir: str) -> tuple[Path, int]:
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(dest_dir, f"{BACKUP_NAME_PREFIX}{stamp}.zip")
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in DIR_TARGETS:
            root = Path(DATA_DIR) / rel
            if not root.is_dir():
                print(f"[备份] 跳过不存在的目录: {rel}")
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    arc = os.path.join("data", os.path.relpath(full, DATA_DIR))
                    zf.write(full, arc)
                    file_count += 1
        for rel in FILE_TARGETS:
            full = Path(DATA_DIR) / rel
            if full.is_file():
                zf.write(full, os.path.join("data", rel))
                file_count += 1
        for reg in _iter_doc_lists():
            zf.write(reg, os.path.join("data", reg.name))
            file_count += 1
    return Path(zip_path), file_count


def prune_old_backups(dest_dir: str, keep: int) -> int:
    """只清理本脚本产出的备份（按文件名前缀识别），按时间留最新 keep 份"""
    backups = sorted(
        (p for p in Path(dest_dir).glob(f"{BACKUP_NAME_PREFIX}*.zip")),
        key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in backups[keep:]:
        os.remove(old)
        removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description="RAG 助手运行时数据一键备份")
    parser.add_argument("--dest", default=str(Path(DATA_DIR) / "backups"),
                        help="备份输出目录（建议指向另一块盘/挂载卷）")
    parser.add_argument("--keep", type=int, default=7, help="保留最近 N 份（默认 7）")
    args = parser.parse_args()

    started = time.time()
    zip_path, file_count = create_backup(args.dest)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    removed = prune_old_backups(args.dest, max(1, args.keep))
    kept = len(list(Path(args.dest).glob(f"{BACKUP_NAME_PREFIX}*.zip")))
    print(f"[备份] 完成: {zip_path}")
    print(f"[备份] 打包文件 {file_count} 个，包大小 {size_mb:.1f}MB，耗时 {time.time() - started:.1f}s")
    print(f"[备份] 当前共 {kept} 份备份" + (f"（已清理最旧 {removed} 份）" if removed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
