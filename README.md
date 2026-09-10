# 无畏契约 · 智能问答助手（VALORANT RAG Assistant）

> 把散落在网页、图鉴、教程里的游戏知识，收进一个可版本管理的知识库，让 AI 边检索边回答，每条结论都指得出出处。

一个开箱即用的游戏知识 RAG（检索增强生成）系统。后端 FastAPI + LangChain + ChromaDB，前端 Vue 3 + Element Plus，大模型接入智谱 GLM-4-Flash。核心价值不是「能聊天」，而是**回答有依据、来源可追溯、知识可维护**。

---

## 它解决什么

玩家查「捷风怎么玩」「幻影弹道怎么样」「这张图怎么打」，往往要翻十几个网页、B 站视频和文档，信息零散、版本滞后、真假难辨。这个项目把 VALORANT 资料统一收口成结构化知识库，用 RAG 让大模型先检索再作答，把「拍脑袋回答」变成「查完资料再回答」。

## 核心亮点

| 能力 | 说明 |
|------|------|
| 检索增强问答 | 先向量检索知识库，再交给 GLM 生成，降低幻觉 |
| 来源可追溯 | 每条回答展示命中的文档块与匹配相似度 |
| 多轮记忆 | 支持上下文追问、回滚到历史某一轮 |
| 知识库管理 | 前端上传/删除文档，自动解析、切分、向量化 |
| 安全兜底 | 敏感词过滤 + 无关问题自动拒答 |
| 一键启动 | `start.bat` 同时拉起前后端 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 大语言模型 | 智谱 GLM-4-Flash |
| RAG 框架 | LangChain |
| 向量模型 | BAAI/bge-small-zh-v1.5 |
| 向量数据库 | ChromaDB |
| 前端 | Vue 3 + Element Plus + Vite |
| Markdown 渲染 | marked |

## 一分钟跑起来

```bash
# 1. 配置 API 密钥（智谱开放平台 https://open.bigmodel.cn/ 领取）
echo "ZHIPU_API_KEY=你的密钥" > .env

# 2. 安装后端
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 3. 安装前端（另开终端）
cd ../frontend
npm install

# 4. 启动（Windows 可直接双击根目录 start.bat）
cd ../backend && python main.py   # 终端 1：后端 :8000
cd ../frontend && npm run dev     # 终端 2：前端 :5173
```

浏览器打开 **http://localhost:5173**。首次启动会自动下载 embedding 模型（约 92MB，已配置国内镜像加速）。

## 架构

```
Vue3 前端（对话页 / 知识库管理页）
   │ HTTP /api/*
   ▼
FastAPI 后端
   ├─ routers   接口层（chat / document / vector）
   ├─ services  业务层（RAG 编排 / 多轮记忆 / 敏感词 / 文档解析）
   ├─ schemas   Pydantic 数据校验
   └─ config    模型、路径、提示词集中配置
   │
   ├─ ChromaDB（向量库）
   └─ 智谱 GLM-4-Flash（生成）
```

## 它是怎么工作的

1. **入库**：`knowledge_base/` 下的 Markdown → 解析切分 → `bge-small-zh-v1.5` 向量化 → 写入 ChromaDB。
2. **问答**：用户提问 → 向量检索 top-k 相关文档块 → 拼成上下文 → 交给 GLM 生成。
3. **追溯**：回答下方展示参考文档与相似度，知道「这句话来自哪」。

## 功能清单

| 功能 | 说明 |
|------|------|
| 智能问答 | 基于知识库的 VALORANT 问题解答 |
| 多轮对话 | 上下文记忆，连续追问 |
| Markdown 渲染 | 加粗、列表、代码块、表格 |
| 敏感词过滤 | 自动拦截不文明用语 |
| 无关拒答 | 非游戏问题自动拒绝 |
| 来源追溯 | 每次回答展示参考来源 |
| 撤回对话 | 回滚到历史某一轮 |
| 文档管理 | 上传/删除文档，自动解析入库 |
| 知识库管理页 | 前端可视化上传、查看向量规模 |
| 教程资源 | 官方资料导航、B 站/抖音搜索入口、进阶战术文档 |

## RAG 效果评估（v3.0）

在 backend 目录运行评估脚本，对比新旧管线效果：

```bash
cd backend
# 旧管线（纯向量检索）
python scripts/evaluate_rag.py --mode baseline --skip-llm
# 新管线（查询改写 + 混合检索 + 重排序）
python scripts/evaluate_rag.py --mode hybrid --skip-llm
# 去掉 --skip-llm 会连答案生成一起测（需要有效的 API 密钥）
```

实测数据（20 条评测集：武器/英雄/玩法/术语变体/拒答）：

| 指标 | 旧管线（纯向量） | 新管线（三级检索） |
|------|------|------|
| 检索 Hit@5 | 94.1% | 94.1% |
| 检索 MRR | 0.882 | 0.796 |
| 答案关键词覆盖 | — | 75.0%（24/32） |
| 拒答正确率（无关问题拦截） | 0%（靠阈值 0.35 拦不住） | **100%**（重排分数阈值 + 强化版提示词规则双保险） |

**结论分析**（诚实数据，答辩可直接引用）：
- 拒答兜底是本次升级最确定的收益：旧管线中文向量相似度普遍虚高（>0.35），无关问题拦不住；新管线重排分数区分度好（无关≈0.50，相关≥0.67），阈值 0.60 精准拦截。
- 检索类指标在本知识库（小规模、语义清晰）上持平：纯向量已近上限。MRR 略降的原因是 BM25 把"Jett 在教程文档里顺带提到"这类弱关联块带进候选，入门级 bge-reranker-base 未能完全纠正。混合检索的收益会随知识库规模增长而显现（RAGFlow/QAnything 均为此架构）。
- 重排模型可通过环境变量 `RERANK_MODEL` 更换（如 `bge-reranker-v2-m3`，效果更好但 CPU 推理约慢 3 倍，且需重新调 `rerank_score_threshold`）。

## 企业化改造指南

本系统的领域内容已全部配置化，改造成任意企业知识助手**只需三步、不改一行代码**：

1. **复制领域皮肤**：把 `backend/config/domain_profiles/valorant.yaml` 复制一份改名（如 `enterprise.yaml`），把里面的 `system_prompt`（人设与回答规范）、`fallback_answer`/`refuse_answer`（话术）、`custom_rules`（快捷指令）换成企业内容，`default_kb_id` 改为 `enterprise`。
2. **切环境变量**：在 `.env` 中设置 `APP_DOMAIN=enterprise`。
3. **导入企业文档**：通过前端"知识库管理"页上传企业制度/产品/FAQ 文档，或运行 `python scripts/init_knowledge_base.py` 重建索引。

企业部署建议补充的环境变量：

```env
AUTH_ENABLED=1          # 开启 API Key 认证
API_KEYS=你的密钥1,你的密钥2   # 多个用逗号分隔，前端/调用方请求头带 X-API-Key
RAG_RERANK=1            # 管线开关均可独立控制（RAG_HYBRID / RAG_QUERY_REWRITE 同理）
```

已预留的企业化能力：问答审计日志（`backend/data/audit/`，合规留痕可回溯）、API Key 认证、多知识库隔离（kb_id 维度）。

## 知识库

知识库源文件位于 `knowledge_base/`，运行时同步到 `backend/data/uploads/` 并向量化。当前预置：

- 无畏契约英雄介绍.md
- 无畏契约武器图鉴.md
- 无畏契约地图攻略.md
- 无畏契约新手攻略.md
- 无畏契约权威数据与官方资料导航.md
- 无畏契约进阶战术与阵容体系.md
- 无畏契约枪械控制与训练方法.md
- 无畏契约学习教程与视频资源.md


如需更新知识库，有两种方式：

1. 通过前端界面的文档管理页上传 `.txt` / `.md` / `.docx` / `.pdf` 文件
2. 直接调用 API：`POST /api/document/upload`

> 修改 `knowledge_base/` 下的 md 文件后，需要重建索引：运行 `重新索引.bat`，或在 `backend` 目录执行 `python scripts/init_knowledge_base.py`。该脚本会清空旧向量库、同步源文档并重新向量化。

## 项目结构

```
├── backend/            # FastAPI 后端（routers/services/schemas/scripts）
├── frontend/           # Vue3 前端（components/api/views）
├── knowledge_base/     # 可版本管理的知识源（Markdown）
├── docs/               # 调研与升级方案
├── data/               # 运行时数据（chroma_db / chat_history / uploads，自动生成）
├── start.bat           # 一键启动
└── 重新索引.bat         # 重建向量索引
```

## 更新知识库

1. 前端文档管理页上传 `.txt / .md / .docx / .pdf`；
2. 或调用 `POST /api/document/upload`；
3. 改了 `knowledge_base/` 下的 md 后，运行 `重新索引.bat`（或 `python backend/scripts/init_knowledge_base.py`）重建索引。

## 当前进展

- v2.0：知识库管理页、权威资料/教程资源、RAG 配置集中化、来源相似度展示、可版本管理的知识源。

## 许可证

仓库暂未附 LICENSE 文件；如需公开分发，建议补充 MIT 协议。
