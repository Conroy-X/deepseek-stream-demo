# AI Journey · 90 天 AI 应用 / LLM / Agent 开发转型

> 从传统 Web/前端 转向 **AI 应用开发工程师** 的完整学习工程。
> 主路线：`LLM API → Prompt → RAG → Agent → LangGraph → MCP → AI 工程化 → 项目 → 面试`

**当前进度**：Unit 1–19 已学完 ✅｜🟡 Unit 20 学习中（基础 RAG）｜📦 Unit 21–60 材料就绪（自检全绿，待逐个学）
**状态图例**：✅ 已学完 ｜ 🟡 进行中 ｜ 📦 材料就绪（代码可跑、自检全绿，但还没学到——自检全绿 ≠ 学完）
**每日投入**：1–2 小时（在职节奏）
**主力模型**：DeepSeek `deepseek-flash`（OpenAI 兼容协议）

---

## 📌 先看这两份文档

| 文档 | 说明 |
| --- | --- |
| [docs/学习单元计划.md](docs/学习单元计划.md) | **主计划**：Unit 03–90 学习单元、资料链接、完成标准（按单元推进，不跟日历） |
| [docs/interview-questions.md](docs/interview-questions.md) | 面试题库（Unit 87–89 模拟面试用） |

---

## 📁 目录结构

```
~/ai-journey/                  # WSL 家目录（= GitHub 仓库根目录）
├── 00-setup/                  # 环境脚本、密钥模板
├── docs/                      # 计划、笔记、面试题
├── unit01-fastapi/            # ✅ Unit 1   FastAPI 基础（路由/校验/async/流式）
├── unit02-llm-basics/         # ✅ Unit 2   LLM 基础（token/temperature/context）
├── unit03-cli-chat/           # ✅ Unit 3   CLI Chat
├── unit04-prompt/             # ✅ Unit 4   Prompt 实验台
├── unit05-structured/         # ✅ Unit 5   JSON 结构化输出
├── unit06-streaming/          # ✅ Unit 6   流式 / SSE（后端 SSE + 浏览器实验台）
├── unit07-web-chat/           # ✅ Unit 7   全栈打通 + CORS
├── unit08-chat-ui/            # ✅ Unit 8   Markdown 渲染 + 代码高亮（前端，无 py）
├── unit09-conversations/      # ✅ Unit 9   多轮对话 + SQLite
├── unit10-context/            # ✅ Unit 10  上下文管理（三种策略）
├── unit11-postgres/           # ✅ Unit 11  PostgreSQL 三张表
├── unit12-auth/               # ✅ Unit 12  JWT 登录 / 注册 / 权限
├── unit13-llm-engineering/    # ✅ Unit 13  Timeout / Retry / Rate Limit
├── unit14-project1/           # ✅ Unit 14  项目 1 收口：AI Chat 完整版
├── unit15-embedding/          # ✅ Unit 15  Embedding + 余弦相似度 + Top-K
├── unit16-pgvector/           # ✅ Unit 16  pgvector + HNSW / IVFFlat
├── unit17-doc-parsing/        # ✅ Unit 17  PDF / MD / TXT / DOCX 文档解析
├── unit18-chunking/           # ✅ Unit 18  固定 / 递归 / 语义切块
├── unit19-vector-search/      # ✅ Unit 19  Top-K 向量检索与召回评测
├── unit20-basic-rag/          # 🟡 Unit 20  检索增强生成最小闭环
├── unit21-citation/           # 📦 Unit 21  来源引用与页码定位
├── unit22-knowledge-base/     # 📦 Unit 22  知识库上传 / 列表 / 删除
├── unit23-doc-pipeline/       # 📦 Unit 23  文档解析切块后台流水线
├── unit24-embedding-pipeline/ # 📦 Unit 24  批量向量化与断点续跑
├── unit25-retriever/          # 📦 Unit 25  可配置 Retriever 模块
├── unit26-reranker/           # 📦 Unit 26  Rerank 重排与效果对比
├── unit27-rag-streaming/      # 📦 Unit 27  RAG Chat 流式输出
├── unit28-citation-ui/        # 📦 Unit 28  引用高亮与原文定位 UI
├── unit29-rag-engineering/    # 📦 Unit 29  权限 / 日志 / 成本 / 安全
├── unit30-project2/           # 📦 Unit 30  项目 2：企业知识库 AI 交付
├── unit31-function-calling/   # 📦 Unit 31  Function Calling 入门
├── unit32-schema/             # 📦 Unit 32  参数 Schema
├── unit33-tool-result/        # 📦 Unit 33  Tool Result 回填
├── unit34-agent-loop/         # 📦 Unit 34  Agent Loop
├── unit35-multi-tool/         # 📦 Unit 35  Multi Tool 路由
├── unit36-agent-errors/       # 📦 Unit 36  Agent 错误处理
├── unit37-langgraph-state/    # 📦 Unit 37  LangGraph State（TypedDict + merge）
├── unit38-node-edge/          # 📦 Unit 38  Node / Edge（多节点 + stream）
├── unit39-conditional-edge/   # 📦 Unit 39  Conditional Edge（条件路由）
├── unit40-checkpoint/         # 📦 Unit 40  Checkpoint / Memory（interrupt）
├── unit41-search-tool/        # 📦 Unit 41  Search Tool（离线 fixture）
├── unit42-fetch-tool/         # 📦 Unit 42  Fetch Tool（HTML 清洗）
├── unit43-extract/            # 📦 Unit 43  Extract（结构化要点提取）
├── unit44-analysis/           # 📦 Unit 44  Analysis（多来源交叉比对）
├── unit45-project3/           # 📦 Unit 45  项目 3 交付（Research Agent）
├── unit46-mcp-intro/          # 📦 Unit 46  MCP 协议手写（JSON-RPC 帧）
├── unit47-architecture/       # 📦 Unit 47  架构与传输（stdio / HTTP）
├── unit48-capabilities/       # 📦 Unit 48  三大能力（Tools/Resources/Prompts）
├── unit49-first-server/       # 📦 Unit 49  第一个 Server（SDK 2.x）
├── unit50-useful-tools/       # 📦 Unit 50  自定义 Tool
├── unit51-resource/           # 📦 Unit 51  Resource 只读数据
├── unit52-database-tool/      # 📦 Unit 52  Database Tool（只读连接 vs 词法）
├── unit53-agent-mcp/          # 📦 Unit 53  Agent + MCP（schema 转换）
├── unit54-multi-mcp/          # 📦 Unit 54  多 MCP Server（前缀 + 降级）
├── unit55-mcp-sql-mvp/        # 📦 Unit 55  项目 4 MVP（NL→SQL 端到端）
├── unit56-nl2sql/             # 📦 Unit 56  NL→SQL（schema 注入 + 方言）
├── unit57-sql-security/       # 📦 Unit 57  SQL 安全四道闸
├── unit58-error-recovery/     # 📦 Unit 58  Error Recovery（错误自愈）
├── unit59-mcp-ops/            # 📦 Unit 59  MCP 工程化（日志/超时/并发/错误码）
├── unit60-project4/           # 📦 Unit 60  项目 4 交付（ARCHITECTURE/SECURITY/DEMO）
├── experiments/               # 🧪 课程外的试用（Search/Fetch API、零依赖）
└── README.md
```

> **Unit 17–31 已按 Unit 1–16 的同一标准重做完毕**（32–36、37–45、46–60 同标准），全部是可运行实现，不是骨架：
> `main.py` 末尾带离线自检（`uv run python main.py` 打印 `结果：N 通过 / 0 失败`），
> `static/index.html` 是可操作实验台，`README.md` 走「完成标准 / 快速开始 / 自检 / 这个目录里有什么 /
> 实测 / 接口 / 常见坑 / 验收清单 / 下一单元」，`notes.md` 里每个数字都是实跑出来的。
> ⚠️ **「材料就绪 ≠ 学完」**：目录树里的 📦 表示代码自检全绿但还没学到；学习进度以顶部「当前进度」为准。

| 单元 | 自检条数 | 单元 | 自检条数 |
| --- | ---: | --- | ---: |
| 17 文档解析 | 30 | 25 Retriever | 36 |
| 18 切块 | 29 | 26 Rerank | 19 |
| 19 向量检索 | 72 | 27 流式输出 | 51 |
| 20 基础 RAG | 64 | 28 引用 UI | 55 |
| 21 引用校验 | 64 | 29 工程化与安全 | 84 |
| 22 知识库 | 86 | 30 项目 2 | 21 |
| 23 文档流水线 | 67 | 31 Function Calling | 30 |
| 24 向量化流水线 | 67 | **合计** | **775** |

> 端口约定：Unit N → `80NN`（17→8017 … 31→8031）。全部离线可跑，不需要 API key。
> Unit 16 实测备忘（2026-11-03）：5 万行 / 512 维 / top10，全表扫 260ms → HNSW 0.98ms（**266×**）；
> 坑：`ef_search` 拉太大 planner 会弃索引退回 Seq Scan——**建了索引 ≠ 用上索引，必须 EXPLAIN 确认**。

> 四个 project 不再单独建目录：项目 1 收在 `unit14-project1/`，
> 项目 2（企业知识库 RAG）由 Unit 15–30 逐单元累积而成。


## 🔗 GitHub

| 仓库 | 内容 | 状态 |
| --- | --- | --- |
| [Conroy-X/deepseek-stream-demo](https://github.com/Conroy-X/deepseek-stream-demo) | 本工程全部代码：`unit01-fastapi/` ~ `unit60-project4/` 六十个学习单元 + 根目录流式 Demo（`main.py`）+ 学习文档（`docs/`） | ✅ 已同步 |

> **`~/ai-journey` 就是本仓库的工作副本**（git 仓库根目录即此处），远端是 `git@github.com:Conroy-X/deepseek-stream-demo.git`。
> 推送走 **SSH**（`ssh -T git@github.com` 已验证为 `Conroy-X`）；`~/projects/deepseek-stream-demo` 是早期克隆，已与远端同步。

---

## 🧩 Unit 2 · DeepSeek 流式问答 Demo（根目录 `main.py`）

> 单文件（约 80 行）：**FastAPI 接住参数 → 请求 DeepSeek → 流式吐回客户端**。没有多轮对话、没有数据库、没有前端。

```bash
cd ~/ai-journey
uv sync                # 首次安装依赖
uv run fastapi dev     # 默认 http://127.0.0.1:8000（带自动重载）
```

调用：

```bash
# curl（-N 关闭缓冲，否则看不到流式效果）
curl -N "http://localhost:8000/ask?q=用一句话介绍 FastAPI"
```

| 文件 | 说明 |
| --- | --- |
| `main.py` | 全部代码 |
| `pyproject.toml` | 依赖 + `[tool.fastapi] entrypoint`，供 `uv run` / `fastapi dev` 使用 |
| `uv.lock` | uv 锁定的依赖版本 |
| `run.sh` | 等价于 `uv sync && uv run fastapi dev` |

核心就三段：`@app.get("/ask")` 用 `Query` 接住问题 → `httpx.AsyncClient().stream()` 以 `stream=True` 请求 `/chat/completions` → 把每个 `data:` 分片里的 `delta.content` 立刻 `yield` 给 `StreamingResponse`。
（Unit 6 会正式讲这套流式机制，届时把这里的实现对照一遍。）

---

---

## 🚀 快速开始（WSL 里执行）

```bash
# 1. 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

# 2. 进入今天的学习目录（每个单元都是独立的 uv 工程）
cd ~/ai-journey/unit03-cli-chat

# 3. 同步依赖（建 .venv + 按 uv.lock 装，幂等，可反复执行）
uv sync

# 4. 配置 Key（去 https://platform.deepseek.com/api_keys 申请）
cp .env.example .env && nano .env

# 5. 离线自检（不花钱，确认代码没问题）
uv run python selfcheck.py

# 6. 真机跑起来
uv run python 01_hello.py
uv run python chat.py
```

---

## 🩺 一键自检（2026-10-23 新增）

所有单元的自检都是独立的 `.venv` + 独立脚本，以前要跑十次。现在一条命令：

```bash
cd ~/ai-journey
bash 00-setup/healthcheck.sh          # 环境 + 10 个单元，全绿才返回 0
bash 00-setup/healthcheck.sh -v       # 顺带打印失败项的完整输出
```

它检查两段：

| 段 | 内容 | 缺了会怎样 |
| --- | --- | --- |
| 一、环境 | uv / node / `core.hooksPath` / Postgres / pgvector / Redis / Docker / 各单元 `.env` | 只警告，不算失败（Unit 11 之前只有 Postgres 是硬需求） |
| 二、单元 | unit01–unit10 各自的离线自检 | 任一失败 → 退出码 1 |

**不联网、不调用模型、不花一分钱**；完整输出落在 `logs/`（已 gitignore）。

> 适合当「热身 A 步骤」：每次坐下来先跑一遍，确认上一轮的代码没坏，再开始新单元。

---

## ✅ 进度追踪

| 阶段 | 天数 | 主题 | 状态 | 产出 |
| --- | --- | --- | --- | --- |
| 一 | Unit 3–14 | LLM API + Prompt + 全栈 Chat | ✅ 完成（2026-10-25 复核） | AI Chat 完整版 |
| 二 | Unit 15–30 | Embedding / pgvector / RAG | 🟡 进行中（学到 Unit 18，材料已备到 30） | 企业知识库 AI |
| 三 | Unit 31–45 | Function Calling / LangGraph | 📦 材料就绪 | Research Agent |
| 四 | Unit 46–60 | MCP | 📦 材料就绪（846 条自检全绿） | MCP 数据库 Agent |
| 五 | Unit 61–75 | AI 工程化 + LLM 原理 | ⬜ | 容器化 + 评测 |
| 六 | Unit 76–90 | 项目打磨 + 简历 + 面试 | ⬜ | 3 个仓库 + 简历 |

---

## 🔑 三条铁律

1. **API Key 只放 `.env`**，永远不进 git。泄露立刻吊销重建。
2. **允许跳过「选做」，不允许跳过 `git commit`**。每天必须留下痕迹。
3. **每个阶段末尾的项目日不能砍** —— 那是简历上唯一能写的东西。

---

## 🛡️ 提交守门员（pre-commit hook）

第 1 条铁律已经**交给机器执行**了 —— 靠人记得迟早会手滑，一次失误就得吊销重建密钥。

`.githooks/pre-commit` 会在每次 commit 前拦住三类东西：

| 拦什么 | 规则 |
| --- | --- |
| 敏感文件 | `.env`（放行 `.env.example`）、`.venv/`、`logs/`、`*.pem` / `*.key`、`id_rsa` / `id_ed25519`、`.git-credentials` |
| 密钥内容 | 任何新增行里形如 `sk-` + 20 位以上的字符串（命中时会打码显示） |
| 大文件 | 单个文件 >5MB（防止模型/数据集/依赖误入） |

**新克隆后启用一次**（`core.hooksPath` 是本机配置，不会跟着仓库走）：

```bash
git config core.hooksPath .githooks
```

**临时跳过**（确认无风险时）：`git commit --no-verify`

> 实测（2026-10-14）：假密钥 → 拦截；`git add -f .env` → 拦截；干净提交 → 放行。

---

## 📚 官方资料入口（收藏这几个就够）

- [DeepSeek API 文档](https://api-docs.deepseek.com/zh-cn/) ｜ [模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) ｜ [错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes)
- [FastAPI 中文文档](https://fastapi.tiangolo.com/zh/)
- [Next.js 文档](https://nextjs.org/docs/app)
- [PostgreSQL 文档](https://www.postgresql.org/docs/current/index.html) ｜ [pgvector](https://github.com/pgvector/pgvector)
- [LangGraph 文档](https://docs.langchain.com/oss/python/langgraph/overview)
- [MCP 官方文档](https://modelcontextprotocol.io/)
- [RAGAS（RAG 评测）](https://docs.ragas.io/en/stable/)
- [OWASP LLM Top 10（AI 安全）](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [HuggingFace LLM Course 中文](https://huggingface.co/learn/llm-course/zh-CN/chapter1/1)
