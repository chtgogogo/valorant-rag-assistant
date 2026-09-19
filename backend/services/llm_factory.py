# 【新增 v3.3】LLM 实例工厂：按环节生成带独立思考开关的 ChatOpenAI 实例
# ------------------------------------------------------------
# 为什么需要工厂：glm-4.7 系列是混合思考模型，不显式关思考时答案会写进
# reasoning_content、content 为空（界面上就是空白回答）。
# langchain-openai 0.1.x 的 model_kwargs 走不到 openai SDK 的 extra_body
# （会被 create() 当未知参数拒收），所以包装 client.create 注入 extra_body。
# 三个环节各自实例：最终答案生成 / 查询改写 / 质量自评（Critic），
# 思考开关与超时分层配置（见 settings.LLM_CONFIG）。
# ------------------------------------------------------------
from langchain_openai import ChatOpenAI
from config.settings import LLM_CONFIG

import logging
logger = logging.getLogger(__name__)


def make_llm(thinking: bool, timeout: int,
             temperature: float = None, max_tokens: int = None) -> ChatOpenAI:
    """创建一个带思考开关的 ChatOpenAI 实例
    :param thinking: True=开启混合思考（答案前先推理，慢但精度高）；False=显式关闭（快）
    :param timeout: 该实例的调用超时秒数（开思考的调用建议用 thinking_timeout）
    """
    inst = ChatOpenAI(
        api_key=LLM_CONFIG["api_key"],
        base_url=LLM_CONFIG["base_url"],
        model=LLM_CONFIG["model_name"],
        temperature=LLM_CONFIG["temperature"] if temperature is None else temperature,
        max_tokens=LLM_CONFIG["max_tokens"] if max_tokens is None else max_tokens,
        timeout=timeout,
    )
    thinking_type = "enabled" if thinking else "disabled"
    _original_create = inst.client.create

    def _create_with_thinking(*args, **kwargs):
        kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}),
                                "thinking": {"type": thinking_type}}
        return _original_create(*args, **kwargs)

    inst.client.create = _create_with_thinking
    logger.info("LLM 实例创建: model=%s thinking=%s timeout=%ss",
                LLM_CONFIG["model_name"], thinking_type, timeout)
    return inst
