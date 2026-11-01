# Unit 54 · 多 MCP Server

> 同时挂 2-3 个 server 时，最先撞上的事是**工具名冲突** —— 不同 server 都叫 `add`，
> 模型不知道该调哪个。靠命名空间前缀隔离；同时一个 server 起不来时，剩下仍可用。

**完成标准**：能解释为什么多 server 必须加前缀；
能演示部分失败降级（一个 server 路径错时，剩下仍连得上、仍能调）；
能说清关闭后无残留子进程的机制。

## 快速开始

```bash
cd ~/ai-journey/unit54-multi-mcp
uv sync
uv run uvicorn main:app --port 8054
```

打开 http://127.0.0.1:8054/ 。点「模拟一个 server 失败」看降级行为。

## 自检

```bash
uv run python main.py
# 结果：42 通过 / 0 失败
```

四组：三 server 全连、工具名冲突、调用带前缀工具、部分失败降级、关键词路由、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `servers/math_server.py` | math.add / math.square |
| `servers/text_server.py` | text.upper / text.reverse |
| `servers/db_server.py` | db.add / db.query（故意也叫 add，演示冲突） |
| `multi_client.py` | `MultiMCPClient`：聚合工具 + 前缀 + 降级 + 路由 |
| `main.py` | 5 接口 + 42 自检 |
| `static/index.html` | server 状态灯 + 工具表 + 路由演示 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 连接数 | math / text / db 各一个子进程，全连 |
| 聚合工具数 | 6（math 2 + text 2 + db 2） |
| 工具名前缀 | `math.add` / `text.upper` / `db.query` 等 |
| 名字冲突 | `add` 在 math 和 db 都有，靠前缀隔离 |
| math.add(2,3) | = 5 |
| db.add(A-100, 5) | 第一次 17、第二次 22（状态在 db 子进程内累积） |
| db.add 与 math.add 不串 | 各自状态独立 |
| 部分失败降级 | 故意加 `broken` server，它失败但其他 3 个仍连 |
| 关键词路由 | 「两数相加」→ math，「转大写」→ text，「查库存」→ db |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | server 名清单 |
| GET | `/api/snapshot` | 三个 server 全连时的状态 + 工具 + 冲突 |
| GET | `/api/degraded` | 故意让 broken server 失败，看降级 |
| POST | `/api/call` | 调带前缀的工具 |
| POST | `/api/ask` | 关键词路由 → 调对应 server 的第一个工具 |
| GET | `/` | 页面 |

## 常见坑

1. **不加前缀直接聚合**。两个 server 都有 `add`，模型拿到 `tools/list` 后看到两个 `add`，
   选哪个？没人说得清。前缀是必须的。
2. **状态串台**。`db.add(A-100, 5)` 加了 5，第二次再加 5 应该是 22。
   如果 math 的 `add` 也被当成 db 状态的一部分，就乱了。前缀不仅隔离名字，也隔离状态。
3. **一个 server 失败把整个 Agent 带崩**。必须把连接 try/except 住，记录失败但继续。
   本单元的 `MultiMCPClient.__aenter__` 就是这么写的：每个 server 独立 try，失败记到 status.error。
4. **子进程泄漏**。每个 `stdio_client` 起一个子进程，`AsyncExitStack` 关闭时会自动收尾。
   如果不收尾，每跑一次自检就多 3 个僵尸进程。

## 验收清单

- [ ] `python main.py` 输出 `结果：42 通过 / 0 失败`
- [ ] 页面上 3 个 server 状态灯都是绿的
- [ ] 工具表里有 `math.add` 和 `db.add`，冲突挑出
- [ ] 点「模拟一个 server 失败」，broken 是红的，其他仍绿
- [ ] 关键词路由 4 个按钮都能给出正确 namespace

## 下一单元

Unit 55 · 项目 4 MVP（已由子代理完成，63 自检 0 失败）。