# Unit 48 · 三大能力

> Tools / Resources / Prompts 不是三个并列的清单。真正的区别在**谁决定用它**：
> 模型自己判断 / 宿主挂载上下文 / 用户显式点一下。
> 分错了不会报错，只会用起来越来越别扭 —— 这才是最难发现的那类设计缺陷。

**完成标准**：能说出三条控制轴（模型 / 应用 / 用户）各自对应哪种能力；
能解释为什么「读一份文档」该做成 resource 而不是 tool；
能说明服务端没声明某个能力时客户端会收到什么、以及该据此做什么。

## 快速开始

```bash
cd ~/ai-journey/unit48-capabilities
uv sync
uv run uvicorn main:app --reload --port 8048
```

打开 http://127.0.0.1:8048/ 。四块：
控制轴表格、发帧台（11 个样例）、能力声明裁剪器、挑刺器。
「能力声明裁剪」那块最值得玩 —— 点掉 `resources`，看 `resources/list` 返回什么。

## 自检

```bash
uv run python main.py
# 结果：97 通过 / 0 失败
```

八组：控制轴、URI 模板、能力协商、Tools、Resources、Prompts、挑刺器、HTTP 层。
纯标准库 + fastapi，依然**没有 `mcp` 依赖**。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `capabilities.py` | `CONTROL_AXIS`、`UriTemplate`、`Tool`/`Resource`/`ResourceTemplate`/`Prompt`、`DemoServer`、`classify()` 挑刺器、11 个样例帧 |
| `main.py` | 7 个接口 + 97 条自检 |
| `static/index.html` | 能力对照台：控制轴、发帧、声明裁剪、挑刺、三种做法 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 挂载数量 | tools 3 / resources 2 / templates 1 / prompts 2 |
| 只声明 `tools` 时调 `resources/list` | `-32601 Method not found`，`data.declared = ["tools"]` |
| 只声明 `tools` 时调 `tools/list` | 正常返回 3 个 |
| 读不存在的资源 | `-32602`，`data` 同时给出 `known` 与 `templates` |
| 读未知模板参数 `doc://kb/unknown` | `-32602`，`data.known` 列出 3 个合法主题 |
| 提示词缺必填参数 | `-32602`，`data.args` 带回完整声明供客户端渲染表单 |
| resource 读两次 | 内容完全一致（只读性可断言） |
| 挑刺器 | 5 条样例中挑出 **4** 条，正常的写操作工具**不误报** |
| 只读工具预留库存 | 库存真的从 42 → 40，`count_items` 精确等于「40 件」 |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 已声明能力、能力表、各类数量 |
| GET | `/api/axis` | 控制轴三行（owner / trigger / side_effect / wrong_use） |
| GET | `/api/examples` | 11 个可点的帧样例 |
| POST | `/rpc` | 喂一帧，返回响应（通知 → 202） |
| POST | `/reset` | 重置库存等可变状态 |
| GET | `/api/caps/{declared}` | 只声明部分能力，探测三类 `list` 各自的返回 |
| GET | `/api/lint` | 5 条放错的能力，挑刺器给结论 |
| GET | `/api/three-ways` | 同一需求三种做法的对比 |
| GET | `/` | 对照台页面 |

## 常见坑

1. **把「读一份文档」做成 tool**。模型不知道这份文档存在，没有任何「该读了」的信号，
   于是它永远不调。resource 的价值就在这儿：**宿主**在 UI 里把它摆出来，
   用户勾选或自动挂载，模型看到的是现成的上下文，不需要「想到去拿」。
2. **把「取消订单」做成 resource**。resource 会被宿主自动挂载或预取，
   没有「我要执行」的确认动作。放进去等于绕过用户确认直接改状态。
3. **以为没声明能力 = 拒绝访问**。实测返回的是 `-32601 Method not found`。
   这个区别决定了客户端行为：收到 `-32601` 就**不显示**那一栏 UI；
   如果是权限问题，才需要提示「你没权限」。搞混了会做出「点了才知道不能用」的界面。
4. **模板混进 `resources/list`**。模板要单独用 `resources/templates/list` 列 ——
   客户端得知道有这个东西，才能让用户填参数。混在一起客户端没法区分「能直接读」和「要填参数」。
5. **`prompts/get` 返回字符串**。规范返回的是 `messages` 数组（`role` + `content`），
   因为一个模板可能产出多轮消息。返回字符串的 server 在很多宿主上直接不工作。
6. **注解自相矛盾**。`readOnlyHint: true` 和 `destructiveHint: true` 同时出现，
   挑刺器会抓出来。规范里这两个是互斥语义，写错会让宿主的确认策略失效。

## 验收清单

- [ ] `python main.py` 输出 `结果：97 通过 / 0 失败`
- [ ] 控制轴表格里「谁决定」三列分别是模型 / 应用 / 用户
- [ ] 点掉 `resources` 后，`resources/list` 那行变成红色 `-32601`
- [ ] 挑刺器里 `get_leave_doc` 被建议「改 resource」，`reserve_stock` 不被误报
- [ ] 发 ⑦ 号样例，看到 `doc://kb/expense` 走模板返回报销流程
- [ ] 发 ⑪ 号样例，看到 `-32602` 且 `data.args` 里有完整参数声明
- [ ] 能不看文档说出：为什么「读文档」是 resource 而「下单」是 tool

## 下一单元

Unit 49 · 第一个 Server —— 终于装 SDK。前面三章手写的协议层，现在变成十几个装饰器。
你会看到同一批帧，只是换了个写法；也会第一次踩到 SDK 2.x 的坑。
