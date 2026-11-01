# Unit 43 · 结构化要点提取（离线 OfflineModel）

> 从清洗后正文里提取 3-5 个要点。**提取和生成的区别** —— 提取的要点必须能在原文里逐字找到源句（可溯源）。

**完成标准**：能说清「提取」和「生成」的差别，能解释为什么每个要点都要带 `source_sentence`，
能讲清断言 `source_sentence in text` 为什么是反 hallucination 的第一道闸门。

## 快速开始

```bash
cd ~/ai-journey/unit43-extract
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8043
```

打开 <http://127.0.0.1:8043/>：贴一段正文 → 看要点列表 + 源句高亮。
点「加载空正文」看没有定义/数值/因果的正文怎么返回 count=0。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：57 通过 / 0 失败
```

八组：切句、提取要点、溯源、三类句子覆盖、空正文返回空、max_points 限制、
同句多 pattern 不重复、OfflineModel OpenAI 格式、schema 形状、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `extract.py` | `extract_key_points(text, schema)`、`OfflineModel`、`split_sentences()`、`verify_sourcing()`、`SCHEMA` |
| `main.py` | 4 个接口 + 57 条自检 |
| `static/index.html` | 贴正文 → 要点列表 + 源句高亮 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 规则识别的三类 | definition（是/称为/定义为） / number（数字+单位） / cause（因为/所以/导致） |
| 示例正文要点数 | 5 个（含定义、数值、因果） |
| 每个要点可溯源 | ✓（`source_sentence in text` 全部成立） |
| 空正文 | 返回 `[]`（不报错） |
| 没有三类句的正文 | 返回 `[]`（不强行凑数） |
| `max_points=1` | 截到 1 条 |
| `max_points=3` | 截到 3 条 |
| 同句命中多 pattern | 只算一次（去重） |
| OfflineModel 输出 | 标准 OpenAI message 格式（`role` / `content` / `points`） |
| `verify_sourcing` 字段 | `point` / `source_sentence` / `found` / `source_pos` |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端存活 + 模型名 + 默认要点数范围 |
| GET | `/schema` | 要点形状的 JSON schema（point + source_sentence） |
| GET | `/model` | OfflineModel 信息 |
| POST | `/extract` | `{text, min_points, max_points}` → `{count, points, sourcing, all_sourced}` |
| POST | `/extract/model` | 走 OfflineModel 路径（模拟真模型调用） |
| GET | `/` | 实验台页面 |

## 常见坑

1. **把生成伪装成提取。** 模型说「根据文档，reranker 提升了 30%」—— 文档里只说 0.62→0.81，
   没说 30%。看起来像「来自文档」实际是模型算出来的。`source_sentence in text` 这条断言把它钉死。
2. **`source_sentence` 是「大概意思」而不是 verbatim。** 模型喜欢复述，
   source_sentence 写成「reranker 把命中率提升了」但原文是「reranker 把 top-3 命中率从 0.62 拉到 0.81」
   —— `in` 判断立刻失败。要的就是这种失败，强制模型 / 规则不能改字。
3. **空正文抛异常。** 空正文是合法输入（unit 42 抓不到东西），返回 `[]` 让 Agent 跳过这个来源。
   抛异常会让 Agent 以为整个流程崩了。
4. **没有三类句的正文强行凑数。** 正文是「今天天气不错」—— 没有定义/数值/因果，
   应该返回 `[]` 而不是随便挑一句当要点。规则没命中就是没命中，不要硬塞。
5. **同句命中多 pattern 重复出现。** 「MCP 是一种协议，因为它叫协议，定义为规范」一句话命中三类，
   出现 3 次在要点列表里 —— 重复且难看。去重一次（按 source_sentence）。
6. **`schema` 参数收了不用。** 函数签名带 schema 是为了和真模型接口一致，
   但本单元规则版不强校验 schema —— 切真模型时 schema 会变成 LLM 的输出契约（function-calling）。
   参数预留是给 unit 45 切换用。

## 验收清单

- [ ] `python main.py` 输出 `结果：57 通过 / 0 失败`
- [ ] 页面贴示例正文，看到 3-5 个要点
- [ ] 每个要点都能溯源（源句在原文里，位置在 UI 上显示）
- [ ] 源句在原文里被黄色高亮
- [ ] 加载空正文看到「没有命中三类句」的提示
- [ ] 能说清「提取」和「生成」的本质差别

## 下一单元

Unit 44 · 多来源交叉比对 —— 3 个来源对同一主题的不同描述（含冲突），
怎么检测冲突、怎么列出共识、怎么把补充点单独标出来。
