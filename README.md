# 无畏契约智能对话小助手

基于 RAG（检索增强生成）的无畏契约（VALORANT）游戏知识智能问答系统。
后端用 FastAPI + LangChain + ChromaDB，前端用 Vue 3 + Element Plus，大模型接入智谱 GLM-4-Flash。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 大语言模型 | 智谱 GLM-4-Flash |
| RAG 框架 | LangChain |
| Embedding | BAAI/bge-small-zh-v1.5（sentence-transformers） |
| 向量数据库 | ChromaDB |
| 前端 | Vue 3 + Element Plus + Vite |
| Markdown 渲染 | marked |

## 项目结构

```
实训毕设无畏契约智能对话小助手/
├── backend/                  # 后端
│   ├── main.py               # FastAPI 入口
│   ├── config/settings.py    # 配置文件（模型、路径、提示词）
│   ├── routers/              # 路由层
│   │   ├── chat_router.py    # 对话接口
│   │   ├── document_router.py# 文档管理接口
│   │   └── vector_router.py  # 向量检索接口
│   ├── services/             # 业务逻辑层
│   │   ├── chat_service.py   # 对话业务（RAG、多轮记忆、敏感词过滤）
│   │   ├── document_service.py# 文档解析、切分、入库
│   │   └── vector_service.py # Embedding、向量检索
│   ├── schemas/models.py     # 数据模型
│   ├── utils/sensitive.py    # 敏感词过滤
│   └── requirements.txt      # Python 依赖
├── frontend/                 # 前端
│   ├── src/
│   │   ├── App.vue           # 根组件
│   │   ├── components/       # 页面组件（ChatPage 等）
│   │   ├── api.js            # 后端接口封装
│   │   └── style.css         # 全局样式
│   ├── public/               # 静态资源（头像图片等）
│   ├── vite.config.js        # Vite 配置（含 API 代理）
│   └── package.json          # Node 依赖
├── data/                     # 运行时数据（自动生成，不要手动删除）
│   ├── chroma_db/            # 向量数据库
│   ├── chat_history/         # 对话历史
│   └── uploads/              # 知识库文档（4 份 md）
├── .env                      # 环境变量（智谱 API 密钥）
└── start.bat                 # 一键启动脚本
```

## 环境要求

| 环境 | 版本 |
|------|------|
| Python | >= 3.12 |
| Node.js | >= 22 |
| npm | >= 10 |

## 安装步骤

### 1. 配置 API 密钥

在项目根目录创建 `.env` 文件：

```env
ZHIPU_API_KEY=你的智谱API密钥
```

> 密钥去智谱开放平台 https://open.bigmodel.cn/ 注册领取。

### 2. 安装后端依赖

```bash
cd backend

# 创建虚拟环境（推荐）
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
```

## 启动项目

### 方式一：一键启动（Windows）

双击项目根目录的 `start.bat`，会自动打开后端和前端两个命令行窗口。

### 方式二：手动启动（推荐）

需要开两个终端，后端必须先启动。

**终端 1 - 启动后端：**

```bash
cd backend
python main.py
```

看到 `Uvicorn running on http://0.0.0.0:8000` 表示启动成功。

**终端 2 - 启动前端：**

```bash
cd frontend
npm run dev
```

看到 `Local: http://localhost:5173/` 表示启动成功。

### 访问地址

| 入口 | 地址 |
|------|------|
| 前端页面（主要使用） | http://localhost:5173 |
| 后端 API 文档（Swagger） | http://127.0.0.1:8000/docs |

浏览器打开 **http://localhost:5173** 即可使用对话小助手。

> 首次启动后端时会自动下载 bge-small-zh-v1.5 模型（约 92MB），请耐心等待。项目已配置 hf-mirror.com 镜像加速下载。

## 命令行模式（API 直接调用）

不想开前端时，可以直接用 curl 调用后端 API：

```bash
# 发送对话
curl -X POST "http://127.0.0.1:8000/api/chat/send" ^
     -H "Content-Type: application/json" ^
     -d "{\"question\":\"介绍一下捷风\",\"session_id\":\"test001\"}"

# 查看知识库文档列表
curl "http://127.0.0.1:8000/api/document/list?kb_id=valorant"

# 向量检索（看检索到哪些文档块）
curl -X POST "http://127.0.0.1:8000/api/vector/search?question=幻影&top_k=3"

# 清空会话历史
curl -X POST "http://127.0.0.1:8000/api/chat/clear?session_id=test001"
```

> Windows 的 curl 用 `^` 换行，Linux/macOS 用 `\`。

## 功能说明

| 功能 | 说明 |
|------|------|
| 智能问答 | 基于知识库的无畏契约游戏问题解答 |
| 多轮对话 | 支持上下文记忆，连续追问 |
| Markdown 渲染 | 回答支持加粗、列表、代码块、表格等格式 |
| 敏感词过滤 | 自动拦截不文明用语 |
| 无关拒答 | 非游戏问题自动拒绝 |
| 来源追溯 | 每次回答展示参考文档来源 |
| 撤回对话 | 支持回滚到历史某一轮 |
| 文档管理 | 上传/删除知识库文档，自动解析入库 |

## 知识库

项目预置了 4 份无畏契约游戏资料（位于 `data/uploads/`）：

- 无畏契约英雄介绍.md
- 无畏契约武器图鉴.md
- 无畏契约地图攻略.md
- 无畏契约新手攻略.md

如需更新知识库，有两种方式：

1. 通过前端界面的文档管理页上传 `.txt` / `.md` / `.docx` / `.pdf` 文件
2. 直接调用 API：`POST /api/document/upload`

> 修改 md 文件后，需要重新索引才能让搜索生效。重新索引方法：删除 `data/chroma_db/` 目录后重启后端，或通过文档管理页重新上传。

## 四人分工

| 分工 | 姓名 | 职责 |
|------|------|------|
| 分工一 | 刘慧鹏 | 前端交互与页面开发：知识库管理页、文档管理页、对话聊天页、路由与状态管理、接口对接 |
| 分工二 | 周末 | 知识库文档管理：文档上传解析、多格式支持、文本清洗与拆分切块、文档 CRUD |
| 分工三 | 戴鸿 | 向量引擎与检索优化：向量化入库、语义检索、向量去重、纠错与意图识别 |
| 分工四 | 曹灏天 | 对话业务与答案增强：RAG 流程编排、LLM 对话、多轮记忆、敏感词过滤、兜底话术、前端聊天页 |
