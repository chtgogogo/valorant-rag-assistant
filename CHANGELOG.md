# 无畏契约 RAG 助手 · 迭代升级记录（CHANGELOG）

> 记录每个版本做了什么、解决了什么问题、用什么方法解决——完整呈现这个项目如何一步步被优化成现在的样子。
> 数字口径与指标解释见 `docs/学习-评测指标与面试追问.md`，历次评测原始结果在 `backend/eval/reports/`。从新到旧排列。

---

## v3.23（2026-09-25）· 评测体系升级：全量跑全 + 冻结 holdout + 拒答/误杀 16 题 + 多轮消融 + 空来源拦截

**优化了哪些地方**
1. **全量评测总入口**（`scripts/run_full_eval.py`）：主集/变体/L2/官方四集（合计 52 题）
   一次跑全，skip-llm 与带生成各一轮；每集独立子进程（评测开关互不污染），失败自动重试
   一次并在总报告如实标注。首次实现"全集一次跑完"（历史单次最多 20 条）。
2. **冻结 holdout**（`scripts/split_holdout.py`，seed=42 写死）：主集+变体+L2 去重 1 题
   后 45 题按 7:3 切成 `eval_train.json`（31 题，调参可看）与 `eval_holdout.json`
   （14 题，对外数字只认这份）。切分按题目文本哈希排序后由种子打乱——任何重跑得到
   完全相同的切分，杜绝"挑集"。**调参规则：调参只准看 train；对外汇报只认 holdout。**
3. **拒答+误杀双向量**（`eval/eval_set_refusal.json` 16 题 + `scripts/eval_refusal.py`）：
   8 题"应拒"（其他游戏/超时间范围/需实时数据/库内无数据）+ 8 题"必答"（黑话/改写/
   综合问法，出题时逐题在知识库检索自查依据）。实测（skip-llm 与带生成口径一致）：
   **拒答正确率 6/8=75%，误杀率 0/8=0%**。两道漏拒题（X02 实时比分/X03 胜率榜）是
   设计上的真实难点：rerank 分恰好过阈值（0.618/0.615 vs 阈值 0.60）放行生成，LLM
   实际输出"资料没提到…"引导（未编造），但未走标准兜底通道——如实计入漏拒，作为
   train 集调参的明确靶子。
4. **多轮消融裁决**：L2 多轮指代 5 题固化子集，开/关查询改写各跑一次（skip-llm）——
   两种口径都是 **Hit@5 = 4/5，但命中的题不同**：开改写救回 M02（"那爆头伤害呢"）却把
   M05（"那正面刚枪呢"）带偏到蜂刺。历史"5/5 与改写测不出效果"的矛盾解释：skip-llm
   检索口径下改写净收益为 0，但内部一正一负；带生成口径另有答案质量加成。两份报告落盘。
5. **空来源拦截（生成侧）**：最终 answer 非空但 sources 意外为空时不再直接输出——
   非流式转低置信兜底（拒答话术+工单）并记 `error_class=empty_sources`；流式在生成前
   拦截（不调 LLM 直接转兜底）。官方直答/关键词规则/敏感词/缓存命中路径天然白名单
   （提前 return）。3 条测试覆盖。
6. **评测脚本口径修复**：evaluate_rag/eval_refusal 的拒答题带生成分支补上
   `_should_fallback` 兜底判定（原实现"分数不够也硬生成"会虚增漏拒）；官方直答集单独跑
   的两处除零崩溃（无检索题/无关键词题显示 N/A）；官方直答服务匹配词表补
   "多少个英雄/多少把武器/要钱"（官方集 3/6→6/6）。
7. **工单闭环报告落盘**（`scripts/eval_ticket_loop.py`）：基线/复测逐题数据写入
   `reports/report_ticket_loop_*.md`。重跑实测：同题二次命中 5/5、改写泛化 5/5；
   基线阶段发现历史回流文档残留（个案题已能直接命中），已在报告中如实记录。

**新增了什么**
- `eval_train.json` / `eval_holdout.json`（冻结切分）、`eval_set_refusal.json`（16 题）、
  `eval_set_l2_multiturn.json`（消融子集）。
- `scripts/run_full_eval.py`、`scripts/split_holdout.py`、`scripts/eval_refusal.py`。
- 新增 `backend/tests/test_empty_sources.py` 3 条空来源拦截测试。

**解决了什么问题**
- 62 条评测题从未一次跑全、无冻结盲集（全题参与过调参）——现在四集全量可一键跑，
  holdout 14 题永久冻结（基线首跑：Hit@5=9/12=75.0%，MRR=0.611，报告
  `report_hybrid_20260925_055*`）。
- 拒答能力从未量化、误杀率从未测过——现在双向都有数字与逐题报告可复核。
- 多轮"查询改写有没有用"只有矛盾结论没有数字——现在有同卷消融报告（4/5 vs 4/5，
  题级结构差异明确）。
- 31% 历史审计 sources 为空且答案照常输出——生成侧兜底拦截上线，同类情况不再裸奔。

---

## v3.22（2026-09-25）· 可观测性：Token 用量 + 失败分类 + 审计哈希链 + 汇总报表 + 首字延迟

**优化了哪些地方**
1. **Token 用量全链路记账**（`utils/audit.py` + `chat_service` + `query_rewriter`）：
   新增问答级 token 账本，一次问答内改写/评估/生成多次调用分别记账再汇总；智谱返回的
   真实 usage（prompt/completion/total）优先——非流式 invoke 与流式最后分片都能取到时
   记真实值；取不到（兼容端点不回 usage）按实际渲染提示词与答案长度估算，并把
   `estimated=True` 如实标注。审计 `token_usage` 含总和 + `by_stage` 分环节。
2. **失败分类**：审计新增 `error_class` 字段，枚举 rate_limit / timeout / empty_answer /
   generation_error / refusal / none——低置信兜底与敏感词拦截标 refusal，限流/超时/生成失败
   各归各类，正常为 none。旧记录无该字段读作 none，完全向后兼容。
3. **检索可观测**：审计新增 `rerank_top_score`（top1 重排分数）与 `retrieved_count`
   （最终召回块数），缓存命中/官方直答等无检索路径自然留空。
4. **审计哈希链**：每条记录追加 `prev_hash`（上一条 self_hash）与 `self_hash`
   （本条内容+prev 的 SHA-256 链式计算）；`verify_chain()` 读取时校验断链/篡改即告警；
   进程重启自动从文件尾续链；旧格式记录自动跳过校验、新记录重新起链——只加字段，
   旧文件照常读。
5. **bench_latency 首字延迟**：新增 TTFB 测量（POST /api/chat/stream 到首个 token 事件），
   使用同义变体问题避开前两轮语义缓存（否则测出的是缓存命中延迟而非生成首字），
   报告单列 TTFB P50/P95 与逐题数值。

**新增了什么**
- `scripts/audit_report.py` 审计汇总报表：总请求数 / 拒答数与拒答率 / error_class 分布 /
  P50 与 P95 延迟 / token 总量与每轮均值 / 按日期分列 / 哈希链校验，终端打印 + 落盘
  `eval/reports/audit_report_*.md`；支持 `--days` / `--file` 过滤。
- 新增 `backend/tests/test_observability.py` 13 条测试：审计新字段、哈希链（衔接/篡改检出/
  旧格式兼容/重启续链）、记账（真实 vs 估算）、报表统计（假 jsonl 驱动）。

**解决了什么问题**
- 每轮问答花多少 token 不可知——现在真实/估算口径分明，改写+生成分项可查
  （实测样例：critic 895 + rewrite 107 = 1002 tokens/轮，estimated=False 智谱真实返回）。
- 审计只有耗时没有失败原因，出问题只能翻服务日志——现在 failure 分类可直接 SQL 式统计。
- 审计文件无法证明"没被改过"——哈希链让篡改可检出。
- 首字延迟从未测过——TTFB 首次落盘（2026-09-25 CPU 口径实测 P50≈1.7s / P95≈3.9s，
  报告 `bench_v322_20260925_053700.md`）。
- 审计数据没有汇总视图——`audit_report.py` 一条命令出全量报表（当前 78 条历史记录
  已出首份报表落盘）。

---

## v3.21（2026-09-25）· 索引新鲜度与版本管理：BM25 指纹缓存键 + doc_list 原子写

**优化了哪些地方**
1. **BM25 缓存键换内容指纹**（`services/hybrid_retriever.py`）：原缓存键用文档块条数
   count——删 1 篇加 1 篇 count 不变，索引永久陈旧（删除的内容仍能被检索到、新内容永远
   搜不到）。现改为：每次取索引时拉该库全量块的 id+内容，按 id 排序后整体算 SHA-256
   指纹，指纹一致才复用缓存；任何增/删/换内容都会改变指纹并触发重建。代码里那条
   "count 变化会自动重建"的失实注释一并更正为真实机制。
2. **重建锁 per-kb 化**：原重建持全局锁，一个知识库重建阻塞所有知识库检索；现改为
   线程锁字典按 kb_id 分锁（仿 chat_service v3.17 per-session 锁），重建期间其他库不受影响。
3. **doc_list 注册表原子写**（`services/document_service.py`）：写临时文件 + `os.replace`
   （含 fsync），写一半崩溃不再留下损坏的半截 JSON。
4. **注册表并发保护**：新增 `_append_doc_record`——上传追加记录的 load→append→save
   全程持 per-kb 文件锁，并发上传不再互相覆盖丢记录；`delete_document` 的读改写同样入锁。
5. **损坏自愈留痕**：读注册表区分"文件不存在（返回空，正常）"与"文件损坏（error 日志
   + 备份为 `*.corrupt-时间戳` 后自愈为空）"——旧行为 except 静默返回空列表，知识库
   会"凭空消失"且日志里毫无痕迹。
6. **文档版本元数据**：同名文档重复上传 version 递增（记录与块 metadata 均携带），
   每条记录带 updated_at；`/api/document/list` 自动返回两字段；BM25/向量检索结果
   （`SearchResult.version`）与回答 sources 均透传 version。

**新增了什么**
- `SearchResult.version` 字段（旧知识库数据无 version 时为 None，sources 中省略，向后兼容）。
- 新增 `backend/tests/test_index_freshness.py` 6 条回归测试：核心场景"上传A→删A→上传B
  （count 不变）断言 BM25 看到 B 而非 A"、损坏注册表留痕自愈、并发追加零丢失、
  同名文档版本递增、检索链路 version 透传。chroma 全程用临时目录 + 确定性假 embedding，
  不污染真实知识库、不加载真模型。

**解决了什么问题**
- 知识库"删一篇加一篇"后 BM25 索引永久陈旧——检索结果与真实知识库内容脱节且无法自愈
  （本次以同 count 替换场景写出可复现的失败测试后修复，红→绿）。
- 并发上传互覆盖丢文档记录、写一半崩溃留下损坏注册表、损坏被静默吞成"空知识库"——
  注册表三防（原子写/文件锁/损坏留痕）全部闭合。
- 一个知识库重建索引卡住所有知识库——锁粒度降到 per-kb。
- 无法回答"这个答案引的是文档哪个版本"——version 全链路可追溯。

### 附：增量更新还会遇到的坑（排查指引）

1. **向量库与 BM25 双索引同步**：上传/删除只经 `document_service` 走，两边才会同时更新；
   若绕过接口直接操作 chroma（如手工调试脚本删块），BM25 指纹会变但向量库已不一致——
   一切变更必须走 `upload_and_process` / `delete_document`（或重新索引脚本），不要旁路。
2. **换 embedding 模型必须全量重建**：指纹只覆盖"块 id+内容"，不包含 embedding 模型——
   换模型后旧向量与新查询不在同一空间，语义检索会静默劣化。换 `EMBEDDING_MODEL` 后
   必须跑重新索引脚本全量重建（重新索引.bat / init_knowledge_base.py）。
3. **同名覆盖语义**：本项目的"同名文档重复上传"是**并存两个版本**（version 递增），
   不是替换——旧版本仍会被检索到。要"替换"语义需先删旧版再传新版（后续如需一键替换
   再做，当前保持极简）。
4. **半写状态与原子写**：任何 JSON/JSONL 状态文件（doc_list、审计、缓存 epoch）都应
   遵循"临时文件 + os.replace"模式；直接 `open("w")` 的写入在崩溃/断电时会留下半截文件，
   读方还必须配合"损坏留痕"而非静默当空。
5. **BM25 语料过小的边界**：语料块数 <4 时 BM25Okapi 的 IDF 会为 0/负，分数≤0 的块
   会被过滤（表现为"小知识库 BM25 搜不到东西"）——属 rank_bm25 的数学特性，生产
   知识库几百块不受影响；做小样演示时优先走向量检索。

---

## v3.20（2026-09-25）· 安全漏洞清零：session_id/kb_id 白名单 + 鉴权 fail-closed + SSE 断连兜底

**优化了哪些地方**
1. **session_id 白名单**（`services/chat_service.py`）：`_get_history_path` 入口统一校验
   （规则与 kb_id 白名单同款：字母/数字/下划线/连字符，1~64 位），非法值抛 HTTP 400；
   读历史/清空/回滚三个入口全部被覆盖（都经过该函数）；`schemas/models.py` 的
   `ChatRequest.session_id` 加 pydantic `pattern` 约束做双保险（模型层 422 拒绝）。
2. **kb_id 全链路白名单**：校验正则收敛到 `config.settings.KB_ID_RE` 单一事实源；
   新增 `resolve_kb_id()` 唯一入口——格式非法 → 400，格式合法但未配置 → 404。
   `chat_router`（send/stream/warmup）与 `vector_router`（add_chunks/search/stats/delete_doc）
   全部接入；`get_profile` 未知 kb_id 改抛 404；`chat_service` 两条问答链路入口同步接入，
   残留的 `kb_id or DEFAULT_KB_ID` 静默回退移除。
3. **鉴权加固**（`utils/auth.py`）：key 比较从普通字符串 `in` 改为 `hmac.compare_digest`
   常量时间比较（防时序侧信道逐位猜 key）；`AUTH_ENABLED` 判定收敛到
   `settings.compute_auth_enabled()` 单点——生产模式（`APP_ENV=production`）强制开启
   fail-closed，认证开启但 `API_KEYS` 未配置时所有请求 401（宁全拒不裸奔）；
   本地开发默认体验不变；`.env.example` 补注释说明。
4. **SSE 断连兜底**（`chat_single_turn_stream`）：生成器整体包 try/finally——客户端中途
   断开（GeneratorExit）时，已产出的答案仍补保存历史 + 写审计（pipeline 标 `+disconnect`）；
   敏感词/关键词规则/官方直答/缓存命中/正常完成各路径以 finalized 标志防重复落账；
   LLM 失败路径维持 v3.10 行为（不发残缺答案、不写历史）；断连不刷错误日志。
5. **加载期 OOM 降级修复**（`services/device_manager.py`）：`degrade_to_cpu` 判 None——
   加载期（模型实例还没建出来）触发 OOM 时 `vector_service`/`reranker` 传的是 None，
   旧代码直接 AttributeError 导致降级失效。
6. **密钥字样彻底清除**：全仓跟踪文件不再残留旧密钥前缀字样（历史勘误行已改写）；
   `.env` 不入库不用管（密钥早已轮换）。

**新增了什么**
- 新增 `backend/tests/test_security_v320.py` 安全回归测试 27 条：session_id 穿越矩阵
  （`../`/`..\`/空/超长/None + 三入口拦截 + 正常 id 不受影响）、kb_id 非法/未知矩阵、
  鉴权常量时间比较与生产强制开启、SSE 断连兜底（断连补保存 + 无答案不保存 + 正常完成不重复落账）、
  degrade_to_cpu(None) 判空。

**解决了什么问题**
- `session_id=../../x` 可读/写/清任意 json 文件、可枚举 web_时间戳越权读他人对话（L1 级漏洞闭合）。
- chat/vector 链路传未知 kb_id 静默回退默认领域——用户问 A 库答 B 库还毫无感知；现在明确 404。
- 鉴权开启时普通字符串比较可被时序攻击逐位猜 key；生产环境忘开 AUTH_ENABLED 会裸奔——现已强制开启。
- 客户端中途断开 SSE 时，保存历史/写审计/低置信建单全部不执行——断开也留痕，审计不再漏记。
- 加载期 OOM 降级因 None 崩溃而失效——现在安全跳过，加载失败按原有异常路径正常暴露。

---

## v3.19（2026-09-24）· 语义缓存 + 付费主模型：命中毫秒级返回

**做了什么**
1. **语义缓存**（`services/semantic_cache.py`）：问题向量与缓存条目算余弦相似度，≥阈值
   （默认 0.92，宁漏勿错）直接复用答案与来源，跳过 改写→混合检索→重排→大模型生成 全程；
   流式/非流式两条链路都接入；命中在审计 pipeline 记 `+cache_hit`，与 `+fallback` 同款可查。
2. **只缓存"带来源的成功回答"**：低置信兜底（来源为空）与主模型+兜底全部失败（本次为
   `_call_llm_with_retry` 补了全失败标记）的回答一律不入缓存——缓存里永远是有依据的答案。
3. **失效双保险**：上传/删除文档、离线重建索引脚本都 touch `backend/data/kb_epoch` 标记
   文件，缓存发现 mtime 变化即全量清空（离线脚本是独立进程，文件信号是唯一可靠的跨进程
   通道）；外加 TTL（默认 24h）与每知识库条数上限（FIFO 淘汰）。
4. **配置化**：`RAG_SEMANTIC_CACHE` 总开关 / `RAG_CACHE_THRESHOLD` / `RAG_CACHE_TTL_MINUTES`
   / `RAG_CACHE_MAX`；embedding 复用共享 bge-small-zh 实例（懒加载，不增启动负担）。
5. **主模型切换付费通道**：`.env` 设 `ZHIPU_MODEL=glm-4-flashx`、`LLM_FALLBACK_MODEL=glm-4.7-flash`
   （免费档降为限流兜底）、`LLM_REWRITE_THINKING=0`（flashx 非混合思考系列）；
   新增 `scripts/bench_latency.py` 延迟基准脚本（miss/hit 同卷对比，报告落盘 eval/reports/）。

**解决了什么问题**
- 免费档晚高峰限流把响应拖到几十秒。同卷实测（2026-09-24 晚，8 题，RTX 3060 GPU 检索）：
  - 免费主模型 glm-4.7-flash：miss **P50 = 70.7s**（P95 = 118.8s，限流退避叠加）；
  - 付费主模型 glm-4-flashx：miss **P50 = 5.7s**；
  - 语义缓存命中：**P50 = 9.8ms**（付费）/ 8.8ms（免费）——命中比未命中快 3 个数量级，
    且两轮答案逐字一致（8/8），重复/相似提问不再重复等待。

**怎么验证的**
- 单测新增 14 例（命中 / 阈值边界 / 知识库隔离 / 空回答不缓存 / TTL / epoch 失效 /
  FIFO 淘汰 / 统计 / 并发烟测 / 阈值可调），全量 **91/91 绿**。
- 基准报告：`eval/reports/bench_paid_20260924_193249.md`、`bench_free_20260924_194353.md`
  （样本量 8，量级参考；精确缓存统计看审计 `+cache_hit`）。
- 已知边界：缓存键不含对话历史——多轮追问（"那它呢"）语义不匹配会正常走完整管线，
  不会被错误命中；知识库任何变更经 epoch 信号即时失效。

---

## v3.18（2026-09-24）· 限流兜底：主模型限流自动切换备用模型

**做了什么**
1. **限流兜底（failover）**：主模型 glm-4.7-flash 重试耗尽且错误属**限流/模型过载**类
   （429/1302/1305/5xx）时，自动切换 `LLM_FALLBACK_MODEL`（默认 glm-4-flashx，智谱最便宜
   付费款，约 0.1 元/百万tokens 级）再试——同账号同密钥只换模型名，用户端无感；
   连接失败/超时换模型无意义，不触发兜底；兜底也失败则维持既有友好文案。
2. **覆盖两条链路**：非流式（`_call_llm_with_retry` 加 fallback 分支 + `fallback_used`
   标记回收）；流式（**仅当尚未产出任何 token** 时切备用模型重新流式生成——已出部分
   token 时换模型续写会前后不一致，维持 error 事件收尾）。
3. **审计打标**：兜底接管成功的问答在审计 pipeline 记 `+fallback`，哪次切换、什么原因全部可查。
4. `make_llm` 支持 `model_name` 覆盖；兜底实例懒加载（同 v3.15 模式）；`.env.example`
   文档化 `LLM_FALLBACK_MODEL`（设空串关闭兜底）。

**解决了什么问题**
- 免费档高峰期 429 的用户体验从"看到繁忙提示需要重问"变成"无感切换继续问答"；
  答辩演示时不会撞限流翻车。选择同平台付费款而非接入第二家：零新注册（账号已有余额）、
  兜底只在限流瞬间触发，日常花费趋近于零。

**怎么验证的**
- glm-4-flashx 实测可用（thinking disabled 包装兼容，真实请求返回正常）。
- 单测 4 例（mock 主模型）：限流→兜底接管并打标 ✓；连接错误→不切兜底 ✓；空串配置→
  不切 ✓；兜底也失败→友好文案 ✓。全量 **77/77 绿**（2.71s）。
- 8002 冒烟：关键词问答与真实检索问答 HTTP 200、答案正常（正常路径零影响）；
  验完进程结束、测试会话已删除。
- 真实限流场景的端到端触发留待自然发生：审计 `+fallback` 标记与日志可观测。

---

## v3.17（2026-09-24）· 忠实度评测上线 + 历史锁/冷启动剔除等工程收尾

**做了什么**
1. **答案忠实度（faithfulness）评测**（`evaluate_rag.py`，简化版 RAGAS）：judge 模型对照
   检索来源逐条核对答案声明（允许同义转述，不允许来源里没有的事实/数字），二元判定 +
   列出无依据声明；判分失败（LLM 异常/输出不可解析）3 次尝试后退避放弃，**不计入分母**
   并在报告单列；带生成评测自动产出，报告新增"忠实"明细列。judge 实例懒加载（同 v3.15 模式）。
2. **评测延迟剔除冷启动**：评测循环前预热一次检索（不计入统计）——首条用例的耗时含模型
   加载（实测 22.5s），此前会把 P95 污染到 34s 量级。
3. **会话历史 per-session 锁**：新增 `append_history()`（load→append→save 在同一把锁内），
   替换 6 处裸"读改写"；`rollback`/`clear` 同锁——send 路由同步 def 走线程池，同会话并发
   请求此前会互相覆盖丢消息。
4. **三路由鉴权双重挂载去重**：domain/feedback/ticket 的 router 内部 `Depends(verify_api_key)`
   删除（main.py include_router 统一挂载保留），鉴权矩阵行为不变、单一事实源。
5. **`hybrid_search` 默认 kb_id 收口**：默认参数从硬编码 `"valorant"` 改为跟随
   `DEFAULT_KB_ID`（多领域运行时一致）。
6. **评测脚本可测试性**：模块级 `parse_args`/环境变量预设拆入 `_apply_mode_env(args)` 与
   `__main__` 块，`main(args)` 参数化——评测纯函数（judge_refusal/parse_faithfulness）
   可被 pytest 导入；新增 11 用例（解析 4 + 拒答判定 3 + 历史锁并发 1 + 原有对齐），
   总数 **73**。

**解决了什么问题**
- README"已知局限"中"尚未建立忠实度自动评测"一项闭环；延迟统计不再被冷启动污染
  （P95 34116ms → 136ms）；并发丢历史与双重挂载两类工程债清偿。

**怎么验证的**
- pytest 73/73 全绿（2.24s）；评测命令行行为零回归（--skip-llm 主集 12/17=70.6% /
  MRR 0.598 / 拒答 3/3 逐字一致），延迟 P50 116ms / P95 136ms（预热 22.5s 剔除实证）。
- 历史锁：并发单测 10 线程×5 轮 100 条无丢失；8002 冒烟两轮问答 200、历史 4 条成对完整。
- 忠实度判分链路实测：早高峰实测暴露判分 prompt 的 JSON 花括号未转义（v3.3/v3.16 同款
  坑第三次出现，容错机制拦住未崩评测）→ 双花括号转义修复；限流重试退避加码（3 次尝试
  3s/6s）。**本轮实测（08:40 高峰）忠实度 3/6=50%、判分失败 11 题不计入，关键词覆盖
  28.1%/拒答 0/3 均为限流失真样本（对照 v3.2 的 62.5%/100%），不可引用——README 已
  标注"稳定数字需低峰期采集"**；报告 report_hybrid_20260924_084049.md 留档作限流样本。
- 鉴权去重后行为不变：AUTH_ENABLED=1 时七路由 401 拦截由 include_router 统一承担
  （v3.12 验证路径未动）。

---

## v3.16（2026-09-24）· 单元测试体系落地（65 用例进 CI）+ 三处真 bug 修复

**做了什么**
1. **pytest 单元测试体系**（`backend/tests/`，9 文件 65 用例，2 秒跑完，无 key/无网络/无模型依赖）：
   文档接口路径穿越防护 25 例（v3.14 安检修复用例转正为回归测试）、RRF 融合与分词 5 例、
   兜底判定/去重/LLM 错误分类/懒加载契约/提示词组装 15 例、鉴权矩阵 4 例、官方直答与
   别名 7 例、指代特征 3 例、敏感词 2 例、配置层 4 例；CI 在语法检查后新增 pytest 步骤。
2. **bug 修复①——提示词花括号炸弹（潜伏 bug）**：`_build_rag_messages` 的系统提示与历史
   消息改用 Message 对象直装，不走模板插值——知识库内容或历史含 `{xxx}`（JSON 示例、
   代码片段）时不再被 langchain 当占位符解析导致调用崩溃（Critic 提示词同款坑 v3.3 已修，
   本处补齐生成主链路）；仅最后一条 human 保留 `{question}` 占位符。
3. **bug 修复②——重排降级后兜底失真**：rerank 失败降级时给结果标记 `rerank_degraded`
   （SearchResult 新增字段），兜底判定 `_should_fallback` 据此改用 dense_score 余弦阈值——
   此前降级后 score 是召回量纲（BM25 分可达 8.5），按 rerank 阈值 0.60 比会永远漏兜底。
4. **bug 修复③——评测分母稀释**：`evaluate_rag.py` 检索指标分母从"总数-拒答"修正为
   同时扣除官方直答题与观察题，与"观察题只记录行为不计分"的声明口径对齐；报告头
   同步披露四类题数。
5. **docstring 勘误**：`hybrid_retriever._tokenize` 注释声称"过滤单字符标点"但实现只
   过滤空白——注释改为如实描述，并加现状快照测试钉住该行为（改过滤需重跑检索评测）。

**解决了什么问题**
- 单元测试从 0 到 1：此前"测试=评测脚本+手工冒烟"，pytest 层缺失；现在安全修复有回归
  防线（改坏穿越防护会立刻红），且 65 用例进 CI 与评测守护线构成双保险。
- 三个 bug 全部由"先写失败测试再修"流程钉住：花括号（改前 2 红例）、量纲（改前 1 红例）、
  分母（L2 集实跑红绿对照）。

**怎么验证的**
- pytest：第一轮 60/65 绿、4 红（3 红为 bug①② 的预期失败，1 红为测试断言自身写错——
  已按现状快照修正并归入 docstring 勘误）；修复后 65/65 全绿（2.13s）。
- bug① 端到端：8002 验证实例实测"轮 1 问题含 `{max_tokens}` → 轮 2 指代追问"——
  改前该场景历史组装必炸 KeyError（500），改后两轮均 HTTP 200 且正常返回检索来源。
- bug② 单测红绿：`score=8.5/dense=0.2/degraded` 用例改前判"不兜底"（错），改后正确兜底。
- bug③ 实跑红绿：L2 集（18 题=检索 13+拒答 3+观察 2）改前 Hit@5 13/15=86.7%（观察题
  占分母），改后 13/13=100%/MRR 0.962（口径对齐）；主集（无观察/官方题）12/17=70.6% /
  MRR 0.598 / 拒答 3/3 逐字不变——分母修正零回归实证。
- 服务冒烟：8002 实例 /health 200、关键词问答与完整检索问答正常（轮 2 答案为免费档限流
  的既有友好降级文案，与本次改动无关）；验完进程结束、端口无残留、测试会话历史已删除。

---

## v3.15（2026-09-24）· CI 修复转绿：密钥校验延迟化 + 建库/CPU torch 进流水线

**做了什么**
1. **密钥校验从 import 链移除（CI 红的根因）**：`config/settings.py` 删除模块级
   `raise`，新增 `require_api_key()` 统一把关——服务启动入口（`main.py`，缺失依旧
   启动即报错退出并给中文指引）与 `llm_factory.make_llm`（创建实例前校验）两处调用。
2. **LLM 实例全部懒加载**：`chat_service` 三个模块级实例（生成/改写/Critic）改为
   `get_llm() / get_llm_rewrite() / get_llm_critic()` 缓存式懒加载；`query_rewriter`
   的专用改写实例（低温度/少 token/独立超时）同样懒加载——import 本项目任何模块
   都不再建实例、不再要求密钥。
3. **CI 流水线补全**（`.github/workflows/ci.yml`）：安装步骤先装 CPU 版 torch
   （云端无显卡，CUDA 版会白拖 NVIDIA 全家桶）；评测前新增 `init_knowledge_base.py`
   建库步骤（CI 环境没有 `backend/data/` 向量库）；`HF_ENDPOINT` 覆盖为官方源
   （境外机器直连更快）。

**解决了什么问题**
- 上一版（104e096）新增的 CI 从未跑绿过：`ZHIPU_API_KEY` 模块级强校验让
  --skip-llm 评测在无 key 环境直接崩（GitHub Actions run 35911673324 = failure）；
  即使过了这关，CI 环境没有向量库，评测仍无数据可查。"Hit@5 守护线 65.6%"
  自宣称起实际从未生效——本次修复后守护线首次真正上岗。
- 附带收益：服务启动不再等模型加载（懒加载后 8002 实例 5 秒即就绪）。

**怎么验证的**
- 红（改前）：`ZHIPU_API_KEY= python scripts/evaluate_rag.py --mode hybrid --skip-llm`
  → exit 1，`settings.py:64` ValueError，与 CI 失败日志同因同源。
- 绿（改后）：同命令 → exit 0，Hit@5 12/17 = 70.6% / MRR 0.598 / 拒答 3/3，
  与基线逐字一致零回归（报告 report_hybrid_20260924_063416.md）。
- 服务冒烟（8002 验证实例，不动现役 8001）：/health 200；关键词规则问答正常
  （不调 LLM）；真实检索问答全管线正常（改写→混合检索→重排→生成带来源）；
  验完进程结束、端口无残留、测试会话历史已删除。
- CI 本体：本提交触发的 GitHub Actions run 全步转绿（含守护线断言）即为最终验证。

---

## v3.14（2026-09-23）· 文档上传路径穿越修复 + 安检报告勘误（安检 L1 复核）

**做了什么**
1. **文档上传/删除路径穿越修复（安检复核 L1）**：`document_router` 新增
   `_safe_filename`（客户端文件名剥路径成分，`../../evil.md` → `evil.md`，拒空/拒点开头/拒超长）、
   `_check_kb_id`（kb_id 白名单 `^[\w\-]{1,64}$`——它会拼进 `doc_list_{kb_id}.json` 注册表路径）、
   `_ensure_within_upload`（落盘前 realpath 边界双保险）；`document_service.delete_document`
   删除前同样做 realpath 越界校验，防历史脏注册表数据借删除接口删任意文件。
2. **安检报告勘误（docs/pipeline/安检报告.md，4 处）**：删除对不存在提交号 `dbaf112` 的
   三处引用（初版笔误，`git cat-file` 证实对象不存在）；"轮换已核实"改回诚实的
   "待平台核实"（与正文"判定为未知"自洽）；删除"无需重写历史"表述（2026-09-23 历史已
   整体重写并 force-push，表述失效）；补删旧 key 前缀残留（此前"记录全删"的漏网项，
   【v3.20】起全仓不再出现任何真实密钥片段字样）。
3. **代码注释残迹清理**：5 个文件 8 处"分工N写"式注释改为中性描述（document_router /
   vector_router / document_service / vector_service / schemas/models.py）。

**解决了什么问题**
- 上传接口可用 `filename=../../x.md` 向任意路径写文件、`kb_id=../../x` 读写任意注册表路径（L1 级注入面闭合）。
- 安检报告自身引用不存在的证据、结论与正文自相矛盾（报告可信度修复）。

**怎么验证的**
- 路径穿越用例实测 13 条全过：4 条清洗（`../../evil.md`→`evil.md`、`..\..\win.md`→`win.md` 等）、
  5 条文件名拦截、5 条 kb_id 拦截（`../../x` / 空 / 65 字符 / 含 `/` `\`）、落盘越界拦截 1 条。
- `py_compile` 通过；后端无 schema/接口变更，前端零改动。

---

## v3.13（2026-09-23）· 前端 markdown XSS 净化（安检 L1 第 7 项修复）

**做了什么**
- **引入 DOMPurify 3.4.15**（`frontend` 唯一新增依赖，`npm install dompurify`）：marked 自身不做 HTML 净化（sanitize 能力早已移除），LLM 输出与知识库文档内容（文档可被任何人上传）经 `renderMarkdown()` 渲染后直进 `v-html`，`<img onerror>`/`<script>`/`javascript:` 链接等载荷会原样执行——典型存储型 XSS 面。
- **`ChatPage.vue` `renderMarkdown()` 末尾统一净化**：`mdParser.parse()` 产物先过 `DOMPurify.sanitize()` 再返回，流式增量/普通接口降级/历史恢复/撤回恢复等全部渲染路径共用此函数，一处收口全覆盖。
- **顺带闭环同文件唯一另一处动态 `v-html`**：欢迎屏快捷问题 `q.emoji`（来自后端领域配置）同样过净化；领域配置现均为纯文本表情（📦 等），净化后渲染零变化。`cat.icon`/`VALORANT_QUICK` 的 SVG 为组件内写死的字面量（可信静态内容），不改。

**解决了什么**
- 安检 L1 第 7 项（输出编码）阻断：任何人上传一份含 `<img src=x onerror=alert(1)>` 的知识库文档，提问命中后载荷随答案原样进 `v-html` 即在浏览者会话里执行。修复后事件属性/script/iframe/危险协议一律在渲染前剥离，正常 markdown（标题/加粗/代码/外链/表格/引用/列表）零误伤。

**验证**
- 前后对比（jsdom 思路，复用仓库内同版本 marked@18.0.5 + dompurify@3.4.15、与 ChatPage.vue 完全相同的 parser 配置）：修复前 5 类载荷（onerror/script/markdown javascript: 链接/iframe/字面 javascript: 链接）在渲染产物中全部原样保留（漏洞确认）；修复后逐载荷断言全部 PASS（onerror 剥离且 img 本体保留、script/iframe 整块移除、javascript: href 剥离且链接文本保留），脚本退出码 0。
- 不误伤回归：正常业务 markdown 净化前后渲染结果一致；纯文本 emoji 净化后不变。
- `npm run build` 通过（569ms，exit 0；警告为 element-plus 依赖 @vueuse/core 的既有 PURE 注解提示，与本改动无关）；`dist/index.html` 实际引用的新产物 `index-Dql4JR3I.js` 内含 DOMPurify 与 7 处 sanitize 调用（bundle 走查）。
- 本卡无后端 Python 改动（py_compile/pytest 按卡跳过）；8001/5174 现役实例未动；无 git 操作。完整证据链见 `docs/pipeline/安检报告.md` 第 7 项。

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
- 评测升级为三层：L1 主回归集（20+8 冻结）、L2 扩充集（18 题：黑话别名 8 + 多轮指代 5 + 拒答陷阱 3 + 边界观察 2，关键词全部在知识库逐条核对；2026-09-24 勘误：原文误写"21 题"，与分解式及 eval_set_l2.json 实际条数不符）、L3 Critic 评审员标注集（10 题人工标注，专测评审判分质量）。
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
