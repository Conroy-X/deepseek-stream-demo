# Unit 50 · 自定义 Tool

> 工具设计的第一原则：模型只看得到 `name` / `description` / `input_schema` 这三样东西。
> 你没写进描述里的，它就不可能知道。

**完成标准**：能说出纯函数 / 有状态 / 有副作用三类工具在 schema 上的差别；
能让一个非法参数返回「中文错误 + `is_error=false`」而不是让整个调用炸掉；
能在不看代码的情况下，只读 `tools/list` 的输出就知道每个工具该怎么调。

## 快速开始

```bash
cd ~/ai-journey/unit50-useful-tools
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8050
```

打开 http://127.0.0.1:8050/ 。左边选工具，右边的表单是**按 `input_schema` 现场生成**的 ——
必填项标红，`type: number` 会转成数字再发出去。点「调用」才会真的起 stdio 子进程。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：65 通过 / 0 失败        （约 4.9 秒，连跑两次都是 0 失败）
```

八组：纯函数、CSV 状态、握手与工具清单、成功路径、工具自兜的错误、副作用、框架层失败、HTTP 层。
第 3 组开始是真的 `stdio_client` 起子进程、`initialize` 握手、`call_tool` —— 不是 mock。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `server.py` | `MCPServer` + 4 个工具 + 它们背后的纯函数（`fx_convert` / `convert_value` / `compute_stats`） |
| `client.py` | `MCPClient`（connect / list_tools / call_tool / close）+ 同步包装 `fetch_catalog` / `invoke` |
| `main.py` | 5 个接口 + 65 条自检 |
| `static/index.html` | 实验台：工具选择器、schema 生成的表单、`is_error` 与 `content[0].text` |
| `data/sales.csv` | 20 行销售数据，`server.py` 首次导入时幂等生成 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 工具数 | 4（`fx_rate` / `unit_convert` / `csv_stats` / `append_note`） |
| 握手 serverInfo | `unit50-tools v0.1.0`，协议 `2025-11-25` |
| `100 USD → CNY` | `100 USD = 712.00 CNY（1 USD = 7.1200 CNY）` |
| `100 c → f` | `100 c = 212 f（temperature）` |
| `csv_stats(qty)` | `列 qty：rows=20 sum=97 mean=4.85 min=1 max=15` |
| 单次 HTTP 调用耗时 | 0.75 s（起进程 0.5 s + 握手 + 调用 + 关闭） |
| 一次 `tools/list` | 4 个工具合计 2566 bytes，`description` 平均 172 字（最长的 `fx_rate` 769 bytes） |
| `GET /` 页面 | 10771 bytes，10 ms |
| 自检总耗时 | 4.9 s（含 5 次子进程往返） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 服务端身份、协议版本、工具数、CSV 行数、notes.log 行数 |
| GET | `/api/tools` | 工具清单（`description` + `input_schema`），前端据此生成表单 |
| GET | `/api/samples` | 6 个可点的样例参数 |
| POST | `/api/call` | `{name, arguments}` → `{is_error, text, blocks, structured}` |
| GET | `/` | 实验台页面 |

## 常见坑

1. **`description` 写成给人看的文档**。模型读的是它，不是你的 README。必须写清：
   什么时候该用、参数是什么单位、返回值长什么样。`unit_convert` 的描述里明写了
   「不带数字的单位符号（例如 `km`）」，否则模型会传 `"3km"`。
2. **参数类型写 `str` 图省事**。`amount: float` 写错类型时，pydantic 会在服务端拦下来
   并返回 `is_error=true` + `Error executing tool ...`；写成 `str` 你就得在函数体里自己解析，
   而且模型完全不知道该传数字。**schema 是你的第一道校验。**
3. **以为工具抛异常模型能看到原因**。实测 `raise RuntimeError("内部炸了")` 之后，
   客户端拿到的文本只有 `Error executing tool boom` —— 真实原因只进服务端 stderr。
4. **所有错误都往外抛**。非法单位这种「用户能改」的错误，应该 `return "错误：..."`，
   保持 `is_error=false`。两者取舍见 notes 第四节。
5. **有副作用的工具没在描述里声明**。`append_note` 的描述第一句就是「有副作用，重复调用会写多行」。
   不写这句，Agent 会重试，日志里就多出三条一样的笔记。
6. **默认值不算必填**。`csv_stats(column, op="summary")` 的 `required` 只有 `["column"]` ——
   这点小事决定了模型会不会莫名其妙地传一个 `op` 进来。

## 验收清单

- [ ] `python main.py` 输出 `结果：65 通过 / 0 失败`，连跑两次都一样
- [ ] 页面上选 `unit_convert`，填 `3 / km / kg`，看到中文拒绝且 `is_error = false`
- [ ] 把 `value` 填成 `abc`，看到 `is_error = true` 且文本以 `Error executing tool` 开头
- [ ] 连点两次 `append_note`，`/health` 里的 `notes_log_lines` 加 2
- [ ] 只读 `/api/tools` 的 `input_schema`，能说出每个字段该填什么
- [ ] 能解释：为什么非法单位不该抛异常

## 下一单元

Unit 51 · Resource —— 同样是「给模型提供数据」，为什么有的东西该做成 tool，
有的必须做成 resource；以及模板 resource 的占位符到底算不算「参数」。
