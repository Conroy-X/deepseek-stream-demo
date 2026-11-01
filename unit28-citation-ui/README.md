# Unit 28 · Citation UI

> 让 `[1]` 不是装饰：点击后能定位原 chunk、跳到 PDF 对应页，并在跳不了的时候说得清楚。

**完成标准**：四种状态（正常 / 没页码 / 未知编号 / 恶意内容）在页面上各有各的画法，
而不是全塌缩成一句「加载失败」；连点三个角标，上屏的必须是最后一次点的那个。

## 快速开始

```bash
cd ~/ai-journey/unit28-citation-ui
uv sync
uv run uvicorn main:app --reload --port 8028
```

打开 http://127.0.0.1:8028/ 。离线。没有真 PDF：页码打在 5 页模拟文档上，
但**契约、跳转判定、页内定位、竞态丢弃全是真的**。

## 自检

```bash
uv run python main.py
# 结果：55 通过 / 0 失败
```

六组：契约校验、渲染与 XSS、跳转三态、竞态守卫、页内定位、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `contract.py` | 纯函数：`validate_answer_payload` / `render_answer` / `jump_target` / `locate_in_page` / `RaceGuard` / 4 个 fixture |
| `main.py` | FastAPI 路由 + 模拟文档 + 55 条自检 |
| `static/index.html` | 引用 UI 实验台：fixture 切换、角标点击、跳转降级、模拟原文页 |
| `notes.md` | 契约设计与踩坑笔记 |

## 实测

四份 fixture 的校验结果（`python main.py` 实跑）：

| fixture | 问题码 | renderable | 想演示什么 |
| --- | --- | --- | --- |
| `normal` | 无 | ✅ | 三条引用都能跳页 |
| `missing_page` | 无 | ✅ | `[2]` 没有页码 → `no_page`，能展开片段但不能跳 |
| `unknown_marker` | `UNKNOWN_MARKER`(warning) | ✅ | 正文写了 `[4]`，后端没这条数据 |
| `malicious` | `BAD_URL` + `DUP_ID`(error) | ❌ | `javascript:` 链接 + 重复 id 同时出现 |

跳转三态的实测分布：

| fixture | 引用 | page | status |
| --- | --- | --- | --- |
| `normal` | [1] / [2] / [3] | 3 / 7 / 2 | ok / ok / ok |
| `missing_page` | [1] / [2] / [3] | 3 / **None** / 2 | ok / **no_page** / ok |
| `malicious` | [1] / [1]（重复） | 3 / 7 | **bad_url** / ok |

模拟文档：`hr-handbook` 共 7 页，`refund-policy` 共 5 页；页内定位实测能命中
`[0, 19)` 这类区间并画 `<mark>`，命中不到时返回 `degraded: true` 但仍给整页文本。

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | fixture 列表、协议白名单、定位器版本 |
| GET | `/api/demo/answer?case=` | 一份响应 + `problems` + `renderable` + 渲染好的 `html` + `citations_view` |
| GET | `/api/jump?case=&n=` | 这条引用能不能跳（`ok` / `no_page` / `bad_url`） |
| GET | `/api/docs/{doc}/pages/{page}?snippet=` | 模拟 PDF 的一页 + 片段高亮区间 |
| GET | `/` | 实验台页面 |

## 常见坑

1. **把「没页码」当成「加载失败」**。这是两件事：前者是数据本身就没有页码（.md 来源），
   后者是请求挂了。前者应该展开片段并说明原因，后者才该报错重试。混在一起，用户会一直点重试。
2. **为没有页码的引用编一个默认页**。跳到第 1 页看起来「能用」，实际是把用户送到错误的位置，
   比明确说「这条没有页码」更糟 —— 前者他会以为系统坏了，后者他会以为自己记错了。
3. **只转义答案，忘了转义引用片段**。侧栏展示的 `c.text` 同样来自文档，同样可能含 `<`。
   本单元 `render_citation_text()` 单独做一次 `html.escape`，就是为了不让它成为漏网之鱼。
4. **协议白名单漏了 `data:`**。很多人记得拦 `javascript:`，忘了 `data:text/html,<script>…` ——
   粘进地址栏一样执行。白名单是 `http://`、`https://`、`/`，其余一律 `bad_url`。
5. **`isinstance(True, int)` 为真**。`"id": true` 会被当成 `1` 混过校验，然后和真正的 `[1]` 撞在一起。
   所以判 id 时必须先 `isinstance(cid, bool)` 排掉。
6. **连点三个角标，侧栏显示错的那条**。慢响应盖掉快响应，而且只在网络抖动时复现，几乎没法查。
   `RaceGuard` 给每次点击发一个自增 token，回调时比对 —— 不是最后一次就丢弃并计数。
7. **用前端隐藏按钮代替鉴权**。页面把「打开原文」置灰，不等于接口不能调。
   真正的鉴权必须在 `/api/docs/...` 里，页面只是不给入口。

## 验收清单

- [ ] `python main.py` 输出 `结果：55 通过 / 0 失败`
- [ ] 点 `normal` 的 [1]，右侧出现引用详情，能打开第 3 页并看到高亮
- [ ] 切换到 `missing_page`，[2] 显示「不能跳页」而不是「加载失败」，且**没有**跳转按钮
- [ ] 切换到 `unknown_marker`，[4] 是红色失效角标，点了有说明
- [ ] 切换到 `malicious`，答案里的 `<img>` 显示成文本，引用 [1] 的跳转被禁用
- [ ] 点「连点 [1][2][3]」，日志里能看到「响应晚到，已被丢弃」的计数上涨
- [ ] 能说出 `no_page` 和「加载失败」的区别，以及为什么前者不该给默认页

## 下一单元

Unit 29 · RAG 工程化与安全 —— 在同一套链路上加租户隔离、结构化日志、统一错误、成本核算和提示注入防护。
