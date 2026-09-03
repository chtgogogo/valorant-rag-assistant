import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件（自动往上找两级目录，因为 settings.py 在 backend/config 里）
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

# ------------------------------------------------------
# 1. 大模型配置（从 .env 读取密钥，绝不硬编码）
# ------------------------------------------------------
LLM_CONFIG = {
    "api_key": os.getenv("ZHIPU_API_KEY"),
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model_name": "glm-4-flash",
    "temperature": 0.3,
    "max_tokens": 2048  #
}

# 检查一下有没有读到密钥（如果没读到，程序直接报错提醒你）
if not LLM_CONFIG["api_key"]:
    raise ValueError("❌ 没有找到 ZHIPU_API_KEY！请在项目根目录创建 .env 文件并写入你的密钥。")

# ------------------------------------------------------
# 2. Embedding 模型配置（本地跑的，不需要密钥）
# ------------------------------------------------------
EMBEDDING_CONFIG = {
    "model_name": "bge-small-zh-v1.5",
    "device": "cpu"
}

# ------------------------------------------------------
# 3. 向量库配置
# ------------------------------------------------------
VECTOR_DB_PATH = "./data/chroma_db"

# ------------------------------------------------------
# 4. 文件上传配置
# ------------------------------------------------------
UPLOAD_PATH = "./data/uploads"
ALLOWED_EXTENSIONS = ["docx", "pdf", "txt", "md"]
MAX_FILE_SIZE = 10 * 1024 * 1024

# ------------------------------------------------------
# 5. 对话与兜底配置
# ------------------------------------------------------
CHAT_CONFIG = {
    "max_history_turns": 20,
    "similarity_threshold": 0.35,
    "history_path": "./data/chat_history"
}

SENSITIVE_WORDS = ["操你妈", "傻逼"]
FALLBACK_ANSWER = "抱歉，我在无畏契约的知识库里没有找到相关答案，要不你换个问法试试？"
REFUSE_ANSWER = "我只解答无畏契约的游戏问题哦，其他问题我暂时不会~"

SYSTEM_PROMPT = """你是顶级的无畏契约（VALORANT）战术大师，性格沉稳冷静。
回答必须严格基于下面提供的【参考资料】，严禁编造任何技能伤害数值、地图点位或枪械数据。
如果参考资料里没有，直接说"资料没提到"。
回答格式要求：
1. 使用 Markdown 格式，重点数据（如伤害数值）用**加粗**。
2. 结构清晰，善用换行。
3. 语气像高玩给新手指导，专业且友好。
"""

# ------------------------------------------------------
# 6. 服务配置
# ------------------------------------------------------
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000