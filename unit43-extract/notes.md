# Unit 43 笔记 · 提取和生成的区别

> 把 hallucination 拦在源头：每个要点都要能逐字溯源到原文。规则版做得粗，但「可溯源」这条契约是真的。

## 本次做了什么

- `extract.py`：`extract_key_points(text, schema)` 规则识别三类句；`OfflineModel` 输出标准 OpenAI message；`verify_sourcing()` 校验 source_sentence 在原文里的位置。
- `main.py`：4 个接口 + 57 条自检。
- `static/index.html`：贴正文 → 要点列表 + 源句在原文里高亮（按 `source_pos` 倒序插入 span，避免位置错位）。

## 一、规则识别的三类句：覆盖 80% 的「值得记」

```python
DEFINITION_PATTERNS = [r"(?:是|叫作|称为|定义为|是指)"]
NUMBER_PATTERN = r"\d+(?:\.\d+)?\s*(?:%|个|ms|秒|天|倍|元|...)"
CAUSE_PATTERNS = [r"因为", r"所以", r"导致", r"从而", r"因此", r"由于"]
```

为什么这三类：定义句是「是什么」（最值得记）、数值句是「多少」（事实硬核）、
因果句是「为什么」（逻辑链）。剩下 20%（如「列表」「过程描述」）本单元不处理。

实测：示例正文 7 句话命中 5 句（定义 2 + 数值 1 + 因果 2），过滤 2 句。
`max_points=5` 截到 5 条全部可溯源。

## 二、可溯源：source_sentence 必须 verbatim 出现在原文里

```python
return [{"point": ..., "source_sentence": src} for ...]
# 断言：
assert all(p["source_sentence"] in text for p in pts)
```

`point` 可以是精炼版（截断、换标点），`source_sentence` 必须逐字一致。
这条契约挡住的是「生成伪装成提取」—— 模型说「文档说 reranker 提升 30%」，
原文里只说「0.62 拉到 0.81」，30% 是模型算的，不是文档说的。

`verify_sourcing()` 返回 `source_pos`（首字符位置），UI 用这个位置在原文里
插 `<span class="hl">` 高亮。位置必须是真位置，不能估算。

## 三、切句：长度阈值挡住「嗯。啊。哦。」

```python
SENTENCE_MIN_LEN = 12
def split_sentences(text):
    parts = re.split(r"[。！？!?；;\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= SENTENCE_MIN_LEN]
```

短句过滤是必要的：fixture 里 page1 末尾的「如下。」这种短句不该成为要点候选。
阈值 12 是经验值 —— 中文完整陈述基本 >= 12 字符。
自检里 max_points 那段故意写了「苹果是一种常见的水果，颜色多为红色或绿色。」
（20+ 字符）才被切进候选。

## 四、同句多 pattern 去重

「MCP 是一种协议，因为它叫协议，定义为规范」一句话命中三类 pattern。
不去重会出现 3 次在要点列表里，全指着同一句话 —— 重复且难看。
按 source_sentence 去重一次。实测命中数量 = 1。

## 五、空正文返回空列表，没三类句也返回空列表

两种「空」分开：文本本身空（`extract_key_points("", SCHEMA) == []`）
和文本非空但没三类句（`extract_key_points("今天天气不错", SCHEMA) == []`）。
都不抛异常。Agent 处理多来源时某个来源空了应该跳过而不是整个崩。

## 六、OfflineModel 输出标准 OpenAI message 格式

```python
def complete(self, messages):
    return {"role": "assistant", "content": _format_points_as_text(points), "points": points}
```

`content` 是文本版（给前端展示），`points` 是结构化版（给下游 Agent 直接用）。
带两个版本是因为：真模型只回 `content` 字符串，结构化要走 function-calling；
本单元把两个都塞进 message，让 unit 45 的 Agent 两个路径都能接。

实测：`OfflineModel().complete([...])` 返回的 `points` 全部可溯源 ——
规则版天然 verbatim，但切真模型后这条不一定成立（真模型可能改字），
这就是为什么 `verify_sourcing()` 是必须的运行时检查。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| `extract_key_points(text, schema)` | 规则识别三类句，返回 `[{point, source_sentence}]` |
| 3-5 个要点 | max_points=5 截断，min_points 不强行凑数 |
| 每个要点 source_sentence 在原文里 | 自检逐条断言，all_sourced=True |
| 空正文返回 `[]` | 4 种空场景都返回 `[]` |
| schema 形状 | type=object / points array / additionalProperties false |
| HTTP 接口真能跑 | 6 个接口都 200 |
| 自检 ≥ 25 条 | 57 条，连跑两次 0 失败 |

## 一句话总结

**反 hallucination 的第一道闸门不是更聪明的模型，是「要点能不能在原文里逐字找到」这条断言。**

## 面试追问

**Q：规则版只覆盖三类句，剩下 20% 怎么办？**
A：教学范围控制。生产用真 LLM + function-calling，但 `source_sentence in text` 这条契约不变 —— LLM 输出后用同样的 verify_sourcing 校验，找不到就丢掉或重生成。规则版让自检离线可复现，规则粗没关系，重点是契约。

**Q：source_sentence 用 `in` 判断会不会太严？**
A：会，但严是好事。模型哪怕改一个标点都失败 —— 失败就丢这条要点或重生成。宽松的话（语义相似度），hallucination 就漏过去。生产可以「相似度 > 0.95」做软判断，但严格 `in` 是基础断言。

**Q：为什么不直接调 OpenAI？**
A：本单元重点是「提取和生成的区别」+「可溯源契约」，不是「调模型」。规则版让契约可见。切真模型时，契约通过 `verify_sourcing()` 运行时检查保证，不依赖模型自觉。

**Q：`max_points=1` 截断按什么顺序？**
A：优先级 `definition > number > cause`。业务直觉是定义最有价值（告诉你「是什么」），数值次之，因果最后。生产可以让 LLM 自己排序，或用句子位置（开头/结尾通常更重要）。
