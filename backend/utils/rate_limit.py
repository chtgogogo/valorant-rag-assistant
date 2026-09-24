# 【新增 v3.24】对话接口 IP 限流：防脚本刷问答烧 token
# ------------------------------------------------------------
# 为什么需要：服务器主模型已切付费通道（按量计费）。语义缓存能挡"重复问题"，
# 挡不住"每人问一句不同的"——限流是 token 预算的根本闸门。
# 实现：进程内固定窗口计数（单进程部署足够；与失物招领系统的 rate_limit 同思路）。
# 双层：每分钟 N 次（挡刷屏脚本）+ 每日 M 次（挡长期薅）。
# 默认值按"演示站"口径：5 次/分钟、100 次/天/IP——一天最多烧约 3 分钱 token。
# ------------------------------------------------------------
import threading
import time
from collections import defaultdict, deque

from config.settings import RATE_LIMIT_CONFIG


class RateLimitExceeded(Exception):
    """限流命中（429）。message 面向用户展示。"""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class _SlidingWindow:
    """线程安全固定窗口计数器。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_sec: int) -> tuple[bool, int]:
        """记录一次并判定：返回 (是否放行, 距窗口重置的秒数)。"""
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_sec:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window_sec - (now - q[0])))
            q.append(now)
            return True, 0

    def reset(self, key: str):
        with self._lock:
            self._hits.pop(key, None)


_minute_window = _SlidingWindow()
_daily_window = _SlidingWindow()


def check_chat_rate_limit(ip: str) -> None:
    """对话接口统一入口限流：分钟窗口 → 日窗口，任一超限即抛 RateLimitExceeded(429)。"""
    if not RATE_LIMIT_CONFIG["enabled"]:
        return
    ok, retry = _minute_window.hit(
        f"m:{ip}", RATE_LIMIT_CONFIG["per_minute"], 60
    )
    if not ok:
        raise RateLimitExceeded(
            f"提问太频繁啦，休息 {retry} 秒再来～", retry_after=retry
        )
    ok, retry = _daily_window.hit(
        f"d:{ip}", RATE_LIMIT_CONFIG["daily"], 86400
    )
    if not ok:
        raise RateLimitExceeded(
            "今天的提问次数用完啦（防 token 被刷的温柔上限），明天再来～",
            retry_after=max(60, 86400 - int(time.time()) % 86400),
        )
