# 【新增 v3.33】生成并发闸门：把"卡爆全站"变成"排队 + 友好拒绝"
# ------------------------------------------------------------
# 报告第一道坎：同步路由共享 uvicorn 线程池（默认约 40 席），生成请求
# 一多（付费通道 ~5.7s/条）就把席位全部占住——上传/列表/健康检查等轻
# 接口跟着饿死，等于一个慢请求类型劫持全站；且上游 API 排队时加机器
# 也没用。IP 限流（v3.24）挡的是"同一个人刷"，闸门挡的是"总量超过容量"，
# 两者互补。
#
# 设计：有界并发（inflight 上限）+ 有界等待队列（queue 上限）+ 限时等待：
#   - inflight 未满 → 直接进入；
#   - inflight 满但队列未满 → FIFO 排队等位，超过 queue_timeout 秒仍无位
#     则按队满拒绝（不无限等）；
#   - 队列也满 → 立即抛 GateFull(429)，前端拿到"人比较多"的提示而不是
#     无限转圈。
# 关键参数关系：max_concurrent + max_queue 必须 < 线程池大小（默认 40）——
# 闸门自己最多占 8+24=32 席，永远给轻接口留席。单进程部署下每进程一份
# 闸门；将来多进程/多容器时总上限 = 单闸 × 进程数。
# ------------------------------------------------------------
import threading
import time


class GateFull(Exception):
    """闸门排队已满 / 等待超时（429）。message 面向用户展示。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_QUEUE_FULL_MSG = "当前提问的人比较多，队列已满，请稍等几秒再试～"
_QUEUE_TIMEOUT_MSG = "排队等待超时啦，请稍后再试一次～"


class ConcurrencyGate:
    """线程安全的有界并发 + 有界排队闸门（with 语法进入/退出）。"""

    def __init__(self, max_inflight: int, max_queue: int, wait_timeout: float = 60.0):
        if max_inflight < 1 or max_queue < 0:
            raise ValueError("要求 max_inflight>=1 且 max_queue>=0")
        self._max_inflight = int(max_inflight)
        self._max_queue = int(max_queue)
        self._wait_timeout = float(wait_timeout)
        self._cond = threading.Condition()
        self._inflight = 0
        self._waiting = 0

    def enter(self):
        deadline = time.monotonic() + self._wait_timeout
        with self._cond:
            if self._inflight < self._max_inflight:
                self._inflight += 1
                return
            if self._waiting >= self._max_queue:
                raise GateFull(_QUEUE_FULL_MSG)
            self._waiting += 1
            try:
                while self._inflight >= self._max_inflight:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._cond.wait(remaining):
                        raise GateFull(_QUEUE_TIMEOUT_MSG)
                self._inflight += 1
            finally:
                self._waiting -= 1

    def leave(self):
        with self._cond:
            self._inflight = max(0, self._inflight - 1)
            self._cond.notify_all()

    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.leave()
        return False

    def stats(self) -> dict:
        with self._cond:
            return {"inflight": self._inflight, "waiting": self._waiting}


_gate: ConcurrencyGate | None = None
_gate_lock = threading.Lock()


def get_generation_gate() -> ConcurrencyGate:
    """生成闸门生产单例（懒加载；参数来自 RATE_LIMIT_CONFIG，入口防护同源）"""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                from config.settings import RATE_LIMIT_CONFIG
                _gate = ConcurrencyGate(
                    RATE_LIMIT_CONFIG["max_concurrent"],
                    RATE_LIMIT_CONFIG["max_queue"],
                    RATE_LIMIT_CONFIG["queue_timeout"],
                )
    return _gate
