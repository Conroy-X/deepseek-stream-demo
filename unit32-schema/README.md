# Unit 32 · 参数 Schema

> 模型读 `description` 决定传什么，代码读同一份 schema 决定收不收 —— 所以它只能有一份。

**完成标准**：能从 Pydantic 模型一把生成 OpenAI `tools` 数组，并且用它拦住模型填错的枚举值，报错精确到字段路径。

## 快速开始

```bash
cd ~/ai-journey/unit32-schema
uv sync
uv run uvicorn main:app --reload --port 8032
```

打开 <http://127.0.0.1:8032/>：左边选工具看生成的 schema，右边贴一段「模型返回的 arguments」，点校验看错误路径。默认走离线规则模型，不花钱。

## 自检

```bash
uv run python main.py
```

57 项，全离线：必填与默认值、enum 与边界、`additionalProperties`、`$defs/$ref` 摊平、
错误路径定位（含 `room.floor` 这种层级）、三个工具的 OpenAI 格式、
**手写 schema 与生成 schema 的漂移检测**、HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `schema.py` | 三个 Pydantic 参数模型 + `flatten_schema` + `validate` + 一份故意过期的 `LEGACY_SCHEMA` |
| `main.py` | `OfflineModel`（会把枚举翻成中文的假模型）+ 5 个接口 + 57 项自检 |
| `static/index.html` | 选工具看 schema、贴 arguments 看错误路径、漂移报告 |

## 实测

`model_json_schema()` → 摊平 → OpenAI 格式，三个工具的体积与必填项：

| 工具 | 参数个数 | 必填 | 生成后字节数 |
| --- | ---: | --- | ---: |
| `search_orders` | 3 | `keyword` | 654 |
| `schedule_meeting` | 4 | `title, start, attendees, room` | 982 |
| `refund_order` | 4 | `order_id, amount, reason` | 695 |

`schedule_meeting` 嵌套了 `MeetingRoom`，原始 schema 是 **1185 字节、带 1 个 `$ref` 和 1 个 `$defs`**；摊平并剥掉 `title` 后是 **852 字节、0 个 `$ref`**（小 28%）。省下的不是钱，是模型读不懂 `#/$defs/MeetingRoom` 的风险。

模型填错时校验器返回的东西（本单元的核心产出）：

| 输入 | 结果（path · type · msg 原文） |
| --- | --- |
| `{"keyword":"耳机","status":"已发货"}` | `status` · `literal_error` · `Input should be 'pending', 'paid', 'shipped' or 'refunded'` |
| `{"keyword":"耳机","admin":true}` | `admin` · `extra_forbidden` · `Extra inputs are not permitted` |
| `{"keyword":"耳机","limit":0}` | `limit` · `greater_than_equal` · `Input should be greater than or equal to 1` |
| `{"room":{"building":"A 座","floor":99}}` | `room.floor` · `less_than_equal` · `Input should be less than or equal to 30` |

路径带层级很关键：只说「参数不合法」，模型下次还是错；说「`room.floor` 超了 30」，它会改对。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 工具列表与当前模型 |
| GET | `/tools` | 完整 `tools` 数组（已摊平，前端也读它） |
| GET | `/schema/{name}` | 同一份契约的两个视图：`pydantic` 原样 + `openai` 摊平后 |
| POST | `/validate` | `{name, arguments}` → 校验结论 + 错误路径 + 执行结果 |
| GET | `/drift` | 手写 schema 与生成 schema 的差异清单、同一输入的相反结论 |
| POST | `/chat` | 一问一答，能看到 schema 在哪里拒绝了模型 |

## 常见坑

**1. `additionalProperties: false` 不会自动出现。** `BaseModel` 默认 `extra="ignore"`，模型多塞
`admin: true` 会被**默默丢掉**，你以为一切正常。每个模型都要写 `model_config = ConfigDict(extra="forbid")`。

**2. 嵌套模型一定产生 `$defs` + `$ref`。** OpenAI 官方接口能吃，但一堆兼容网关（老 vLLM、某些中转）
会报参数错误或把 `$ref` 当空对象。`flatten_schema()` 就是为了这个：内联 + 剥 `title`，顺带压掉 28%。

**3. `title` 是给文档看的，不是给模型看的。** 每个字段都带 `"title": "Keyword"`，三个工具能白烧几百字节，而且英文 title 和中文 description 并列时，模型有时会去猜 title 的意思。

**4. 默认值要进 schema，但别混进日志。** `properties.limit.default = 10` 是对的（模型才知道不传也行）；校验完要用 `model_dump(exclude_unset=True)`，否则日志里全是「模型传了 limit=10」，排障时会被骗。

**5. `str | None` 变成 `anyOf`，不是简单的 nullable。** Pydantic v2 输出
`{"anyOf":[{"type":"string"},{"type":"null"}]}`，有些网关不支持 `anyOf`；能不写可空就不写。

**6. 两份契约一定会漂移，而且是双向的。** 自检里 `LEGACY_SCHEMA` 少了 `refunded`、把 `limit` 当必填、
没有 `additionalProperties`，一共 **12 处差异**。同一份输入 `{"keyword":"耳机","status":"refunded"}`：
生成 schema **接受**、手写校验器 **拒绝**；反过来带 `admin` 的输入：手写 **放过**、生成 **拒绝**。
没有一处会自己报错，只会在某天以「线上退款接口 400」的形式出现。

## 验收清单

- [ ] `uv run python main.py` → 57 通过 / 0 失败
- [ ] 切三个工具，schema 都跟着变，且都看不到 `$ref`
- [ ] 点「枚举值翻成中文」，错误路径精确到 `status`
- [ ] 点「看嵌套字段的路径」，错误路径是 `room.floor` 而不是 `room`
- [ ] 点「跑漂移检测」，能看到同一输入两边结论相反
- [ ] 能说出为什么 `description` 不是注释，而是契约本身

## 下一单元

Unit 33 处理另一半：校验通过之后，工具结果要变成一条**合法消息**回填进 `messages`，并写一个能抓出「少回填一条」的校验器。
