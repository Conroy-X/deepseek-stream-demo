# DeepSeek 流式问答最小 Demo

单文件（`main.py`，约 80 行）：**FastAPI 接住参数 → 请求 DeepSeek → 流式吐回客户端**。
没有多轮对话、没有数据库、没有前端。

## 运行

```bash
cd ~/projects/deepseek-stream-demo
uv run fastapi dev
```

首次运行如果还没装依赖，先 `uv sync`。

- 默认监听 **http://127.0.0.1:8000**（`fastapi dev` 的默认值），带**自动重载**
- 想让局域网/其他机器也能访问：`uv run fastapi dev --host 0.0.0.0`
- 换端口：`uv run fastapi dev --port 9000`，或 `PORT=9000 uv run fastapi dev`
- 不想用 CLI：`.venv/bin/uvicorn main:app --reload`

## 调用

```bash
# 浏览器打开（流式效果最直观）
http://localhost:8000/ask?q=用一句话介绍 FastAPI

# curl（-N 关闭缓冲，否则看不到流式）
curl -N "http://localhost:8000/ask?q=用一句话介绍 FastAPI"
```

Python 客户端：

```python
import httpx

with httpx.stream("GET", "http://localhost:8000/ask", params={"q": "你好"}) as r:
    for chunk in r.iter_text():
        print(chunk, end="", flush=True)
```

## 核心就三段

1. `@app.get("/ask")` 用 `q: str = Query(...)` 接住问题；
2. `httpx.AsyncClient().stream(...)` 以 `stream: True` 请求 `/chat/completions`；
3. 把每个 `data:` 分片里的 `delta.content` 立刻 `yield`，
   交给 `StreamingResponse(..., media_type="text/plain")` 边收边发。

## 项目文件

| 文件 | 说明 |
|---|---|
| `main.py` | 全部代码 |
| `pyproject.toml` | 依赖 + `[tool.fastapi] entrypoint`，供 `uv run` / `fastapi dev` 使用 |
| `uv.lock` | uv 锁定的依赖版本 |
| `.env` | `DEEPSEEK_API_KEY`（权限 600，已在 .gitignore 中） |
| `run.sh` | 等价于 `uv sync && uv run fastapi dev` |

依赖里用的是 `fastapi[standard]`，它包含 `fastapi-cli`（即 `fastapi dev` 命令）和 `uvicorn[standard]`。

## 说明

- 单轮：`messages` 里只有当前这一条用户消息，不保存历史。
- 默认模型 `deepseek-flash`；改成 `deepseek-v4-pro` 就编辑 `.env` 里的 `DEFAULT_MODEL`。
- 8000 端口现在专门留给 `fastapi dev`；另一套完整示例（systemd 常驻、多轮、前端）在
  `~/projects/deepseek-fastapi`，跑在 8010。
