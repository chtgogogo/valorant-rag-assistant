# 【新增 v3.0】重排序（Rerank）：两阶段检索的第二阶段
# ------------------------------------------------------------
# 参考网易有道 QAnything 的两阶段架构：
#   第一阶段（召回）：混合检索粗选出 top 10 候选，保证"不漏"
#   第二阶段（精排）：CrossEncoder 对"问题-候选"逐对深度打分，保证"排对"
# bge-reranker-base 本地运行无需密钥；任何失败自动降级为召回排序。
# ------------------------------------------------------------
import math
import os
import threading
from typing import TYPE_CHECKING

# HuggingFace 镜像与缓存位置（必须在 import sentence_transformers 之前设置）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

if TYPE_CHECKING:  # 只给类型检查器看；运行时不导入，避免启动就吃 1.5GB
    from sentence_transformers import CrossEncoder

from config.settings import RAG_CONFIG, EMBEDDING_CONFIG
from schemas.models import SearchResult

import logging
logger = logging.getLogger(__name__)

_cross_encoder = None
_cross_encoder_lock = threading.Lock()  # 【v3.25】并发首调只加载一次


def _get_cross_encoder() -> "CrossEncoder":
    """懒加载 bge-reranker 模型（GPU 优先，OOM 自动降级 CPU；与向量模型共用设备策略，同进同退）
    【v3.25】double-checked locking：并发首调时只创建一次实例"""
    global _cross_encoder
    if _cross_encoder is None:  # 先查（无锁快路径）
        with _cross_encoder_lock:
            if _cross_encoder is None:  # 再查（等锁期间可能已被别的线程加载）
                # 延迟导入：同样是避免启动时把 torch 拖进来
                from sentence_transformers import CrossEncoder
                from services.device_manager import get_device, is_oom_error, degrade_to_cpu
                model_name = RAG_CONFIG["rerank_model"]
                if "/" not in model_name:
                    model_name = f"BAAI/{model_name}"
                device = get_device()
                print(f"[重排序] 正在加载 Rerank 模型: {model_name} (device={device}) ...")
                try:
                    try:
                        # 【v3.31】优先本地缓存加载（与 vector_service 同策略）：
                        # 模型下载过一次后零网络依赖，hf-mirror 抖动不再拖垮检索链路
                        _cross_encoder = CrossEncoder(model_name, device=device, max_length=512, local_files_only=True)
                    except Exception:
                        print("[重排序] 本地缓存不可用，转在线下载模型（首次部署需联网）...")
                        _cross_encoder = CrossEncoder(model_name, device=device, max_length=512)
                except Exception as e:
                    if is_oom_error(e) and device == "cuda":
                        degrade_to_cpu(None)
                        _cross_encoder = CrossEncoder(model_name, device="cpu", max_length=512)
                    else:
                        raise
                print("[重排序] Rerank 模型加载完成")
    return _cross_encoder


def rerank(question: str, candidates: list[SearchResult],
           top_k: int = None) -> list[SearchResult]:
    """
    对召回候选精排：CrossEncoder 逐对打分 → sigmoid 归一到 0~1 → 取 top_k
    :param question: （改写后的）用户问题
    :param candidates: 混合召回的候选块
    :param top_k: 最终保留条数（默认 RAG_CONFIG.top_k）
    :return: 精排后的 SearchResult 列表（score 字段 = rerank 置信分）
             模型未启用或调用失败时，原样截断返回（降级不阻塞）
    """
    if top_k is None:
        top_k = RAG_CONFIG["top_k"]
    if not candidates:
        return []
    if not RAG_CONFIG.get("enable_rerank", True):
        return candidates[:top_k]

    # 候选太多时先截断，控制 CPU 推理耗时
    candidates = candidates[: RAG_CONFIG["rerank_candidates"]]

    try:
        model = _get_cross_encoder()
        pairs = [(question, c.content) for c in candidates]
        try:
            raw_scores = model.predict(pairs, show_progress_bar=False)
        except Exception as e:
            # 推理中爆显存：模型降级 CPU 后原地重试一次（GPU 被游戏抢占的场景）
            from services.device_manager import is_oom_error, degrade_to_cpu
            if is_oom_error(e) and str(getattr(model, "device", "")) != "cpu":
                degrade_to_cpu(model)
                raw_scores = model.predict(pairs, show_progress_bar=False)
            else:
                raise
        # bge-reranker 输出 logit，过 sigmoid 转成 0~1 的置信分
        scores = [1.0 / (1.0 + math.exp(-float(s))) for s in raw_scores]

        for c, s in zip(candidates, scores):
            c.score = round(s, 4)

        ranked = sorted(candidates, key=lambda r: r.score, reverse=True)[:top_k]
        logger.info("重排序完成: %d 个候选 → 取前 %d，top1 分数 %.3f",
                    len(candidates), len(ranked), ranked[0].score if ranked else 0)
        return ranked
    except Exception as e:
        logger.warning("重排序失败(降级为召回排序): %s", e)
        # 【v3.16】标记量纲已降级：此时 score 是召回分（BM25/RRF），非 rerank sigmoid 分，
        # 兜底判定（_should_fallback）据此改用 dense_score 余弦阈值，避免误判
        for c in candidates:
            c.rerank_degraded = True
        return candidates[:top_k]
