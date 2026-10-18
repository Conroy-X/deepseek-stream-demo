# Unit 5 · 结构化输出：JSON Schema / JSON Mode

> 今日必须完成：从一段自由文本简历里，稳定抽取 8 个字段的 JSON。
> 昨天的教训延续：光"答对"没用，要**程序能消费**——今天就是把"能解析"升级成"能保证"。


> **关于运行方式**：这些脚本依赖 venv 里的包。
> 先 `source .venv/bin/activate`，或直接用 `.venv/bin/python xxx.py`。
> 用系统 `python3` 会报 `ModuleNotFoundError`。

---

## 一、今日节奏（60+30 分钟）

| 时段 | 任务 | 对应文件 |
| --- | --- | --- |
| A 10min | 读 DeepSeek JSON Output 文档（重点：**必须提示模型输出 JSON** 这个坑） | 见资料 |
| B 60min | ① `models.py` 定义 Resume（8 字段）② `extract.py` 三种模式对比 ③ 跑 10 轮测通过率 | 本目录 |
| C 10min | 对抗实验：喂非简历文本，看模型怎么失败、校验怎么拦 | `--adversarial` |
| D 30min | 选做：校验失败自动重试（错误信息喂回模型） | `retry_extract.py` |

---

## 二、必读资料

| 资料 | 读什么 |
| --- | --- |
| [DeepSeek JSON Output 官方文档](https://api-docs.deepseek.com/zh-cn/guides/json_mode) | json_mode 的两个前置条件（今天最大的坑） |
| [Pydantic v2 官方文档](https://docs.pydantic.dev/latest/) | Concepts 里的 Validators、JSON Schema 两节 |
| [Pydantic JSON Schema 生成](https://docs.pydantic.dev/latest/concepts/json_schema/) | `model_json_schema()` 怎么变成 Prompt 的一部分 |
| [OpenAI Structured Outputs 指南](https://platform.openai.com/docs/guides/structured-outputs) | 为什么要 json_schema 而不只是 json_object |
| [Instructor 库](https://python.useinstructor.com/) | 生产级的"重试闭环"，选做时对照手写版 |

---

## 三、快速开始

```bash
cd ~/ai-journey/unit05-structured
uv venv --python /usr/bin/python3 && source .venv/bin/activate
uv pip install -r requirements.txt
cp -n ../unit03-cli-chat/.env .env

# 离线自检：验证 Pydantic 模型与校验逻辑（不花钱）
python models.py

# 三种模式对比（3 次调用，约 ¥0.005）
python extract.py

# 完成标准：10 轮 schema 模式，至少 9 次通过
python extract.py --mode schema --rounds 10

# 对抗实验（C 段必做）
python extract.py --adversarial

# 选做：重试闭环
python retry_extract.py --dirty
```

---

## 四、三种模式——今天必须建立的认知

| 模式 | 做法 | 保证什么 | 不保证什么 |
| --- | --- | --- | --- |
| ① naive | 直接 `json.loads(输出)` | 无 | 什么都可能崩：```json 围栏、前后闲话、单引号 |
| ② json_mode | API 传 `response_format={"type":"json_object"}` | **语法上**是合法 JSON | 字段名对不对、类型对不对、缺不缺——都不管 |
| ③ schema | Prompt 里给字段规则 + JSON Schema，输出后 `Resume.model_validate_json()` | **字段级**正确（类型/范围/枚举/必填全查） | 内容幻觉（模型可以编一个"王小明"出来） |

**一句话结论：json_mode 管"是 JSON"，Pydantic 管"是对的 JSON"。两个都要。**

DeepSeek json_mode 的坑（官方文档明说的）：**必须在 Prompt 里显式要求输出 JSON**，
否则 API 会报 400。这就是"参数传了≠生效"的又一例——Unit 4 的情报在这里直接复用。

---

## 五、核心文件

| 文件 | 作用 |
| --- | --- |
| `models.py` | `Resume` Pydantic 模型（8 字段 + 枚举 + 范围校验 + 别名归一）+ 抽取用的 Prompt 三件套 |
| `extract.py` | 三种模式对比器 + 10 轮通过率统计 + 对抗输入 |
| `retry_extract.py` | 选做：ValidationError 喂回模型的重试闭环（手写 Instructor 核心） |
| `notes.md` | 今日笔记（含完成标准对照） |

---

## 六、完成标准 ✅（对齐 90 天计划）

- [ ] `python models.py` 自检通过（合法数据过、5 类非法数据被拦）
- [ ] 10 次抽取至少 9 次通过 Pydantic 校验（`--rounds 10`）
- [ ] 输出是真正的 `dict`/`Resume` 对象，不是字符串拼的 JSON（`type(r)` 自己看）
- [ ] 能解释「为什么不能直接 `json.loads` 模型的输出」（用 naive 模式的失败现场回答）
- [ ] 对抗实验跑过，理解"校验拦结构错误，拦不住内容幻觉"
- [ ] `notes.md` 填完并 commit

---

## 七、常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| json_mode 报 400 | 没在 Prompt 里说"输出 JSON"——官方前置条件，加上即可 |
| naive 模式 `Expecting value` | 输出带 ```json 围栏或闲话，`extract.py` 会打印原始输出给你看 |
| `extra_forbidden` 校验错 | 模型多吐了字段（好事！说明 forbid 在工作），Prompt 里强调"恰好 8 个字段" |
| 年限抽取成 "6-7 年" 字符串 | `float` 类型校验会拦；重试轮模型会自己改成 6.5 |
| 输出被截断 | JSON 比分类任务长，`max_tokens` 给到 3000（extract.py 已设） |

---

## 八、和 Unit 6 的衔接

- 今天拿到的是"一次性完整 JSON"；Unit 6 的流式输出里，JSON 是**一块块到**的——到时候要用「累积缓冲 + 收完再解析」的策略
- `retry_extract.py` 的"错误回喂"循环，Unit 13 做重试机制时升级成带退避的版本
- Resume 模型 Unit 11 进 PostgreSQL 时直接变成表结构（Pydantic → SQLAlchemy 映射）

---

## 九、看原始响应（重要）

只看到「解析后的结果」是不够的——你无法判断模型到底吐了什么、解析器做了什么。
所以每次调用都会**把原始响应完整存到 `logs/`**，并可以打印到终端。

### 两层日志

| 文件 | 由谁产生 | 内容 |
| --- | --- | --- |
| `logs/*-call.json` | `llm.py` 每次调用自动写 | **完整 HTTP 响应体**（id/choices/usage/…）+ 响应头 + 请求体 + 元数据 |
| `logs/*-extract.json` | `extract.py` 每次抽取自动写 | **模型原始输出字符串** + 解析结果 + 输入文本 + 元数据 |

### 终端打印

```bash
# 每次抽取都打印「原始输出 → 解析步骤 → 解析结果」三段
python extract.py --mode schema --show-raw

# 不带 --show-raw 时，解析失败也会自动打印原始输出，方便排错
```

输出长这样：

```
──────────── schema 的原始响应 ────────────
╭─── 模型返回的原始字符串（未经任何处理） ───╮
│ {"name":"张三","email":"zhangsan@example.com",... │
╰──────────────────────────────────────────╯
─────────────── 解析过程 ───────────────
  ① 剥离 markdown 围栏（如果有）
  ② Resume.model_validate_json(raw) —— 一次完成「JSON 解析 + 类型校验 + 字段校验」
  ③ 校验内容：必填/类型/枚举/范围/正则/多余字段
─────────────── 解析结果 ───────────────
╭─── Resume 对象（已校验，转回 dict） ───╮
│ { "name": "张三", ... }                │
╰────────────────────────────────────────╯
```

### 在代码里访问

```python
raw = client.chat_one(...)          # 模型输出的原始字符串
client.meta.text                    # 同上（原始，未经解析）
client.meta.raw_response            # 完整响应 JSON（含 usage、finish_reason）
client.meta.raw_headers             # 响应头（含 x-ds-trace-id）
client.meta.request                 # 发出去的请求体
client.meta.saved_path              # 本次存盘位置
```

### 关闭存盘

```bash
LLM_SAVE_RAW=0 python extract.py --mode schema
```

`logs/` 已在 `.gitignore` 里，不会提交。想留档就挑一份复制到 `docs/`。

### 一个值得看的细节

打开任意 `*-call.json`，能直接看到 DeepSeek 返回的缓存命中情况：

```json
"prompt_cache_hit_tokens": 1408,
"prompt_cache_miss_tokens": 186
```

同一个 System Prompt 反复调用时 `cache_hit` 会很高——这是**提示词缓存**在省钱，
也是 Unit 61 成本优化要利用的机制。只看解析结果永远发现不了这个。
