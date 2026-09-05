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
