# AI Journey · 90 天 AI 应用 / LLM / Agent 开发转型

> 从传统 Web/前端 转向 **AI 应用开发工程师** 的完整学习工程。
> 主路线：`LLM API → Prompt → RAG → Agent → LangGraph → MCP → AI 工程化 → 项目 → 面试`

**当前进度**：Day 3（2026-10-10 开始）· 核心已跑通（2026-10-14）
**每日投入**：1–2 小时（在职节奏）
**主力模型**：DeepSeek `deepseek-flash`（OpenAI 兼容协议）

---

## 📌 先看这两份文档

| 文档 | 说明 |
| --- | --- |
| [docs/90天学习计划-Day3起.md](docs/90天学习计划-Day3起.md) | **主计划**：Day 3–90 逐天任务、资料链接、完成标准 |
| [docs/interview-questions.md](docs/interview-questions.md) | 面试题库（Day 87–89 模拟面试用） |

---

## 📁 目录结构

```
~/ai-journey/                   # WSL 家目录（主副本，从这里写代码）
├── docs/                       # 计划、笔记、面试题
├── day03-cli-chat/             # ✅ Day 3   CLI Chat（已跑通、已上传 GitHub）
├── day04-prompt/               # ⬜ Day 4   Prompt 实验台
├── day05-structured/           # ⬜ Day 5   JSON 结构化输出
├── day06-streaming/            # ⬜ Day 6   流式 / SSE
├── day07-web-chat/             # ⬜ Day 7   Next.js + FastAPI 打通
├── project1-ai-chat/           # ⬜ Day 8–14  AI Chat 完整版
├── project2-knowledge-base/    # ⬜ Day 15–30 企业知识库（RAG）
├── project3-research-agent/    # ⬜ Day 31–45 Research Agent
├── project4-mcp-sql-agent/     # ⬜ Day 46–60 MCP 数据库 Agent
└── README.md
```

## 🔗 GitHub

| 仓库 | 内容 | 状态 |
| --- | --- | --- |
| [Conroy-X/deepseek-stream-demo](https://github.com/Conroy-X/deepseek-stream-demo) | 本工程全部代码：Day 2 流式 Demo（根目录 `main.py`）+ Day 3 CLI Chat（`day03-cli-chat/`）+ 学习文档（`docs/`） | ✅ 已同步 |

> **`~/ai-journey` 就是本仓库的工作副本**（git 仓库根目录即此处），远端是 `git@github.com:Conroy-X/deepseek-stream-demo.git`。
> 推送走 **SSH**（`ssh -T git@github.com` 已验证为 `Conroy-X`）；`~/projects/deepseek-stream-demo` 是早期克隆，已与远端同步。

---

## 🧩 Day 2 · DeepSeek 流式问答 Demo（根目录 `main.py`）

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
（Day 6 会正式讲这套流式机制，届时把这里的实现对照一遍。）

---

---

## 🚀 快速开始（WSL 里执行）

```bash
# 1. 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

# 2. 进入今天的学习目录
cd ~/ai-journey/day03-cli-chat

# 3. 建环境装依赖
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt

# 4. 配置 Key（去 https://platform.deepseek.com/api_keys 申请）
cp .env.example .env && nano .env

# 5. 离线自检（不花钱，确认代码没问题）
python selfcheck.py

# 6. 真机跑起来
python 01_hello.py
python chat.py
```

---

## ✅ 进度追踪

| 阶段 | 天数 | 主题 | 状态 | 产出 |
| --- | --- | --- | --- | --- |
| 一 | Day 3–14 | LLM API + Prompt + 全栈 Chat | 🟡 进行中 | AI Chat 完整版 |
| 二 | Day 15–30 | Embedding / pgvector / RAG | ⬜ | 企业知识库 AI |
| 三 | Day 31–45 | Function Calling / LangGraph | ⬜ | Research Agent |
| 四 | Day 46–60 | MCP | ⬜ | MCP 数据库 Agent |
| 五 | Day 61–75 | AI 工程化 + LLM 原理 | ⬜ | 容器化 + 评测 |
| 六 | Day 76–90 | 项目打磨 + 简历 + 面试 | ⬜ | 3 个仓库 + 简历 |

---

## 🔑 三条铁律

1. **API Key 只放 `.env`**，永远不进 git。泄露立刻吊销重建。
2. **允许跳过「选做」，不允许跳过 `git commit`**。每天必须留下痕迹。
3. **每个阶段末尾的项目日不能砍** —— 那是简历上唯一能写的东西。

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
