# Unit 32 笔记 · 一份契约，两个读者

> schema 不是「给模型看的文档」，它是模型和你的代码之间的**接口协议**。手写两份，就等着漂移。

## 本次做了什么

- `schema.py`：三个 Pydantic v2 参数模型、`flatten_schema()`（`$defs`+`$ref` 内联、剥 `title`）、`validate()`（错误带字段路径）、`schema_diff()`（递归比较），以及一份故意过期的手写 `LEGACY_SCHEMA` 和它的手写校验器。
- `main.py`：`OfflineModel`（会把 `shipped` 翻成「已发货」的假模型）+ 6 个接口 + 57 条离线自检。
- `static/index.html`：选工具看 schema、贴 arguments 看错误路径、一键跑漂移报告。

## 一、生成出来的 schema 长什么样

`SearchOrdersArgs.model_json_schema()` 摊平之后（654 字节）：

```json
{"type":"object","additionalProperties":false,
 "properties":{"keyword":{"type":"string","minLength":1,"maxLength":40,"description":"…"},
               "status":{"type":"string","enum":["pending","paid","shipped","refunded"],"default":"pending"},
               "limit":{"type":"integer","minimum":1,"maximum":50,"default":10}},
 "required":["keyword"]}
```

三件事自动成立，而且只有一份定义：`required` 只含没有默认值的字段（`limit`/`status` 有 `default` 就不进去）；`enum` 来自 `Literal`、`minimum/maximum` 来自 `Field(ge=,le=)`、`pattern` 来自 `Field(pattern=)`；`additionalProperties:false` 来自 `ConfigDict(extra="forbid")`。改模型 → schema 和校验器同时变，没有魔法。

## 二、摊平：`$defs` 和 `$ref` 是给别人看的，不是给模型看的

带一个嵌套模型 `MeetingRoom` 时，Pydantic 输出 `{"$defs":{"MeetingRoom":{...}}, "properties":{"room":{"$ref":"#/$defs/MeetingRoom"}}}`。实测：原始 **1185 字节 / 1 个 `$ref`**，摊平 + 剥 `title` 后 **852 字节 / 0 个 `$ref`**，小 28%。

OpenAI 官方能解析 `$ref`，兼容网关不一定；而且模型看到 `#/$defs/MeetingRoom` 这种指针，经常不理解它是「去上面找定义」，干脆漏填 `room` 或者编一个 `{"building":"某楼"}`。
`flatten_schema()` 递归替换 `$ref`，同时保留 `$ref` 旁边的 `description` —— Pydantic 会把字段注释挂在那里，直接替换会把它丢掉，这是实现里最容易漏的一行（自检里有一条专门盯它）。循环引用直接报错，不硬展开。

## 三、错误要有路径，否则模型学不会

`validate()` 返回的不是一句「参数不合法」，而是：

```
status       literal_error       Input should be 'pending', 'paid', 'shipped' or 'refunded'
room.floor   less_than_equal     Input should be less than or equal to 30
admin        extra_forbidden     Extra inputs are not permitted
```

路径来自 `ValidationError.errors()[i]["loc"]`，元组里的数字下标渲染成 `[0]`、字段名渲染成 `.field`。收益在下一轮对话才显出来：把这三行原样回填给模型，它下一次能自己改对；只说「参数错误」，它会换个姿势再错一次。

## 四、模型真的会填错，而且错得很人性

离线模型的 `_decide()` 做了一件很真实的事：看到「已发货的订单」，就把 `status` 填成 `"已发货"`。不是它笨 —— 是 `description` 里写着 `shipped=已发货`，它就理解成「这个字段的值可以是『已发货』」。

**中文解释写进 description 是有代价的**：模型会把它当成取值候选。稳妥写法是枚举值单独一行、明确说「只能填英文枚举值，不要翻译」，再把这句话也写进 `system`（本单元的 `SYSTEM` 就是这么写的）。即便如此它还是会翻 —— 所以要校验兜底，不能只靠提示词。

## 五、漂移检测：12 处差异，一处都不会自己报错

`LEGACY_SCHEMA` 是「半年前手写的那份」，`legacy_validate()` 是「另一个同事手写的 if/else」。`schema_diff()` 递归比较，实测 **12 处差异**，关键三处：

| 差异路径 | 生成 | 手写 |
| --- | --- | --- |
| `properties.status.enum` | 4 个（含 `refunded`） | 3 个（漏了 `refunded`） |
| `required` | `["keyword"]` | `["keyword","limit"]` |
| `additionalProperties` | `false` | 不存在（= 允许任意字段） |

后果是**双向**的：`{"keyword":"耳机","status":"refunded"}` → 生成 schema 接受、手写校验器拒绝（合法请求被误杀）；`{"keyword":"耳机","admin":true}` → 生成 schema 拒绝、手写校验器放过（越权字段溜进去）。两条都写成了断言。

漂移最坏的地方不是它错，是它**只在特定输入下才错**。所以你需要一个能自动比对的函数，不能靠人眼 review 两份 JSON。

## 六、离线怎么测

`OfflineModel` 规则驱动（关键词 → 工具名 → 造 arguments），输出标准 OpenAI `message` 形状。`chat_api()` 保留 httpx 直连实现，配了 key 就切过去，但**自检永远走离线** —— 这一章验证的是 schema 形状、摊平正确性、错误路径定位，全是确定性逻辑。用真模型只会让 `[FAIL]` 随机出现。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 一份模型生成 schema 与校验器 | 三个工具都是 `model_json_schema()` + `model_validate()` 同源；57 条断言 |
| `additionalProperties` 生效 | 三个工具都 `false`；带 `admin` 的输入报 `extra_forbidden` |
| enum 表达正确 | `Literal` → `enum`，英文 4 值与中文 4 值都验过 |
| 可选字段默认值进 schema | `limit=10` / `status='pending'` 出现在 `default`，且不在 `required` |
| `$defs/$ref` 摊平 | 1185→852 字节、1→0 个 `$ref`，`room.floor` 仍在 |
| 错误路径可定位 | `status`、`room.floor`、`admin` 三种路径都有断言 |
| 漂移能被检出 | `schema_diff()` 报出 12 处；同一输入两边结论相反的断言 2 条 |
| 三个工具符合 OpenAI 格式 | 顶层字段不多不少、`type=function`、`parameters.type=object` |

## 一句话总结

**Schema 的价值不在「描述得清楚」，而在「只有一份」—— 两份一定会漂移，而漂移不会报错。**

## 面试追问

**Q：为什么不直接手写 JSON Schema？反正也就三个工具。**
A：因为校验逻辑是第二份。手写 schema 只是开始，你迟早还要写 `if not isinstance(args["limit"], int)`，那份代码和 schema 没有任何机制保证一致 —— 本单元那 12 处差异就是这么来的。Pydantic 的作用是把「写一遍」变成「只能写一遍」；代价是生成器带噪音（`title`、`anyOf`），需要一层后处理，但那层是**可以测的**。

**Q：`additionalProperties: false` 会不会太严？模型多传一个字段就失败。**
A：松一点是忽略多余字段，但代价是：某个工具实现用了 `**kwargs`，或下游把它转发给另一个服务，`{"city":"上海","admin":true}` 就悄悄变成一条越权请求。严格模式让这类错误在开发期暴露，放宽后它潜伏到线上。真需要兼容就显式声明一个 `extra: dict` 字段并记录，而不是关掉严格模式。

**Q：枚举能不能干脆用中文？**
A：能，中文模型往往更准 —— `refund_order.reason` 就是中文枚举，实测没出过错。副作用有两个：它和用户口语高度重合，模型容易把原话塞进去（「不想要了」不在枚举里）；下游代码要处理中文字面量，日志很别扭。折中方案是枚举用英文、`description` 里写「shipped=已发货」并禁止翻译 —— 而这恰恰是本单元演示的失败场景，所以要校验兜底。

**Q：schema 这么大，会不会很贵？**
A：会。三个工具 654+982+695 = 2331 字节，每轮对话都要发一遍；涨到 20 个工具就是十几 KB，而 `tools` 是**每轮重复**的，多轮 Agent 里成本按轮次线性涨。这才是 `title` 剥离（省 28%）和 Unit 35「工具越多路由越差」的真实背景 —— 工具不是越多越好，schema 要按 token 记账。
