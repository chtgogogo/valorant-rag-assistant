import os
import re
import yaml
from pathlib import Path
from dotenv import load_dotenv
from fastapi import HTTPException

# 项目根目录 / 后端根目录 / 数据目录（统一绝对路径，避免从不同目录启动导致数据不一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"

# 加载项目根目录下的 .env 文件
load_dotenv(PROJECT_ROOT / ".env")

# ------------------------------------------------------
# 0. 领域皮肤配置（企业化改造关键）
#    启动时加载 domain_profiles/ 下全部领域 → DOMAIN_PROFILES，
#    默认领域由 APP_DOMAIN 指定；运行时按 kb_id 取对应领域配置
#    （get_profile），实现前端一键切换、无需重启进程。
#    换新领域 = 复制任一 yaml 改内容放进该目录即可。
# ------------------------------------------------------
DOMAIN = os.getenv("APP_DOMAIN", "valorant")
_PROFILES_DIR = Path(__file__).resolve().parent / "domain_profiles"
DOMAIN_PROFILES: dict[str, dict] = {}
for _p in sorted(_PROFILES_DIR.glob("*.yaml")):
    with open(_p, "r", encoding="utf-8") as _f:
        DOMAIN_PROFILES[_p.stem] = yaml.safe_load(_f) or {}
if not DOMAIN_PROFILES:
    raise ValueError(f"❌ 领域皮肤目录为空: {_PROFILES_DIR}，至少需要一个领域配置")
if DOMAIN not in DOMAIN_PROFILES:
    raise ValueError(f"❌ APP_DOMAIN={DOMAIN} 的领域配置不存在，可用: {list(DOMAIN_PROFILES)}")

# 默认领域的配置（模块级常量保留：不传 kb_id 的旧代码路径仍指向默认领域）
DOMAIN_PROFILE = DOMAIN_PROFILES[DOMAIN]
_PROFILE_PATH = _PROFILES_DIR / f"{DOMAIN}.yaml"  # 兼容旧引用（仅路径展示用）


def get_profile(kb_id: str = None) -> dict:
    """运行时按知识库ID取领域配置（kb_id 与领域同名）。
    【v3.20】kb_id=None 回退默认领域（内部旧调用路径兼容）；显式传入但未配置的
    kb_id 抛 404——不再静默回退默认领域（静默回退会掩盖客户端传错参数）"""
    if kb_id is None:
        return DOMAIN_PROFILE
    if kb_id in DOMAIN_PROFILES:
        return DOMAIN_PROFILES[kb_id]
    raise HTTPException(
        status_code=404,
        detail=f"知识库 '{kb_id}' 不存在，可用知识库: {sorted(DOMAIN_PROFILES)}")


# ------------------------------------------------------
# 【v3.20】kb_id 白名单校验（单一事实源）：kb_id 会拼进文件/集合名
# （doc_list_{kb_id}.json、chroma collection、会话文件等），必须白名单约束。
# chat / vector / document 三个 router 与 chat_service 全部走 resolve_kb_id。
# ------------------------------------------------------
KB_ID_RE = re.compile(r"^[\w\-]{1,64}$")


def resolve_kb_id(kb_id: str | None) -> str:
    """kb_id 校验+解析唯一入口：
    - None → 默认领域（不传 kb_id 的旧调用路径兼容）
    - 格式非法（空白/超长/路径字符）→ HTTP 400
    - 格式合法但未配置 → HTTP 404（不再静默回退默认领域）
    """
    if kb_id is None:
        return DEFAULT_KB_ID
    kb = str(kb_id)
    if not KB_ID_RE.match(kb):
        raise HTTPException(status_code=400, detail="非法知识库 ID")
    if kb not in DOMAIN_PROFILES:
        raise HTTPException(
            status_code=404,
            detail=f"知识库 '{kb}' 不存在，可用知识库: {sorted(DOMAIN_PROFILES)}")
    return kb

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
    "thinking_critic": os.getenv("LLM_CRITIC_THINKING", "0") == "1",    # 质量自评：默认关（二元判断任务思考增益小，实测见 Layer3 标注集 A/B）
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
    "timeout": int(os.getenv("LLM_TIMEOUT", "30")),               # 普通调用超时
    "thinking_timeout": int(os.getenv("LLM_THINKING_TIMEOUT", "60")),  # 开思考的调用放宽（思考本身 10~30s）
    "max_retries": int(os.getenv("LLM_MAX_RETRIES", "1")),        # 【v3.11】openai 客户端自动重试次数（原默认 2）：与上层重试叠加曾把限流最坏耗时拖到 2 分钟级，收紧为 1 快速失败
    # 【v3.18】限流兜底模型：主模型限流/过载时自动切换（同账号同密钥，只换模型名）。
    # 默认 glm-4-flashx（智谱最便宜付费款，约 0.1 元/百万tokens 级，以官网为准）——
    # 兜底只在限流时触发，日常花费趋近于零；设为空串关闭兜底
    "fallback_model": os.getenv("LLM_FALLBACK_MODEL", "glm-4-flashx"),
}

# 【v3.15】密钥校验延迟化：import 本模块不再强制要求密钥——评测/CI 的 --skip-llm
# 路径全程不调大模型，不该被连坐（CI 红过的根因）；真正用大模型前由
# require_api_key() 统一把关（服务启动入口 main.py 与 llm_factory.make_llm 调用），
# 缺失时仍给出同样的中文指引。
def require_api_key() -> str:
    """返回智谱 API 密钥；缺失时抛出带配置指引的中文错误"""
    key = LLM_CONFIG["api_key"]
    if not key:
        raise ValueError("❌ 没有找到 ZHIPU_API_KEY！请在项目根目录创建 .env 文件并写入你的密钥。")
    return key

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
    # 判断资料是否足以回答；不足则换写法重检索，全程写日志。
    # 【v3.11】轮次默认 1（1=速度优先 3=质量优先）：灰区问题最多 1 次评估+1 次重检索；
    # 拒答/兜底判定在生成环节（ critic 之外），不受轮次影响
    "critic_enabled": os.getenv("RAG_CRITIC", "1") == "1",
    "critic_max_iterations": int(os.getenv("CRITIC_MAX_ROUNDS", "1")),
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
# 5.1.1 语义缓存配置（v3.19）：相似问题命中缓存直接复用答案，
#       跳过 改写→混合检索→重排→大模型生成 全程；知识库内容变更自动失效
# ------------------------------------------------------
CACHE_CONFIG = {
    "enabled": os.getenv("RAG_SEMANTIC_CACHE", "1") == "1",
    "threshold": float(os.getenv("RAG_CACHE_THRESHOLD", "0.92")),   # 余弦相似度≥此值才命中（保守，宁漏勿错）
    "ttl_minutes": int(os.getenv("RAG_CACHE_TTL_MINUTES", "1440")), # 条目有效期（默认 24h）
    "max_entries": int(os.getenv("RAG_CACHE_MAX", "512")),          # 每知识库最大缓存条数（FIFO 淘汰）
    "epoch_path": str(DATA_DIR / "kb_epoch"),                       # 知识库变更标记文件（mtime 变化即全量失效）
}

# ------------------------------------------------------
# 5.2 认证配置（企业化预留：默认关闭，开了才校验）
#     【v3.20】AUTH_ENABLED 判定收敛到 compute_auth_enabled 单点：
#     显式 AUTH_ENABLED=1 永远开启；生产模式（APP_ENV=production）强制开启
#     fail-closed——即使运维忘了设 AUTH_ENABLED，生产环境也不会裸奔。
#     生产模式 + API_KEYS 为空时 verify_api_key 对所有请求返回 401（宁全拒不裸奔）。
# ------------------------------------------------------
def compute_auth_enabled() -> bool:
    if os.getenv("AUTH_ENABLED", "0") == "1":
        return True
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


AUTH_ENABLED = compute_auth_enabled()
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]

# ------------------------------------------------------
# 5.2.1 管理端密码（v3.30）：工单/知识库/审计等管理接口要求请求头
#     X-Admin-Key 与 ADMIN_PASSWORD 一致（常量时间比较）。
#     【fail-closed】未配置 ADMIN_PASSWORD 时管理接口全部 403——
#     防止任何人打开页面就能处理工单/改知识库污染数据。
#     本地演示：在 .env 里设 ADMIN_PASSWORD=你的密码 即可。
# ------------------------------------------------------
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()

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

# ------------------------------------------------------
# 5.3.1 成本防护配置（v3.24）：主模型已切付费通道（按量计费），
#     输入限长 + IP 双层限流把"被刷烧 token"的上限锁死；
#     单次问答成本约 0.00035 元 → 默认上限 100 次/天/IP ≈ 0.035 元/天
# ------------------------------------------------------
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "100"))  # 单次提问最大字符数（v3.28：300→100，游戏问答足矣）
RATE_LIMIT_CONFIG = {
    "enabled": os.getenv("RATE_LIMIT_ENABLED", "1") == "1",
    "per_minute": int(os.getenv("RATE_LIMIT_CHAT_PER_MIN", "5")),  # 每分钟提问上限（挡刷屏脚本）
    "daily": int(os.getenv("RATE_LIMIT_CHAT_DAILY", "100")),       # 每日提问上限（挡长期薅）
}

# ------------------------------------------------------
# 5.4 工单闭环配置（v3.5：低置信兜底 → 自动建工单 → 人工答案回流知识库）
# ------------------------------------------------------
TICKET_CONFIG = {
    "enabled": os.getenv("TICKET_ENABLED", "1") == "1",   # 总开关（评测对比时可关）
    "db_path": str(DATA_DIR / "tickets.db"),
    "auto_create": os.getenv("TICKET_AUTO_CREATE", "1") == "1",  # 兜底时自动建工单
    "cooldown_minutes": int(os.getenv("TICKET_COOLDOWN", "30")),  # 同会话同问题的建单冷却（分钟），防刷单
}

# 敏感词表（通用安全层，与领域无关；企业部署时可按需扩充）
SENSITIVE_WORDS = ["操你妈", "傻逼", "妈的", "废物", "脑残"]

# ------------------------------------------------------
# 6. 服务配置
# ------------------------------------------------------
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8001"))
