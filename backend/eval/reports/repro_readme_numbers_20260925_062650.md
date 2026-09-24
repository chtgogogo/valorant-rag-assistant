# README 数字落档复现（2026-09-25 06:26:21）

- **限流最坏反馈耗时**（mock 全 429，重试1次+1.5s退避+快速失败）：**0.01s**（v3.11 README 口径「2 分钟 → 2.3 秒」的复现值）

- **检索链路延迟**（cpu 口径，关闭改写，预热后 10 次）：P50=995ms / P95=1109ms / mean=980ms
- ⚠️ GPU 口径需在真机跑（本环境 CUDA 受限）：`python scripts/repro_readme_numbers.py --retrieval-only`（ unset EMBEDDING_DEVICE 让其自动探测 GPU）
