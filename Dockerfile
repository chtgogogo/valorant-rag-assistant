# 后端镜像：Python 3.12 + 项目依赖 + 预置知识库/官方数据
# ------------------------------------------------------------
# 模型权重（bge-small-zh / bge-reranker，约 1-2GB）不进镜像——
# 首次启动时由容器按 HF_ENDPOINT 镜像源下载，经 volume 缓存复用；
# 知识库与官方结构化数据（knowledge_base/）随镜像分发，保证开箱可问。
# 密钥绝不写进镜像：ZHIPU_API_KEY 运行时经 docker-compose 的 env_file 注入。
# ------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    EMBEDDING_DEVICE=cpu

# 系统依赖：gcc 编译部分轮子用（chromadb/rank_bm25 链路）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷依赖清单再装依赖：代码改动不触发依赖层重建
# torch 用标准 PyPI 轮（自带 CUDA 支持）：与宿主"GPU/CPU 自动切换"行为一致——
# 镜像里带上 CUDA 运行库（体积大些），以后容器开 GPU 只改 compose 配置、不用重建依赖层；
# 实际用哪个设备由 compose 的 EMBEDDING_DEVICE 环境变量决定（本项目 device_manager 自动探测）
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 拷代码与知识库/官方数据（不拷 backend/data：运行时数据全部走卷）
COPY backend /app/backend
COPY knowledge_base /app/knowledge_base

# 运行时数据目录（卷挂载点：会话历史/审计/上传/向量库/缓存epoch）
RUN mkdir -p /app/backend/data
VOLUME ["/app/backend/data"]

WORKDIR /app/backend
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -sf http://127.0.0.1:8001/health || exit 1

CMD ["python", "main.py"]
