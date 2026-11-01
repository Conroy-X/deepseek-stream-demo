# Unit 20 · 基础 RAG

> 检索到的才有资格被说出口；没检索到就拒答，比编一个像样的答案便宜一百倍。

**完成标准**：同一套代码，不配 key 也能跑出带 `[n]` 的答案；问题无关时返回固定拒答；
每次问答都能在 trace 里查到「当时检索到什么、prompt 长什么样、答了什么」。

## 快速开始

```bash
cd ~/ai-journey/unit20-basic-rag
uv sync
cp .env.example .env          # 不配 key 也行，默认走离线后端
uv run uvicorn main:app --reload --port 8020
```

打开 <http://127.0.0.1:8020/>：提问 → 答案里带 `[n]`，右侧上下文卡片，底部可折叠的 prompt。

> 端口 **8020**。首次提问会建 `data/traces.db`。

## 自检

```bash
uv run python main.py
```

64 项，全离线：切句、上下文编号、prompt 约束、离线答案逐字来自原文、无关问题拒答、
阈值可调、空库、trace 落盘往返、API 响应解析（假 JSON，不联网）、参数校验、HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `rag.py` | 拼上下文/prompt、两种生成后端、拒答、`TraceStore` |
| `retriever.py` | 16 段语料 + 余弦检索（接口形状对齐 Unit 19 的 store） |
| `main.py` | `/ask` `/trace/{id}` `/traces` + 64 项自检 |
| `embedding.py` | 从 Unit 15 复制 |
| `.env.example` | OFFLINE / API 两种模式的全部开关 |
| `static/index.html` | 提问 + 答案（带 `[n]`）+ 上下文卡片 + 可折叠 prompt |

## 两条生成路径

| | extractive（默认） | api（配了 key） |
| --- | --- | --- |
| 做法 | 从**最高分那一段**摘 1–2 句原句 | `POST /chat/completions`，`temperature=0` |
| 成本 | 0 | 按 token 计费 |
| 会不会编 | 不会（逐字来自原文） | 会，所以更需要 Citation |
| 缺点 | 只会摘抄，问「综合来看」就废 | 要网、要钱、有延迟 |

模式自动判断：有 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 走 api，否则 extractive。
`POST /ask` 也可以显式传 `mode=extractive|api`。

## 实测（16 段语料，阈值 0.15）

| 问题 | 最高分 | 结果 |
| --- | ---: | --- |
| 一线城市住宿标准 | 0.384 | 正常回答 |
| 回滚后先检查什么 | 0.378 | 正常回答 |
| 退款多久到账 | 0.219 | 正常回答 |
| 年假要提前几天申请 | 0.203 | 正常回答 |
| 账号会被锁定多久 | 0.182 | 正常回答 |
| 公司年会什么时候开 | 0.144 | 拒答（0.144 < 0.15） |
| 账号锁定多久 | 0.143 | 拒答（**换个说法就掉出去**） |
| 今天天气怎么样 | 0.131 | 拒答 |
| 怎么给猫剪指甲 | 0.061 | 拒答 |

**相关的 0.20、无关的 0.13 —— 余量只有 0.07。** 这是本地哈希向量的真实水平，
也是阈值必须扫出来、不能抄别人 0.8 的原因。`账号锁定多久`（0.143）被拒是词表的天花板，
不是逻辑 bug：换真 embedding，同义改写通常能回到 0.5 以上。

## 无依据拒答怎么实现

阈值判定放在**生成之前**，不是写进 prompt 让模型自己判断：

```python
hits = retriever.search(q, top_k=top_k)
if not hits or hits[0].score < threshold:
    return REFUSAL          # 固定常量，可断言、可统计
answer = generate(prompt)   # 只有过了阈值才花这笔钱
```

两个好处：拒答可测（`answer == REFUSAL`）、拒答免费（不调模型就知道不该答）。
交给模型「不确定就说不知道」，等于把可测的东西变成不可测 —— 而且它经常不说。

## trace 里有什么

`data/traces.db` 每条记录：`trace_id / ts / question / mode / threshold / refused / hits / prompt / answer / usage / elapsed_ms`。

- `hits` 存 JSON：拒答也要能复盘「当时检索到什么、分数多低」
- `prompt` 存全文：出问题时第一件事是看真正发出去的东西，而不是看代码
- `usage`：api 模式记 `prompt_tokens / completion_tokens`，extractive 记字符数

没这条记录，你永远分不清「没检索到」和「模型答歪了」。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 当前模式、语料规模、是否配了 key、trace 条数、拒答话术 |
| POST | `/ask` | `{question, top_k, threshold, mode?}` → 答案 + hits + prompt + trace_id |
| GET | `/trace/{trace_id}` | 一条完整记录（含 prompt 与 usage） |
| GET | `/traces?limit=` | 最近 N 条 |

## 常见坑

- **跨段拼句子**：每个命中段各挑一句拼起来，会得到「住宿标准…[1] 超时未到账…[2]」这种胡说。
  规则版只从第一名那段取句，跨段归纳是模型的事。
- **threshold 当正确率**：0.15 只是这份语料 + 这个 embedder 的拐点，换模型必须重扫。
- **上下文编号事后重排**：`[n]` 必须等于 hits 的顺序，重排一次引用就全指错。
- **把拒答交给模型**：它会在没有依据时照样给你一段通顺的话。
- **不存 prompt**：只存 question/answer，出了问题无从复现。

## 验收清单

- [ ] `uv run python main.py` → 64 通过 / 0 失败
- [ ] 不配 key 也能提问，答案里带 `[1]`
- [ ] 阈值拉到 0.3，相关问题变成拒答；拉回 0，无关问题也说起了话
- [ ] `/traces` 里能看到刚才两次问答，`refused` 分别是 0 和 1
- [ ] 点开页面底部折叠区，prompt 里确实有 `[1] (handbook.md 第 1 页) …`
- [ ] 能说出为什么阈值判定要放在生成之前

## 下一单元

Unit 21 拿这里的 `[n]` 做校验：编号越界、覆盖率、引用的片段在来源里找不到（伪造引用），
并把 `[n]` 渲染成可点击的安全 HTML。
