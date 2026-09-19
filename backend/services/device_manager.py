# 【新增 v3.3】动态设备选择：GPU 优先、显存不足/OOM 自动降级 CPU
# ------------------------------------------------------------
# 策略（应用启动时执行一次，结果全局复用）：
#   1. 显式设置 EMBEDDING_DEVICE=cpu/cuda 时完全尊重配置（兼容旧行为）
#   2. 未设置时自动探测：torch.cuda 可用且空闲显存 > 阈值 → cuda，否则 cpu
#      空闲显存用 cudaMemGetInfo（torch.cuda.mem_get_info）读取——
#      它反映整卡空闲（含游戏等其他进程占用），memory_allocated 只看自己，测不出外部占用
#   3. 运行中爆显存（OutOfMemoryError）：捕获后清空 CUDA 缓存、模型降级 CPU 重试
# BGE 向量模型与 CrossEncoder 重排模型共用同一个 device 配置，天然同进同退，
# 避免"一个在 CPU 一个在 GPU"造成 PCIe 来回拷贝反而变慢。
# ------------------------------------------------------------
import os
import logging

logger = logging.getLogger(__name__)

_resolved: str | None = None


def _torch():
    """延迟导入 torch（约 1.5GB，进程启动轻量化）"""
    import torch
    return torch


def _detect() -> tuple[str, str]:
    """探测应使用的设备，返回 (device, 原因说明)"""
    explicit = os.getenv("EMBEDDING_DEVICE", "").strip().lower()
    if explicit in ("cpu", "cuda"):
        return explicit, f"显式配置 EMBEDDING_DEVICE={explicit}"

    try:
        torch = _torch()
        if not torch.cuda.is_available():
            return "cpu", "torch 无 CUDA 支持（CPU 版构建）或无可用显卡"

        # 确定性优先：关掉 TF32 与 flash/sdpa 注意力内核（与 CPU 的 eager 实测一致），
        # 避免重排分数出现 ±0.01 级浮动翻转边界题的排序（实测 8/8 → 5/8 的教训）
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        try:
            torch.backends.cuda.enable_flash_sdp(False)
            torch.backends.cuda.enable_mem_efficient_sdp(False)
            torch.backends.cuda.enable_math_sdp(True)
        except Exception:
            pass  # 旧版 torch 无这些开关，忽略

        free_bytes, _total = torch.cuda.mem_get_info()
        free_mb = free_bytes / (1024 * 1024)
        min_free_mb = int(os.getenv("GPU_MIN_FREE_MB", "1536"))
        name = torch.cuda.get_device_name(0)
        if free_mb > min_free_mb:
            return "cuda", f"GPU {name} 空闲显存 {free_mb:.0f}MB > 阈值 {min_free_mb}MB"
        return "cpu", f"GPU {name} 空闲显存 {free_mb:.0f}MB ≤ 阈值 {min_free_mb}MB（可能被其他程序占用）"
    except Exception as e:
        return "cpu", f"GPU 探测异常({e})，保守回退 CPU"


def get_device() -> str:
    """获取本进程应使用的设备（首次调用探测并打印，之后缓存复用）"""
    global _resolved
    if _resolved is None:
        device, reason = _detect()
        _resolved = device
        print(f"[设备策略] 模型运行设备 = {device}（{reason}）")
        logger.info("设备策略: %s（%s）", device, reason)
    return _resolved


def is_oom_error(e: Exception) -> bool:
    """判断异常是否为 CUDA 显存不足"""
    try:
        torch = _torch()
        oom_cls = getattr(torch.cuda, "OutOfMemoryError", None)
        if oom_cls and isinstance(e, oom_cls):
            return True
    except Exception:
        pass
    return "CUDA out of memory" in str(e) or "OutOfMemoryError" in type(e).__name__


def degrade_to_cpu(model) -> None:
    """把已加载模型降级到 CPU 并清空 CUDA 缓存（OOM 后调用）"""
    global _resolved
    try:
        torch = _torch()
        model.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        _resolved = "cpu"
        print("[设备策略] 检测到显存不足(OOM)，模型已降级到 CPU，本次运行不再回到 GPU")
        logger.warning("OOM 降级：模型已切换到 CPU，CUDA 缓存已清空")
    except Exception as e:
        logger.error("OOM 降级失败: %s", e)
