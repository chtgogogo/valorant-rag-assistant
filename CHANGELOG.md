# 无畏契约 RAG 助手 · 迭代升级记录（CHANGELOG）

> 记录每个版本做了什么、解决了什么问题、用什么方法解决——完整呈现这个项目如何一步步被优化成现在的样子。
> 数字口径与指标解释见 `docs/学习-评测指标与面试追问.md`，历次评测原始结果在 `backend/eval/reports/`。从新到旧排列。

---

## v3.12（2026-09-23）· 审计接口挂鉴权 + 死代码双清（安检 L1 修复）

**做了什么**
- **审计接口补挂鉴权**（`backend/main.py`）：`/api/audit/recent` 补上 `dependencies=[Depends(verify_api_key)]`，与文档/向量/对话/工单/反馈/领域六个路由完全同款——该接口存有全部用户问答原文，此前是全项目唯一未挂鉴权的业务路由。放行逻辑（`utils/auth.py`）：`AUTH_ENABLED=0`（settings 默认值，`os.getenv("AUTH_ENABLED","0")=="1"` 才开启）时 `verify_api_key` 直接 return 放行，因此默认配置下本地行为零变化，管理页审计表不受影响；企业部署设 `AUTH_ENABLED=1` 后无/错 key 返回 401。
- **删除死函数**（`backend/services/chat_service.py`）：删掉 `test_llm_call` 整个函数（含注释头）。该函数形参是 `question`，函数体却引用未定义的 `question_for_prompt`，一调用必崩 `NameError`，属遗留死代码。主流程里的同名局部变量 `question_for_prompt`（官方别名替换结果）是正常代码，未受影响。
- **删除死代码路由**（`backend/routers/chat_router.py`）：`GET /api/chat/test_llm` 是上述死函数的唯一调用方，函数删除后留着会变成"调用即 ImportError"的暗雷，故随链一并删除；`GET /api/chat/test`（模块加载测试）保留——前端 ChatPage 挂载时调它做后端在线检测，是活代码。
- **前端核查结论（零改动）**：全量 grep 证实前端没有任何按钮或 api 函数调用"测试大模型"接口（`测试大模型`/`test-llm`/`test_llm` 在 `frontend/src` 零命中）；`api.js` 的 `testChat()` 调的是 `/chat/test`（模块加载测试，供在线状态指示），与死代码链无关，按外科修改原则不动。

**解决了什么**
- 安检 L1 两项阻断：①审计接口裸奔——问答原文接口无鉴权防护，企业部署形态下任何人可拉走全部用户问答；②死函数一调必崩且路由可达，属于随时可触发的故障点。修复后鉴权矩阵六路由+审计全覆盖，死代码链（路由→函数）清零。

**验证**
- 8002 验证实例（用户的 8001/5174 现役实例未动）：`AUTH_ENABLED=1 API_KEYS=testkey123` 启动，curl `/api/audit/recent?days=1` 无 key → `HTTP/1.1 401 Unauthorized {"detail":"API Key 无效或缺失"}`；带正确 `X-API-Key: testkey123` → `HTTP/1.1 200 OK`（26921 字节真实审计数据）。恢复默认（不设 AUTH_ENABLED）重启同端口，无 key → `HTTP_CODE=200 SIZE=26921B`、`{"code":200,...}` 正常返回——本地行为零变化实证。
- 死代码清零：`grep -n "test_llm_call\|question_for_prompt" backend/services/chat_service.py` 无函数残留，`question_for_prompt` 仅剩主流程 10 处正常变量引用；后端全局 grep `test_llm` 零代码命中。
- `py_compile` 通过（main.py / chat_service.py / chat_router.py / utils/auth.py 四文件）。
- 前端：vite 验证实例（5175 端口，现役 5174 未动）263ms ready 无报错，根路径与 api.js/AdminPage.vue/ChatPage.vue/KnowledgeManage.vue/App.vue 编译产物逐个 curl 均 HTTP 200。
- 验完 8002/5175 验证进程已全部结束、端口无残留；无 git 操作。

---

## v3.11（2026-09-23）· LLM 调用节流提速（调用次数减半 + 快速失败）

**做了什么**
- **Critic 轮次默认 3→1**（`backend/config/settings.py`）：配置项改名为 `CRITIC_MAX_ROUNDS`（原 `CRITIC_MAX_ITER`），默认 1（1=速度优先 3=质量优先，答辩对比时可调回 3）；`chat_service._critic_refine` 内部 fallback 同步为 1。拒答/兜底判定不受影响——无关问题拒答在生成环节提示词规则里、低置信兜底 `_should_fallback` 在 Critic 循环之外，轮次只控制"灰区换写法重检索"的次数上限。
- **上层重试 3 次→2 次尝试**（`backend/services/chat_service.py`）：`_call_llm_with_retry` 默认 `max_retry` 2→1（失败等固定 1.5s 重试一次，再失败立即结束；原递增退避 3s/6s）。流式正常路径零改动——生成仍走 `chain.stream`，失败由既有 v3.10 error 事件通道收尾。
- **openai 客户端自动重试 2→1**（`backend/services/llm_factory.py` + settings）：`make_llm` 给 `ChatOpenAI` 显式传 `max_retries`（新配置 `LLM_MAX_RETRIES`，默认 1）——SDK 自动重试与上层重试叠加曾把限流最坏耗时拖到 2 分钟级。两层收紧后 LLM 环节最坏尝试次数从 3×3=9 降到 2×2=4。
- `.env.example` 同步新配置名与注释。

**解决了什么**
- 不限流时同题 20.2s 基线尚可，但限流时两层重试叠加（SDK 2 次 × chat_service 3 次）× Critic 最多 3 轮重检索，最坏要等 2 分钟才见反馈；现在失败反馈缩到秒级（实测连接失败 2.3s 出 error 事件），不限流正常问答无回退。

**验证**
- 正常回归（8002 独立实例、纯默认配置）：同题「排位上分有什么技巧？」流式 `sources → done`，总耗时 30.1s、918 个 token 事件、答案 1510 字（生成约 22s + 检索/Critic 约 8s，与 20s 基线同量级；答案更长所以略高）。另一次成功样本 83.7s 中生成段实测约 20s，多出约 60s 为智谱 429 响应 Retry-After 头的 SDK 等待（限流窗口条件，非基线）。
- Critic 生效：三种场景（judge 成功/429 失败降级/连接失败降级）日志均只出现 `[Critic] 第1轮评估` 一行，无第 2/3 轮；轮次=1 时拒答兜底不受影响已从代码路径确认。
- 快速失败：.env 临时注入 `ZHIPU_BASE_URL=http://127.0.0.1:9`（复原后 md5 与原文件一致：f8840b06e46ec122fd3055adce05b88d），提问到 `event: error`（code=unavailable）总耗时 **2.3s**（要求 ≤30s），事件序列 sources→error 干净结束、无残缺答案；日志确认 judge 只尝试 2 次（第1次失败→1.5s→第2次失败，无第3次）。
- `py_compile` 通过（settings/chat_service/llm_factory）；验完 8002 实例进程已结束、端口无残留，测试会话历史已删除，8001 现役实例未受影响。

---

## v3.10（2026-09-23）· LLM 限流/故障前端友好降级（消灭无限转圈）

**做了什么**
- **后端结构化错误事件**（`backend/services/chat_service.py`）：新增 `_llm_error_event()` 把大模型异常归类为四档用户可读提示——429/账户速率限制(1302)/模型访问量过大(1305)→`rate_limited`「模型服务繁忙，请稍后再试~」、超时→`timeout`「模型响应超时」、5xx→`server_error`「模型服务开小差了」、其余含连接失败→`unavailable`「模型服务暂时不可用」；`chat_single_turn_stream` 流式生成 except 分支不再把"抱歉"文案伪装成回答 token，改为 yield `{"type":"error","code":...,"message":...}` 后直接结束流——不发残缺答案、不把失败文案写进对话历史，审计 `log_qa` 照常留痕（pipeline 标记 `+llm_error`）。SSE 沿用既有 `event: <type>\ndata: <json>` 协议风格，只新增 `error` 一种事件类型。
- **前端 error 事件处理**（`frontend/src/api.js`）：`sendMessageStream` 新增解析 `error` 事件→取消读取并抛出带 `isLlmError`/`llmMessage` 标记的错误；读循环结束后若从未收到 `done` 事件（SSE 连接中断）同样抛出带 `isStreamInterrupted` 标记的错误——两条路径都不再静默返回空答案。
- **前端错误气泡**（`frontend/src/components/ChatPage.vue`）：`handleSend` catch 分支优先识别上述两类标记错误→渲染暗色主题错误气泡（红调描边 `.msg-bubble.error`，assistant 形态）并**跳过"降级走普通接口重问"**（避免二次撞限流继续转圈）；错误气泡不参与点赞点踩（`msg.isError` 排除）；原"流式接口不存在→普通接口降级"兼容路径（`resp.ok=false`）保留不动。所有路径汇入 `finally { loading.value = false }`，loading 必停。

**解决了什么**
- 2026-09-23 09:05 实测：智谱 429（1302/1305）+500 交替时前端无限转圈——生成环节失败后只把"抱歉"文案伪装成普通回答（还会写进历史污染上下文），前端无从感知故障；现在失败即推明确错误事件，前端停止 loading 并显示友好提示，转圈问题根除。

**验证**
- 模拟故障：.env 临时注入 `ZHIPU_BASE_URL=http://127.0.0.1:9`（验完复原，diff/MD5 确认与原文件完全一致），起后端 curl SSE 问"排位上分有什么技巧？"：先 `event: sources`（top1=0.727，改写首轮跳过、Critic 正常降级放行），再 `event: error` + `{"code":"unavailable","message":"模型服务暂时不可用，请稍后再试~"}`，连接干净结束、无残缺答案；后端日志 `[ERROR] 流式大模型调用失败: Connection error.`，chat_history 无该测试会话文件（零污染）。
- 真实限流实证：回归期间智谱免费档真实返回 429+1305（"该模型当前访问量过大，请您稍后再试"），error 事件正确归类 `rate_limited`「模型服务繁忙，请稍后再试~」，与连接失败/超时文案成功区分。
- 正常回归：复原配置后同一问题流式正常（uvicorn 日志 `POST /api/chat/stream HTTP/1.1" 200 OK` + 逐 token 推送 + `done` 事件含完整答案与历史），成功请求日志无任何异常；`npm run dev` 414ms 就绪无报错，ChatPage.vue/api.js 编译产物 HTTP 200 且含新代码标记。
- `py_compile` 通过；验完 uvicorn/node 进程全部结束，8001/5174 端口无残留，测试会话历史已删除。

---

## v3.9（2026-09-23）· 工单与审计管理页（运营侧告别裸 API）+ 点踩 badcase 回流评测集

**做了什么**
- **点踩回流脚本**（`backend/scripts/export_feedback_to_eval.py` 新增）：一条命令把 `feedback.db` 中 `rating='down'` 记录追加导出为 `backend/eval/badcase_candidates.jsonl` 评测集候选（含 feedback_id / question / reference_answer「待人工复核」/ source 字段，中文可读），导出前按 feedback_id 扫描去重保证幂等；无点踩/库不存在时提示后正常退出。「用户点踩 → 评测资产候选」回流管线打通，候选经人工复核后才进正式评测集。
- **运营管理页**（`frontend/src/components/AdminPage.vue` 新增）：三个核心区块 + 一个附加块，风格完全对齐知识库管理页（暗色主题变量、卡片/表格布局、ElMessage/ElMessageBox 交互）：①工单统计卡（待处理/已解决/已关闭/已回流，调 `GET /api/ticket/stats`）；②工单列表（状态筛选 全部/待处理/已解决/已关闭，行内「解决」=弹窗填标准答案+二次确认是否回流知识库，调 `POST /api/ticket/{id}/resolve`；「关闭」确认后调 `POST /api/ticket/{id}/close`）；③最近问答审计表（7 天内时间/问题/检索管线/来源数/耗时，调 `GET /api/audit/recent`）；④附加小块：最近用户反馈列表（赞/踩标签，调 `GET /api/feedback/recent`，只读不喧宾夺主）。页面顶部注明「本地运营演示用，鉴权由后续用户体系承担」。
- **api.js 新增 6 个函数**：`getTickets / getTicketStats / resolveTicket / closeTicket / getRecentAudit / getRecentFeedback`，全部照文件既有风格封装现有后端接口，**后端零改动**。
- **入口挂载**（`App.vue`）：管理页以组件切换方式挂载（同知识库管理页先例），入口为对话页右下角悬浮「管理」小按钮（仅对话视图显示），**ChatPage.vue 零改动**。

**解决了什么**
- 运营侧此前只能拿 curl/数据库工具裸查工单和审计数据，没有可视化处理入口；现在工单解决→回流、关闭的闭环动作和审计排查全部在页面上完成，运营演示链路补齐最后一块。

**验证**
- 回流脚本五步验证：插 2 条测试点踩→导出 2 条（EXIT=0）→再插 1 条复跑仅导出新增 1 条（幂等✓）→测试数据全清理→空库复跑提示正常退出；库与 JSONL 均复原无残留。
- 起后端(8001)+前端(5174)：`AdminPage.vue` / `App.vue` 编译产物 curl 均 HTTP 200（70943 / 6775 字节，vite 无编译报错）；四个数据接口经 vite 代理逐个 curl 有真实返回：工单统计 `{total:7, open:2, resolved:5, closed:0, fed_back:5}`、工单列表/筛选正常、审计 7 天内 16 条（最新 09-17，pipeline=hybrid+rerank）、反馈空列表（空态正常）。
- 动作闭环：sqlite 造 2 条测试工单 → curl resolve（feedback=false）→ 状态 open→resolved、答案落库且未污染向量库（doc_id 空）；curl close → open→closed；测试数据已 DELETE 清理，库恢复原状（7 条：2 open / 5 resolved / 0 closed）。
- 完成后 uvicorn/node 进程已全部结束，8001/5174 端口无残留。

---

## v3.8（2026-09-23）· 用户反馈闭环最小流程（点赞点踩）

**做了什么**
- **反馈存储**（`feedback_service.py` 新增）：SQLite 单文件 `data/feedback.db`，表 `feedback(session_id, question, answer, rating up/down, created_at)`；建表逻辑放 service 模块级 init（随 router import 在启动时执行，照 `ticket_service` 先例），线程锁保护写。
- **反馈接口**（`feedback_router.py` 新增，`main.py` 注册 `/api/feedback`）：`POST /api/feedback`（body `{session_id, question, answer, rating}`，非法 rating 返回 400；**同一 question+answer 重复评价覆盖原记录**——更新 rating 与时间而非插新行）；`GET /api/feedback/recent?limit=50`（只读查询，供后续管理页/badcase 回流取数）。
- **前端评价按钮**（`ChatPage.vue` + `api.js`）：每条 assistant 消息气泡下新增 👍/👎 小按钮（样式随暗色主题，流式打字中隐藏）；点击后先本地置灰再提交（防连点），提交失败回滚可重试；**已评价后两按钮置灰防重评（组件内存状态，不做持久化）**；问题取该回复前最近一条 user 消息，问题+答案成对落库。

**解决了什么**
- 此前用户对回答满意与否没有任何表达通道，badcase 只能靠工单兜底被动发现；现在点踩数据落库，为 badcase 回流（知识库越用越厚）提供了第一手数据源。

**验证**
- `POST /api/feedback` 点踩→同问答改点赞：库里仍 **1 行且 rating=up**（覆盖不重复，action=created→updated）；`GET /api/feedback/recent` 返回该行；经 vite 代理（5174→8001）端到端 POST 亦通；测试数据已 DELETE 清空。
- 基线回归：L1 评测（hybrid 检索层）改动前后各跑一次，**Hit@5 70.6% / MRR 0.598 / 拒答 100%，两轮完全一致，零回归**（报告 report_hybrid_20260923_083432 / 084139）。
- `npm run dev` 启动无报错，ChatPage.vue / api.js 编译产物 HTTP 200（含按钮代码段）；所有改动 py_compile 通过。

---

## v3.7（2026-09-23）· 会话刷新不失忆 + CORS 白名单收敛

**做了什么**
- **历史读取接口** `GET /api/chat/history?session_id=xxx`（`chat_router.py`）：复用 `chat_service.load_history` 只读返回该会话全部消息（`[{role, content}, ...]`），不修改历史。
- **前端会话 ID 持久化**（`ChatPage.vue`）：组件加载时优先读 `localStorage('valorant_session_id')`，无则生成 `'web_' + Date.now()` 并写入——刷新页面复用同一会话；挂载时调 `getHistory()`（`api.js` 新增）拉取历史并按既有消息结构（`{id, role, content, html, sources, time}`，assistant 走 markdown 渲染）恢复到消息列表，与撤回恢复逻辑一致。
- **CORS 收敛**（`main.py`）：`allow_origins` 从 `["*"]` 收敛为 `["http://localhost:5174", "http://127.0.0.1:5174"]`（端口按 `vite.config.js` 实际 dev/preview 端口 5174 核实）；`allow_credentials=True` 保留；methods/headers 通配不变。

**解决了什么**
- 刷新页面后对话历史全丢（session_id 每次加载重新生成，且后端原本没有按会话读历史的接口）；
- CORS 全开放 + credentials=True 的组合属不安全配置（任何网站可带凭据跨域调用本 API）。

**验证**
- 后端启动正常；`curl /api/chat/history` 对无历史会话返回空列表、对已存会话能完整读回（存+读闭环演示后测试数据已清理）；
- CORS 预检：`Origin: http://localhost:5174` 返回 `access-control-allow-origin: http://localhost:5174`；陌生来源被拒（400，无 allow-origin 头）；
- `npm run dev` 正常启动（5174），ChatPage.vue / api.js 模块编译无报错。

---

## v3.6（2026-09-22）· 前端一键切换领域（多领域运行时支持）

**做了什么**
- **后端多领域运行时**：

**做了什么**
- **后端多领域运行时**：`settings.py` 启动时加载 `domain_profiles/` 下**全部**领域到 `DOMAIN_PROFILES`，新增 `get_profile(kb_id)` 运行时取配置——领域键 = 知识库 ID（valorant / ecommerce 同名），不再绑定 `APP_DOMAIN` 启动环境变量（该变量仍作为默认领域，向后兼容）。
- **chat 链路配置全部随域**：系统提示词、拒答话术、兜底话术、关键词规则、查询改写提示词改为按请求 kb_id 实时取用（`chat_single_turn` / `chat_single_turn_stream` / `_retrieve` / `_build_rag_messages` / `rewrite_query` 传递 profile）；游戏官方数据直答（英雄/武器/地图别名）仅对 valorant 域生效。
- **领域列表接口** `GET /api/domain/list`（`domain_router.py`）：返回领域键、展示名、短名、默认域、欢迎屏快捷问题（yaml 新增 `quick_questions` 字段，两域 yaml 已配 short_name）。
- **前端一键切换**（`ChatPage.vue`）：顶栏新增分段式切换按钮（点一下即换）；标题/欢迎屏文案/快捷问题随域整套变化；发送请求传当前 kb_id；切换时自动清空当前会话（跨域上下文不通用）；非游戏域侧边栏显示领域信息卡（游戏分类树为 valorant 专属）；`api.js` 新增 `getDomains()`。

**解决了什么**
- 以前换领域要改环境变量重启进程，演示"一套代码多领域"只能口头讲；现在点一下按钮，检索库、系统提示词、兜底话术、快捷问题整套切换，前端可见即可证。
- 领域配置与知识库解耦成"配置文件即领域"：新领域 = 复制一份 yaml 改内容 + 建对应知识库，前后端零代码改动。

**验证**
- 同一电商问题："七天无理由退货怎么申请？"在 ecommerce 域命中 5 条来源（首条《七天无理由退货规则.md》），在 valorant 域正确兜底（0 来源）；
- 关键词规则随域："你好"在两域返回各自定制欢迎语；
- 前端 `npm run build` 通过；所有改动 py_compile 通过。

---

## v3.5（2026-09-22）· 业务闭环：售后工单 + 知识回流 + 电商领域

**做了什么**
- **业务形态升级**：从"知识库问答"扩展为"可切换领域的智能客服系统"。新增电商售后领域（`APP_DOMAIN=ecommerce`），知识库 8 份文档 / 18 块，内容改编自公开法规（《消费者权益保护法》七天无理由、三包规定）与主流平台公开规则结构（价保、运费险、退换货流程、物流签收、优惠券、纠纷升级）。
- **工单闭环**（`ticket_service.py` + `ticket_router.py`）：低置信兜底时自动创建人工工单（工单号拼进兜底回答）；人工填标准答案 `resolve` 时一键回流知识库（问题+答案组成 FAQ 块复用 `add_chunks` 入库，文档清单同步可见可删）；防刷冷却（同会话同问题 30 分钟内不重复建单）；闭环统计接口 `/api/ticket/stats`。
- **领域化基建增强**：`init_knowledge_base.py` 支持领域参数（`python scripts/init_knowledge_base.py ecommerce`），valorant 旧布局保持兼容。
- **闭环评测**（`eval_ticket_loop.py`）：三阶段全流程——基线兜底确认 → 模拟人工回流 → 同题+改写题复测；不调 LLM 只走检索+重排，零外部依赖。

**闭环评测结果**（5 个知识库未覆盖的个案：耳机单边无声/预售定金/刹车线断裂/猫粮挑食/发票抬头错开）
- 基线：4/5 正确兜底（top1 0.502-0.588 < 阈值 0.60）；发票题 0.630 落 rerank 灰区（与纠纷文档"90天时效/人工工单"弱关联——真实业务分布，正是 Critic 灰区机制的覆盖对象）。
- 回流后：**同题二次命中 5/5 = 100%，换问法泛化命中 5/5 = 100%**（top1 提升至 0.625-0.731，全部越过阈值不再兜底）。
- 全链路冒烟：新个案"锅铲手柄歪了"→ 兜底回答自动携带工单号 `tk_…` → 工单库 open+1。

**业务意义**
- 冷启动期的"答不上"从服务缺陷变成知识资产积累通道：未解决问题全部沉淀为知识库条目，同类问题二次命中率 0% → 100%（本次口径：同题+改写）。
- 客服系统的完整闭环形态（标准问答 → 兜底 → 人工 → 回流）首次跑通，为"智能客服"叙事提供实证。

---

## v3.4（2026-09-20）· 三层评测体系 + 自审思考关

**做了什么**
- 评测升级为三层：L1 主回归集（20+8 冻结）、L2 扩充集（21 题：黑话别名 8 + 多轮指代 5 + 拒答陷阱 3 + 边界观察 2，关键词全部在知识库逐条核对）、L3 Critic 评审员标注集（10 题人工标注，专测评审判分质量）。
- 评测机制：温度固定 0（消除生成指标采样漂移）、多轮 history 字段支持（查询改写首次进入评测）、官方数据直答路径（path=official）、观察题（observe，记录行为不计分）、报告分类型统计 + P50/P95 延迟 + 设备口径。
- 官方数据评测题自动生成器 `gen_official_eval.py`（从 heroes/weapons/maps.json 产出，题与数据永同步）。
- Critic 评审员 A/B 脚本 `eval_critic_judge.py`。

**解决了什么**
- "自审思考有没有必要"的争议：A/B 实测开/关判分均 10/10，思考零增益 → 自审默认关思考（每道灰区题省约 10 秒），改写保留思考。
- 查询改写零覆盖：多轮题实测改写全部生效（如"那爆头伤害呢？"→"正义的爆头伤害是多少？"），L2 多轮 5/5 命中。
- CS2 别名陷阱（AK=狂徒）：检索层确实会误召回（skip-llm 显示结果可达阈值），但生成层提示词硬约束成功拒答——"双保险"机制首次拿到实证。
- 生成指标漂移：温度 0 后关键词覆盖稳定复现 62.5%。
- 评测脚本对官方/观察题的 KeyError 崩溃。

**怎么解决的 & 新发现（下一步输入）**
- 对照实验方法论贯穿：每类改动先小探针再全套件。
- 新发现：①玩法常识类 Hit@5 仅 1/4（最薄弱类型，下一步优化知识库或关键词）；②官方直答路径不支持"多少把/要钱吗"问法（3/6），计数与口语化问法是补齐方向；③GPU 下 MRR 有 ±0.04 运行间抖动（Hit@5 稳定），精确对比用 CPU 口径。

---

## v3.3（2026-09-20）· GPU 混合模式 + 质量自评 Critic + 向量独立开关

**做了什么**
- 重装 CUDA 版 torch（`2.12.1+cu126`），新增 `services/device_manager.py` 动态设备策略：启动时用 `torch.cuda.mem_get_info()` 检测整卡空闲显存（阈值 `GPU_MIN_FREE_MB=1536`），够就把向量 + 重排模型一起放上显卡；推理中捕获 `torch.cuda.OutOfMemoryError`，清空 CUDA 缓存后模型降级 CPU 原地重试，服务不中断。两个模型共用同一设备策略（同进同退，避免 PCIe 来回拷贝反而变慢）。
- 新增 `USE_VECTOR_RETRIEVAL` 开关：关闭后仅走 BM25 关键词检索（与 `RAG_HYBRID` 组合可凑齐纯向量/混合/纯关键词全部形态）。
- 新增质量自评 Critic：重排分数落在灰区 [0.60, `CRITIC_SCORE_HIGH`=0.75) 时，用带思考的模型评审"资料是否足以回答"；不足则换角度重写查询重检索，最多 `CRITIC_MAX_ITER=3` 轮、两轮结果择优；高分快速通道零额外延迟；每轮评审/重试全部写日志并进审计。
- LLM 实例工厂化（`services/llm_factory.py`）：最终生成 / 查询改写 / 质量自评三个实例，各自独立思考开关（`LLM_THINKING` / `LLM_REWRITE_THINKING` / `LLM_CRITIC_THINKING`，默认 开/关/关：改写开思考提精度，自审经 Layer3 标注集 A/B 实测思考零增益（两版判分均 10/10）后默认关，最终答案生成关保速度）与分层超时（普通 30s、开思考 60s）；改写实例开思考时 token 上限自动放大到 1024（踩坑：思考文本会吃掉预算，128 下 content 为空静默降级——探针实测发现）并加一次退避重试抗免费档 429。
- 评测升级：`eval_set.json` 补 17 题标准答案（数据取自官方 JSON），新增"标准答案语义相似度"指标（embedding 余弦），评测报告自动落盘 `backend/eval/reports/`。

**解决了什么**
- 纯 CPU 推理慢：单条检索约 900ms → 约 110ms（**约 8~10 倍**），主套件结果与 CPU 比特级一致、拒答 100% 保持。
- 上 GPU 后变体鲁棒性 8 题从 8/8 掉到 5/8：CPU 复跑 8/8 排除代码回归 → 关 TF32 后 7/8 → 再关 flash/sdpa 注意力内核仍 7/8 → 结论是 fp32 归约顺序的物理性微差，只剩"拼音 baotu"这一道人工压测的极端边界题在 ±1 浮动。TF32 与内核开关已写死进设备策略（确定性优先，小模型用低精度无速度收益）。
- 计划稿里两处方案修正：显存检测不能用 `memory_allocated()`（只看自己进程，测不出游戏占用），改用 `mem_get_info()`；15s 超时对开思考的调用太短（思考本身 10~30s），放宽到 60s。
- 探针抓出两个真 bug：① Critic 提示词里的 JSON 花括号被 langchain 模板误解析为变量占位符，评审调用从未真正发出（被"失败按足够处理"的兜底静默遮掩），修复 `{{}}` 转义后全链路实测通过；② 查询改写模块有独立 LLM 实例、v3.2 换模型时漏挂"关思考"包装，改写一直在静默失效退回原问题——借工厂模式统一收口。
- 关键词覆盖逐字判分导致数字随模型措辞漂移（同口径两次实测 62.5% / 53.1%），语义相似度指标更稳。

**怎么解决的**
- 标准对照实验定位 GPU 精度问题：固定代码与评测集、一次只改一个变量（CPU 复跑 → 关 TF32 → 关内核），每步跑同一份评测对照。
- 新增代码全部先探针/评测验证再合入：设备策略看启动打印、BM25-only 看日志、Critic 用人工构造的灰区用例实测全链路。

---

## v3.2（2026-09-18）· 生成模型切换 GLM-4.7-Flash（免费）

**做了什么**：默认模型 `glm-4-flash` → `glm-4.7-flash`（免费档）；重试等待从固定 1s 改递增退避（3s/6s）；README 补带生成实测口径与 429 FAQ。

**解决了什么**：智谱账号欠费导致 401 整体停服（免费模型也不可用），充值恢复后顺势升级模型；免费档高峰期 429 限流。

**怎么解决的**：踩出并修掉混合思考模型的"答案写进 `reasoning_content`、`content` 为空"问题——经 openai SDK `extra_body` 显式 `thinking: disabled`（langchain-openai 0.1.x 的 `model_kwargs` 走不到 extra_body，会被拒收）。带生成复测：拒答 100%、检索指标与纯检索模式一致。验证了 LLM 在 RAG 里可插拔。

---

## v3.1（2026-09-18）· 官方数据确定性回答 + 消融实验 + 文档整理

**做了什么**：接入官方结构化数据（`heroes/weapons/maps.json` + 别名黑话表），英雄列表/武器价格/地图类问题走确定性回答不经过 LLM；评测脚本支持 `--mode custom` 消融组合；README 全面重写（评测口径、消融表、FAQ、小白步骤、架构图、截图、版本补日期）；新增 MIT 许可证与 `.env.example`；新增学习笔记文档；新增防重复启动器（`启动-防重复.cmd` + `launch-guard.ps1`，检测 8001/5174 已在跑就不再起第二份，防多实例吃满内存）。

**解决了什么**：GitHub 上"README 宣称的功能与入库代码脱节"（35 个文件未提交，宣称功能在远端不存在）、知识库清单两处矛盾、`.env` 创建命令的引号坑、评测口径不明（分母 17 未披露、阈值自证循环风险）等一批 P0 问题。

**怎么解决的**：消融实验实跑四组管线组合，实锤**拒答收益全部来自重排序**（33%→100%）而混合检索在当前库规模零检索收益——敢承认哪段没用比吹全都有用可信；确立"检索数字必须带评测日期、改库必须重跑"的评测纪律。

---

## v3.0（2026-09-11）· 三级检索管线 + 评测体系

**做了什么**：查询改写（多轮指代消解）+ 混合检索（BM25 jieba 分词 + 向量，RRF K=60 融合）+ CrossEncoder 重排序；重排分数阈值 0.60 拒答兜底；20 题评测集 + 双模式评估脚本；SSE 流式输出。

**解决了什么**：通用 RAG 答不上时瞎编（无关问题旧阈值 0.35 拦不住）；"暴徒"等黑话精确命中失败；多轮对话"它的伤害"指代检索落空。

**怎么解决的**：两阶段架构（召回保不漏、精排保排对）参考 QAnything；拒答兜底把"答错比不答严重"变成机制保证；先建评测集再动手，每轮改动跑分验证。

---

## v2.0（2026-09-04）· 知识库管理 + 配置集中化

**做了什么**：前端知识库管理页（上传/删除/统计）、RAG 配置集中到 `config/settings.py`、来源相似度展示、知识源改为可版本管理的 `knowledge_base/`、一键重建索引脚本。

**解决了什么**：知识更新要改代码、向量库状态黑箱不可见。

---

## v1.0（2026-07-04）· 毕设初版

**做了什么**：基础 RAG 问答（FastAPI + LangChain + ChromaDB + Vue3，向量检索 + LLM 生成）。

**来源**：毕业设计，当月开源为求职作品集（`e9ae934` 补 README 与 gitignore 公开仓库）。
