# 【新增 v3.33】数据卷运维维护：会话历史过期清理 + 审计日志归档轮转
# ------------------------------------------------------------
# 报告第三道坎：三类只增不减的数据里，会话 JSON（一会话一文件，无过期
# 策略）与审计 jsonl（哈希链防篡改，不能随便删）都会随时间把磁盘塞满。
#
# 设计：
#   - 会话历史：删除 mtime 超过 session_retention_days 的 *.json——mtime 在
#     每次追加问答时都会刷新，"N 天没动过"即"N 天没有新对话"。
#   - 审计日志：哈希链逐条衔接，直接删旧文件等于亲手打断防篡改证明——
#     改为无损 gzip 归档：超过 audit_retention_months 的 audit_YYYYMM.jsonl
#     压缩为 .jsonl.gz 后删除原文件，字节一个不丢，追溯时解压即可复验哈希链；
#     read_recent_audit 天然只扫 .jsonl，归档件自动退出日常查询范围。
#   - main.py 启动时拉起守护线程：先清一轮，之后每 interval_hours 小时一轮；
#     所有动作只作用于自己的数据目录，单文件失败跳过并记日志，绝不影响主流程。
# ------------------------------------------------------------
import gzip
import logging
import os
import re
import shutil
import threading
import time

logger = logging.getLogger(__name__)

_AUDIT_MONTH_RE = re.compile(r"^audit_(\d{4})(\d{2})\.jsonl$")


def cleanup_expired_sessions(history_dir: str, retention_days: int,
                             now: float = None) -> int:
    """删除超过保留期的会话文件，返回删除数；目录不存在/逐个失败均安全跳过"""
    if retention_days <= 0 or not os.path.isdir(history_dir):
        return 0
    cutoff = (now or time.time()) - retention_days * 86400
    removed = 0
    for name in os.listdir(history_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(history_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as e:
            logger.warning("[数据维护] 会话清理跳过 %s: %s", name, e)
    return removed


def rotate_old_audits(audit_dir: str, retention_months: int,
                      now: float = None) -> list[str]:
    """把超过保留月数的 audit_YYYYMM.jsonl 压缩为 .jsonl.gz 并删原文件（无损）。
    返回归档文件名列表。月粒度判断：与当前月相距 >= retention_months 个月即归档。"""
    if retention_months <= 0 or not os.path.isdir(audit_dir):
        return []
    tm = time.localtime(now or time.time())
    cur_index = tm.tm_year * 12 + tm.tm_mon
    archived = []
    for name in sorted(os.listdir(audit_dir)):
        m = _AUDIT_MONTH_RE.match(name)
        if not m:
            continue
        month_index = int(m.group(1)) * 12 + int(m.group(2))
        if month_index > cur_index - retention_months:
            continue  # 保留期内
        src = os.path.join(audit_dir, name)
        dst = src + ".gz"
        if os.path.exists(dst):
            # 上轮中断的半成品归档：删掉重做（重压缩成本≈0），不永久卡在"跳过"
            try:
                os.remove(dst)
            except OSError as e:
                logger.warning("[数据维护] 无法删除残留归档 %s，跳过 %s: %s", dst, name, e)
                continue
        try:
            with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(src)
            archived.append(name + ".gz")
        except OSError as e:
            logger.warning("[数据维护] 审计归档失败 %s（原文件保留）: %s", name, e)
            try:
                if os.path.exists(dst):
                    os.remove(dst)  # 删掉写了一半的坏归档，下轮重试
            except OSError:
                pass
    return archived


def run_maintenance_once() -> dict:
    """跑一轮全部维护项（启动后首清与周期轮询共用这一个入口）"""
    from config.settings import CHAT_CONFIG, MAINTENANCE_CONFIG
    from utils.audit import AUDIT_DIR

    sessions = cleanup_expired_sessions(
        CHAT_CONFIG["history_path"], MAINTENANCE_CONFIG["session_retention_days"])
    audits = rotate_old_audits(
        AUDIT_DIR, MAINTENANCE_CONFIG["audit_retention_months"])
    if sessions or audits:
        logger.info("[数据维护] 本轮完成：清理过期会话 %d 个，归档审计 %d 个文件",
                    sessions, len(audits))
    return {"sessions_removed": sessions, "audits_archived": audits}


_daemon_started = threading.Event()


def start_maintenance_daemon(interval_hours: float = None):
    """启动运维维护守护线程（进程内仅一次；先立即清一轮再进入周期等待）"""
    if _daemon_started.is_set():
        return
    _daemon_started.set()
    from config.settings import MAINTENANCE_CONFIG

    interval_sec = (interval_hours or MAINTENANCE_CONFIG["interval_hours"]) * 3600

    def _loop():
        while True:
            try:
                run_maintenance_once()
            except Exception as e:  # 维护永不影响主流程
                logger.error("[数据维护] 本轮失败(下轮继续): %s", e)
            time.sleep(interval_sec)

    threading.Thread(target=_loop, name="ops-maintenance", daemon=True).start()
