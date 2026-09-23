# pytest 全局配置：让 backend 包可导入；测试全程不依赖真实密钥/网络/模型权重
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 纯函数测试不调大模型，但部分模块 import 时会读配置——给个占位 key 保证可导入
# （CI 无 .env 时靠它；本地 .env 的真 key 不会被覆盖：load_dotenv 不覆盖已有环境变量）
os.environ.setdefault("ZHIPU_API_KEY", "test-key-not-real")
