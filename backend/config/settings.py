import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录 / 后端根目录 / 数据目录（统一绝对路径，避免从不同目录启动导致数据不一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"

# 加载项目根目录下的 .env 文件
load_dotenv(PROJECT_ROOT / ".env")

# ------------------------------------------------------
# 0. 领域皮肤配置（企业化改造关键）
#    换领域 = 复制 domain_profiles/valorant.yaml 改内容，
#    再设环境变量 APP_DOMAIN=新领域名，代码零修改
# ------------------------------------------------------
DOMAIN = os.getenv("APP_DOMAIN", "valorant")
_PROFILE_PATH = Path(__file__).resolve().parent / "domain_profiles" / f"{DOMAIN}.yaml"
if not _PROFILE_PATH.exists():
    raise ValueError(f"❌ 领域皮肤配置不存在: {_PROFILE_PATH}，请在 config/domain_profiles/ 下创建")
with open(_PROFILE_PATH, "r", encoding="utf-8") as _f:
    DOMAIN_PROFILE = yaml.safe_load(_f)

# ------------------------------------------------------
# 1. 大模型配置（从 .env 读取密钥，绝不硬编码）
# ------------------------------------------------------
LLM_CONFIG = {
    "api_key": os.getenv("ZHIPU_API_KEY"),
    "base_url": os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    # glm-4.7-flash：智谱免费模型（30B MoE）；RAG 要快、要有依据，默认关闭思考模式，
    # 需要深度推理时设 LLM_THINKING=1（免费模型限流时 _call_llm_with_retry 自动退避重试）
    "model_name": os.getenv("ZHIPU_MODEL", "glm-4.7-flash"),
    "thinking": os.getenv("LLM_THINKING", "0") == "1",            # 最终答案生成：默认关（保速度）
    "thinking_rewrite": os.getenv("LLM_REWRITE_THINKING", "1") == "1",  # 查询改写：默认开（精度优先，多轮指代消解受益；在意延迟可设 0）
    "thinking_critic": os.getenv("LLM_CRITIC_THINKING", "1") == "1",    # 质量自评：默认开（精度收益最大）
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
    "timeout": int(os.getenv("LLM_TIMEOUT", "30")),               # 普通调用超时
    "thinking_timeout": int(os.getenv("LLM_THINKING_TIMEOUT", "60")),  # 开思考的调用放宽（思考本身 10~30s）
}

# 检查一下有没有读到密钥（如果没读到，程序直接报错提醒你）
if not LLM_CONFIG["api_key"]:
    raise ValueError("❌ 没有找到 ZHIPU_API_KEY！请在项目根目录创建 .env 文件并写入你的密钥。")

# ------------------------------------------------------
# 2. Embedding 模型配置（本地跑的，不需要密钥）
# ------------------------------------------------------
EMBEDDING_CONFIG = {
    "model_name": os.getenv("EMBEDDING_MODEL", "bge-small-zh-v1.5"),
    "device": os.getenv("EMBEDDING_DEVICE", "cpu")
}

# ------------------------------------------------------
# 3. 向量库配置
# ------------------------------------------------------
VECTOR_DB_PATH = str(DATA_DIR / "chroma_db")

# ------------------------------------------------------
# 4. 文件上传配置
# ------------------------------------------------------
UPLOAD_PATH = str(DATA_DIR / "uploads")
ALLOWED_EXTENSIONS = ["docx", "pdf", "txt", "md"]
MAX_FILE_SIZE = 10 * 1024 * 1024

# ------------------------------------------------------
# 5. 对话与兜底配置
# ------------------------------------------------------
CHAT_CONFIG = {
    "max_history_turns": 20,
    "similarity_threshold": 0.35,
    "history_path": str(DATA_DIR / "chat_history")
}

# ------------------------------------------------------
# 5.1 RAG 检索配置（v3.0：查询改写 + 混合检索 + 重排序）
#     开关均可用环境变量覆盖，方便答辩演示时对比效果
# ------------------------------------------------------
RAG_CONFIG = {
    # 向量检索总开关（v3.3）：关闭后仅走 BM25 关键词检索；
    # 注意 USE_VECTOR_RETRIEVAL=0 时 RAG_HYBRID 无意义（混合的前提是向量这一路存在）
    "enable_vector_search": os.getenv("USE_VECTOR_RETRIEVAL", "1") == "1",
    # 质量自评 Critic（v3.3）：重排分数落在 [阈值, CRITIC_SCORE_HIGH) 灰区时触发，
    # 判断资料是否足以回答；不足则换写法重检索，最多 CRITIC_MAX_ITER 轮，全程写日志
    "critic_enabled": os.getenv("RAG_CRITIC", "1") == "1",
    "critic_max_iterations": int(os.getenv("CRITIC_MAX_ITER", "3")),
    "critic_score_high": float(os.getenv("CRITIC_SCORE_HIGH", "0.75")),  # ≥此值跳过自评（高分快速通道）
    "top_k": 5,                      # 最终喂给大模型的资料条数
    "score_threshold": 0.35,         # 旧兜底阈值（纯向量模式用）
    "chunk_size": 500,
    "chunk_overlap": 50,
    "enable_source_score": True,
    # ---- 新增：三级检索管线开关 ----
    "enable_query_rewrite": os.getenv("RAG_QUERY_REWRITE", "1") == "1",   # 多轮指代消解
    "enable_hybrid_search": os.getenv("RAG_HYBRID", "1") == "1",          # BM25+向量混合
    "enable_rerank": os.getenv("RAG_RERANK", "1") == "1",                 # 重排序精排
    "recall_k": int(os.getenv("RAG_RECALL_K", "10")),                     # 每路召回条数
    "rerank_candidates": int(os.getenv("RAG_RERANK_CANDIDATES", "6")),   # 进重排的候选数
    "rerank_score_threshold": 0.60,  # 重排sigmoid分低于此值→视为没检索到，走兜底
                                     # 实测：无关问题≈0.50(logit≈0)，相关问题≈0.67+，取gap中间
    "rerank_model": os.getenv("RERANK_MODEL", "bge-reranker-base"),
}

# ------------------------------------------------------
# 5.2 认证配置（企业化预留：默认关闭，开了才校验）
# ------------------------------------------------------
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]

# ------------------------------------------------------
# 5.3 领域专属话术（全部来自领域皮肤配置，代码里不再写死）
# ------------------------------------------------------
APP_NAME = DOMAIN_PROFILE.get("app_name", "RAG 智能助手")
DEFAULT_KB_ID = DOMAIN_PROFILE.get("default_kb_id", "default")
CUSTOM_RULES = DOMAIN_PROFILE.get("custom_rules", {})
FALLBACK_ANSWER = DOMAIN_PROFILE["fallback_answer"]
REFUSE_ANSWER = DOMAIN_PROFILE["refuse_answer"]
QUERY_REWRITE_PROMPT = DOMAIN_PROFILE.get("query_rewrite_prompt", "")

SYSTEM_PROMPT = DOMAIN_PROFILE["system_prompt"]

# 敏感词表（通用安全层，与领域无关；企业部署时可按需扩充）
SENSITIVE_WORDS = ["操你妈", "傻逼", "妈的", "废物", "脑残"]

# ------------------------------------------------------
# 6. 服务配置
# ------------------------------------------------------
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8001"))
