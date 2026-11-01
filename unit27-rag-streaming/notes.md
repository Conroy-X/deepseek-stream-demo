# Unit 27 笔记 · RAG Streaming

> 流式不是「让答案来得更快」，是「让用户更早看到第一个字，并且随时能停」。

## 本次做了什么

- `streaming.py`：SSE 编解码、token 估算、上下文预算装填、`AnswerStream`（meta → token* → done）、引用组装、答案拼接。
- `store.py`：SQLite 存 12 条语料 + 检索（`data/stream.db`）。
- `main.py`：4 个接口 + 51 条离线自检（0 失败），`extractive` 后端不需要 key。
- `static/index.html`：实验台（逐字渲染、事件时间线、首字延迟、故障注入、中止连接）。

## 一、帧格式：两个必须做对的细节

```python
payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
lines = [f"id: {event_id}", f"event: {event}", f"data: {payload}"]
frame = "\n".join(lines) + "\n\n"
```

- **空行分帧**。少了结尾那个空行，浏览器会把下一帧粘过来，`event:` 和 `data:` 就错位了。
- **`ensure_ascii=False`**。默认会把中文转成 `\u9000\u6b3e`，一帧里一半是转义码，带宽白烧三倍，人也没法直接读日志。
- 换行不是问题：`json.dumps` 会把正文里的 `\n` 转义成字面量 `\n`，所以帧里永远不会出现裸换行。

自检里专门测了「换行被 JSON 转义，不破坏帧」和「转义后仍能还原原文」这一对 —— 编码/解码必须互逆，否则前端显示的内容会被悄悄改掉。

## 二、token 估算：不用 tiktoken 的代价与收益

```python
estimate_tokens("退款多久到账")   # 中文按字计
```

为什么不用 `len(text)`：中英混排时字符数与 token 数能差三倍，按字符做预算，英文/代码段落必然超窗。
为什么不上 tiktoken：本单元的重点是流式协议，多一个重依赖会让「离线可跑」这件事变脆。

所以估算**偏保守**（宁多算不少算）：预算可能提前用完，但不会超窗。
真实项目里这一步应该换成 provider 的 `count_tokens`，本单元把它隔离在一个函数里，就是方便替换。

## 三、上下文预算：装不下要跳过，不能 `break`

```python
used = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(question)   # 系统提示词和问题先占位
for hit in hits:                                                     # 从高分到低分
    if used + t > max_context_tokens: dropped.append(...); continue
    selected.append(hit); used += t
```

`continue` 而不是 `break` 是这里的全部要点：一块 800 字的长块装不下，后面一块 100 字的短块
可能正好能回答这个问题。`break` 会把后面的短块一起扔掉，于是「明明有答案却拒答」。

实测：问「退款多久到账」，`top_k=4`、预算 1200，选中 4 块、丢弃 0 块，上下文占 **156 token**。

## 四、事件顺序是契约，不是建议

```
meta  →  token × N  →  done
                  ↘  error（替代 done）
```

三条实测出来的规则：

1. **引用只在 `meta` 发一次**。每条 token 都带引用的话，10 倍流量，前端还要重排侧栏。
   自检断言 `citations 只在 meta 里发一次`、`token 帧里不带引用字段`。
2. **`token` 顺序拼接必须等于完整答案**。所以流式生成器不能「先分组再打乱」，也不能在中间插入分隔符。
3. **`done` 必须发**，即使被截断。前端靠它收尾（关 loading、显示用量）。没有 `done` 的流，页面会一直转圈。

## 五、输出预算：先算再拼，不是先拼再退

```python
if estimate_tokens(self.answer + piece) > self.max_output_tokens:
    self.truncated = True
    break
self.answer += piece
```

顺序反了就会「超了再回退」，`usage.output_tokens` 可能超过上限 —— 而这正是钱算错的地方。

实测两档预算：

| `max_output_tokens` | 帧数 | 实际 output_tokens | finish_reason | truncated |
| ---: | ---: | ---: | --- | --- |
| 300 | 46 | 133 | `stop` | false |
| 40 | 16 | **40**（正好卡住） | `length` | true |

第二次的答案是完整答案的前缀，停在「· 」这种地方 —— 难看，但**是合法截断**：
前端知道 `truncated=true`，可以显示「继续生成」而不是假装这就是全文。

## 六、断连与故障：两种「没正常结束」

- **客户端断开**（用户点了中止 / 关页面）：服务端在 `async for` 里收到 `CancelledError` 或 `GeneratorExit`。
  此时**不能**发 `done`，也**不能**把这次请求记成成功。
  自检断言了「断连时不会把流标成完成」——真实事故就是这里记成了 200，于是「用户取消」在报表里变成了「答案已送达」。
- **生成中途失败**：头已经发出去了，HTTP 状态码改不了。只能发 `error` 事件：

```
data: {"code":"GENERATION_FAILED","message":"生成中断：RuntimeError","request_id":"req_…","retryable":true}
```

这就是必须定义 `error` 事件的全部理由：**200 的响应里也可能是一次失败**。
前端必须按事件判断成功与否，而不是按状态码。

## 七、前端：`EventSource` 发不了 POST

`EventSource` 只支持 GET，而问答请求要带 body（`question`、`top_k`、预算）。
所以页面用的是 `fetch` + `response.body.getReader()`，自己按 `\n\n` 切帧：

```js
buf += dec.decode(value, {stream: true});
let i; while ((i = buf.indexOf("\n\n")) >= 0) { handle(buf.slice(0, i)); buf = buf.slice(i + 2); }
```

两个容易忽略的点：`TextDecoder.decode(..., {stream: true})` 必须带 `stream`，
否则一个 UTF-8 汉字被拆在两个 chunk 里时会解出乱码；缓冲区要**累加**，不能假设每次 `read()` 正好是一帧。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 事件契约讲得清 | `meta → token* → done`，`error` 替代 `done`；自检 8 条覆盖顺序与内容 |
| 引用只发一次 | `meta.citations` 一次，`token` 只有 `text`；有断言 |
| 预算与截断正确 | 先算再拼，40 token 正好卡住，`finish_reason=length` |
| 断连可控 | `CancelledError` 不发 done、不记成功 |
| 故障收尾 | 200 响应里的 `error` 事件，带 `request_id` + `retryable` |
| 离线可自检 | 51 条断言，0 失败 |

## 一句话总结

**流式的协议契约比生成质量更容易出事**：帧怎么切、引用发几次、截断怎么收尾、断连算不算成功 —— 这四件事错了，用户看到的就是「转圈」和「半句话」。

## 面试追问

**Q：为什么不用 WebSocket？**
A：单向、一次性、无状态的场景，SSE 够用且更简单：走普通 HTTP，不用握手升级，不用自己实现重连，
nginx/网关的支持也更成熟。WebSocket 的优势在双向，而问答流不需要客户端中途发消息 ——
真要打断，断 TCP 就行（对应上文 `CancelledError`）。唯一要注意的是 SSE 默认只能 6 个并发连接/域，
生产上要么用 HTTP/2，要么按页面只开一条流。

**Q：首字延迟怎么降？**
A：三件事按收益排序：一是**先检索后生成**、检索阶段不发流式（没意义），但要给它单独的超时；
二是让模型尽快出第一个 token（不要在前面拼一大段「正在思考」的模板）；
三是前端不要等 `done` 才渲染，`meta` 一到就先把引用和骨架画出来。
本单元的 `meta` 就是为此设计的：引用在第一个字之前到达。

**Q：`truncated` 之后用户点「继续」怎么实现？**
A：把已生成的答案作为 assistant 消息回填，再追加一次生成请求，`max_output_tokens` 重置。
关键是**引用编号要接着上一轮的 `meta` 继续**，不能从 1 重新编号，否则同一份答案里会出现两个 `[1]`
指向不同来源。本单元没有实现续写，但事件契约里 `done.truncated` 就是为它留的钩子。

**Q：为什么不断言「最终答案等于某个固定字符串」？**
A：因为生成器是可替换的（`extractive` / 真实模型），断言固定文案会让换后端就红一片。
本单元断言的是**不变量**：token 拼接等于完整答案、引用编号连续、output_tokens 不超预算、截断是前缀。
数字会变，不变量不会。
