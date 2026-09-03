import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录 / 后端根目录 / 数据目录（统一绝对路径，避免从不同目录启动导致数据不一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"

# 加载项目根目录下的 .env 文件
load_dotenv(PROJECT_ROOT / ".env")

# ------------------------------------------------------
# 1. 大模型配置（从 .env 读取密钥，绝不硬编码）
# ------------------------------------------------------
LLM_CONFIG = {
    "api_key": os.getenv("ZHIPU_API_KEY"),
    "base_url": os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "model_name": os.getenv("ZHIPU_MODEL", "glm-4-flash"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
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
# 5.1 RAG 检索配置
# ------------------------------------------------------
RAG_CONFIG = {
    "top_k": 5,
    "score_threshold": 0.35,
    "chunk_size": 500,
    "chunk_overlap": 50,
    "enable_source_score": True
}

SENSITIVE_WORDS = ["操你妈", "傻逼", "妈的", "废物", "脑残"]
FALLBACK_ANSWER = "抱歉，我在无畏契约的知识库里没有找到足够相关的资料。你可以换个问法，或到“知识库管理”页上传更多攻略文档。"
REFUSE_ANSWER = "我只解答无畏契约的游戏问题哦，其他问题我暂时不会~"

SYSTEM_PROMPT = """你是顶级的无畏契约（VALORANT）战术大师，性格沉稳冷静。
回答必须严格基于下面提供的【参考资料】，严禁编造任何技能伤害数值、地图点位或枪械数据。
如果参考资料里没有，直接说"资料没提到"，并可以建议用户补充知识库。
回答格式要求：
1. 使用 Markdown 格式，重点数据（如伤害数值）用**加粗**。
2. 结构清晰，善用换行、列表和小标题。
3. 语气像高玩给新手指导，专业且友好。
4. 涉及“去哪学”时，优先推荐官方资料，再提社区教程。
"""

# ------------------------------------------------------
# 6. 服务配置
# ------------------------------------------------------
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
