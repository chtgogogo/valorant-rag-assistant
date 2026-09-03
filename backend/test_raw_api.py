import requests
import os
import json
from config.settings import LLM_CONFIG, SYSTEM_PROMPT# 先导入你项目里的系统提示词
from onnxruntime.capi.onnxruntime_pybind11_state import ModelLoaded

LLM_CONFIG = {
    "api_key": os.getenv("ZHIPU_API_KEY"),
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model_name": "glm-4-flash",
    "temperature": 0.3,
    "max_tokens": 2048  #
}
#第三步
#这里我查阅了一下工具手册看字典的语法，但是我不知道你的规定咋做，于是问ai：我项目里的 SYSTEM_PROMPT 内容 是什么在哪儿
# messages = {键1:值1,键2:值2}
#AI给我代码：
# 手动构建 messages 字典列表
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "介绍下无畏契约的武器系统"}
]
#我问：字典不是messages = {键1:值1,键2:值2}吗？role，content又是什么，是键吗？为什么"system""user""介绍下无畏契约的武器系统"有引号SYSTEM_PROMPT没有
#原来格式是智谱 / OpenAI 官方 API 规定的标准格式，role，content我也懂了，有引号这代表 字符串字面量，没有就是变量
#第四步，我问：请求体 body = {...}是什么，一般怎么完整构建？

# 1. 定义基础参数（从配置文件拿）
API_KEY = LLM_CONFIG["api_key"]
BASE_URL = LLM_CONFIG["base_url"]
MODEL = LLM_CONFIG["model_name"]


#剩下的太复杂了，我都看不懂理解不了，复制粘贴了，我不知道body是啥，语法规范是啥，底层逻辑是啥，搜了也感觉有点复杂：是什么：发给智谱 API 的核心 JSON 参数包，定义了模型、对话内容和生成参数。
#一般怎么完整构建：把 model、messages、temperature、max_tokens、stream 等键值对放进一个 Python 字典。在你的项目里，LangChain 已经替你把这个“构建”动作完全封装好了，你不需要手动写 body，只需要配置好 LLM_CONFIG 并定义好 prompt 即可。
# 3. 完整构建请求体（把模型、消息、参数全塞进一个字典）
body = {
    "model": MODEL,
    "messages": messages,
    "temperature": LLM_CONFIG["temperature"],   # 从配置取
    "max_tokens": LLM_CONFIG["max_tokens"],     # 从配置取
    "stream": False
}

# 4. 发送请求
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}
response = requests.post(
    f"{BASE_URL}/chat/completions",  # 具体 URL 地址
    headers=headers,
    json=body  # 这里 requests 会自动帮你把字典转成 JSON 字符串
)

# 5. 拿结果
result = response.json()
print(result["choices"][0]["message"]["content"])