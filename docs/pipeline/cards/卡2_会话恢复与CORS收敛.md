# 卡 #2：会话刷新失忆修复 + CORS 收敛
- 目标：用户刷新页面后对话历史不丢；CORS 从全开放收敛为白名单。
- 涉及文件：
  - `frontend/src/components/ChatPage.vue`（约 238 行 `const sessionId = 'web_' + Date.now()`）
  - `backend/main.py`（约 19-25 行 CORSMiddleware）
  - 可能涉及 `backend/routers/` 下 chat 相关 router（若"按 session_id 取历史"接口缺失则补）
  - 可能新建：历史恢复的后端接口实现文件
- 前置依赖：无
- 实现要点：
  1. **先核实再动手**：查 `backend/services/chat_service.py` 与 chat router，确认是否已有"按 session_id 读取历史消息"的接口；有则前端直接用，无则后端补 `GET /api/chat/history?session_id=xxx`（返回该会话消息列表，复用 chat_service 现有读取逻辑）；
  2. 前端：组件挂载时 `localStorage.getItem('valorant_session_id')`，无则生成 `'web_' + Date.now()` 并 `setItem`；刷新复用同一 ID；挂载时调历史接口拉取并渲染到消息列表（渲染前把历史消息与 SSE 消息的数据结构对齐）；
  3. CORS：`allow_origins=["*"]` + `allow_credentials=True` 改为白名单 `["http://localhost:5173","http://127.0.0.1:5173"]`（先核实 `frontend/vite.config.js` 实际 dev 端口，以核实值为准）；保留 credentials=True。
- 基线（开工前先记录）：后端可启动，`GET /docs` 返回 200；记录到卡文件底部。
- 验收（怎么算做完）：
  - 启动后端+前端，发 2 条消息 → 浏览器刷新 → 历史消息仍显示（手动验证清单逐项打勾）；
  - `grep -n '"\*"' backend/main.py` 无 CORS 相关命中；
  - 前端 dev 页面正常对话（SSE 流式不回归）。
- 禁止：不动检索管线（services/ 下 chat_service 之外的检索逻辑）；不动 ChatPage.vue 的样式与布局结构；不引入新依赖；不加"会话列表切换"等未要求功能。
- 完成后动作：贴验证输出（接口 curl 结果 + CORS grep 结果）；更新台账卡#2 行；返回 3 行以内摘要。
