# Unit 5 笔记 · 结构化输出

> 边做边填。模拟面试的复习材料。

## 今天做了什么

-

## 三种模式对比（extract.py 的结果直接抄）

| 模式                  | 结果（n/10） | 失败原因（如果有） |
| --------------------- | ------------ | ------------------ |
| ① naive（直接 loads） | 10 / 10      | 没有               |
| ② json_mode           | 10 / 10      | 没有               |
| ③ schema + Pydantic   | 10 / 10      | 没有               |

### ⚠️ naive 的 10/10 是「假象」，别被通过率骗了

10 轮测试里 naive 和 json_mode 都是 10/10，看起来和 schema 一样稳。
但这**不代表 naive 安全**，原因有三：

**① System Prompt 在强行兜底**
`build_extract_messages()` 里的 System Prompt 已经写了"输出指定 JSON 结构"。
模型被 Prompt 约束着吐 JSON，`json.loads` 当然成功。
→ naive 测的不是"模型裸奔时的能力"，而是"模型 + 我们的 Prompt"。

**② 失败场景不在这个数据集里**（实测对照）

| 场景 | naive (`json.loads`) | `parse_fenced` |
| --- | --- | --- |
| 裸 JSON | ✅ 成功 | ✅ 成功 |
| 带 ```` ```json ```` 围栏 | ❌ 抛异常 | ✅ 成功 |
| 前后有闲聊 | ❌ 抛异常 | ❌ 抛异常 |
| 尾随逗号 `{"a":1,}` | ❌ 抛异常 | ❌ 抛异常 |

这些坑在当前 Prompt + 当前模型 + 当前输入下不触发。
换模型、改 Prompt、输入变长、模型开始"解释"时就会冒出来。

**③ 通过率高 ≠ 数据可用**
naive 那 10 次"成功"里，拿到的可能是 `name: null` 这类空值，
`json.loads` 完全不管，程序一路往下跑，最后在业务层才炸。

**判断标准应当是「失败时怎么失败」，而不是「成功了几次」：**
- naive：静默错讹（拿到 null 继续跑）→ **最危险**
- schema：快速失败（ValidationError 立刻抛，精确到字段）→ 可观测、可重试

## 对抗实验（喂了段打篮球吃火锅的文本）

三种模式的真实表现（`--adversarial --show-raw`，同一份输入）：

| 模式 | 结果 | 模型实际输出 |
| --- | --- | --- |
| ① naive | ✅ 1/1「成功」 | `{"name": null, "years_of_experience": null, "seniority": null, "skills": []}` |
| ② json_mode | ✅ 1/1「成功」 | `{"name": null, "years_of_experience": 0, "skills": [], ...}` |
| ③ schema | ❌ 0/1 失败 | `{"name": "", "years_of_experience": 0, "skills": [], "seniority": "junior", ...}` |

- **模型的行为**：没有编造具体人名，而是返回了"空壳"——name 为空、数组为空。
  但 `json_mode` 把 `years_of_experience` 填成了 `0`，`schema` 填了 `seniority: "junior"`，
  这已经是**无中生有的取值**（原文里根本没有这些信息）。

- **naive / json_mode 拦住了什么**：什么都没拦。
  - `naive` 只做 `json.loads`，`{"name": null}` 是合法 JSON，所以算"成功"
  - `json_mode` 也只保证是 JSON，同样放行
  - 这两个的"成功"是**假成功**：数据是空的/编的，程序却以为拿到了结果

- **schema 模式拦住了什么**：3 个字段级错误
  ```
  1. name              String should have at least 1 character      (string_too_short)
  2. skills            List should have at least 1 item, not 0     (too_short)
  3. work_experiences  List should have at least 1 item, not 0     (too_short)
  ```
  这是 `min_length=1` 在起作用——**空字符串和空列表都被挡在门外**。

- **没拦住什么（关键结论）**：
  Pydantic 只检查"形式合法"，拦不住"内容编造"。实测把 name 改成 `"王小明"`、
  再补一段虚构工作经历，结构完全合法，Pydantic **一路放行、零报错**。
  → 结构化校验解决的是"**能不能安全使用这个数据**"，不解决"**这个数据可不可信**"。
  → 抽取类任务要加**来源核验**（evidence / 原文定位），否则空壳和编造都能混过去。

## 遇到的报错 & 解决过程

| 报错 | 原因 | 怎么解决 |
| ---- | ---- | -------- |
|      |      |          |

## 我自己的话解释「为什么不能直接 json.loads 模型的输出」



当没有指定schema + pydantic 的时候 llm响应不一定是是JSON格式



## 我自己的话解释「json_mode 和 Pydantic 校验的分工」

> 提示：一个管语法层，一个管语义层。

- 一个保证输出的是JSON格式， 可以被正确解析
- 另一个保证输出的JSON格式符合预期， 但是也不能保证JSON值完全正确

## Token / 成本

- 今日累计：调用 ___ 次，约 ¥___

## 明天的第一件事

- Unit 6 Streaming：FastAPI `/chat/stream` + SSE，浏览器逐字输出（把 Day 2 的 main.py 和今天的 JSON 策略接起来）

---

## 一句话总结

三种模式在「**坏数据进来时谁先发现**」上完全不同：
`naive` 装聋作哑（null 也当成功）、`json_mode` 只管语法（是 JSON 就行）、
`schema` 当场报警（ValidationError 精确到字段）。
但三者都**不知道内容真假**——要治幻觉得靠 evidence 核验和评测集，不是靠 Pydantic。

## 面试可能的追问（自己先想）

1. **「你的抽取通过率 100%，怎么保证不是 Prompt 太强导致的？」**
   → 换抗干扰输入重测（对抗实验），并把「结构正确率」和「内容准确率」分开统计。

2. **「Pydantic 校验失败之后怎么办？」**
   → 见 `retry_extract.py`：把 ValidationError 原文塞回对话让模型修正，最多 N 轮，
   仍失败就明确报错，绝不带病通过。

3. **「为什么不用 response_format=json_object 就完事了？」**
   → `json_mode` 只保证 JSON 语法，不保证字段名/类型/范围。
   而且 DeepSeek 的 json_object 模式要求 Prompt 里**必须出现 "json" 字样**，
   否则会报错或返回非 JSON——这是官方文档特别标注的坑。
