# 卡 #10（RAG）：同步 LLM 调用移出事件循环（安检 P3 架构）
- 背景（外部批评，两项目同犯）：`backend/routers/chat_router.py` 非流式接口 `async def` 内同步调用 `chat_single_turn`（内部 LLM 秒级+rerank 推理）——阻塞事件循环，期间全站请求（含其他用户的登录）排队。注意：**流式接口 stream_message 走 StreamingResponse+生成器，先核实其生成器是同步（starlette 会丢线程池，无害）还是 async（要单独看）**，不要想当然改。
- 任务：①grep 全部 `async def` 路由逐个判定同步重活（chat/检索/索引/评测相关），列清单；②非流式 chat 路由改 `def` 或 `await asyncio.to_thread(...)`（按改动面最小选）；③流式路径核实后在报告写清结论（无害就标注"已无害"不动）；④知识库上传/索引等可能有重 IO 的 async 路由一并判定。
- 验收（必须实际执行并贴完整输出）：
  1. 清单：每个 async 路由→有无重活→处理方式；
  2. 起自己验证实例（8002，**用户的 8001 现役实例绝不能动**）：非流式问一题 200 正常、流式问一题 SSE 正常（贴输出）；验完杀实例；
  3. L1 评测复跑一遍（`backend/scripts/evaluate_rag.py --mode hybrid --skip-llm`），Hit@5 不低于 70.6% 基线；py_compile；npm 编译不受影响可跳过（本卡后端为主）；进程清零；禁止 git 操作。
- 禁止：不动检索管线与流式协议；不加新依赖；命令逐个跑；失败 3 次停。
- 完成后输出：①清单 ②验收输出 ③三句话总结。
