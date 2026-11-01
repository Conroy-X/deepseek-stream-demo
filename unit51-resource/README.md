# Unit 51 · Resource

> 一个 tool 都没有，是本单元要证明的事。
> 「让模型能读到的数据」并不只有「给它一个查询工具」这一条路 ——
> static 挂载、template 按主题展开，是把上下文交给模型的两条更轻的通道。

**完成标准**：能说清 `resources/list` 与 `resources/templates/list` 为什么是两个接口；
能解释为什么「读文档」做成 resource 比做成 tool 更合适；
能说出 resource 侧没有任何「脱敏错误文本」通道带来的取舍。

## 快速开始

```bash
cd ~/ai-journey/unit51-resource
uv sync
uv run uvicorn main:app --port 8051
```

打开 http://127.0.0.1:8051/ 。左侧是 resource 列表（书的目录），
右侧是内容渲染。中间多带一个「路径遍历」输入框，用来证明 SDK 不会乱读文件。

## 自检

```bash
uv run python main.py
# 结果：45 通过 / 0 失败
# 起一次真实 stdio 子进程，连一次握手，跑十几次 read_resource
```

四组：文档本身、清单与读取、失败行为、HTTP 层。每条都起真子进程。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `server.py` | `MCPServer`：2 个固定 resource + 1 个模板 resource，**0 个 tool** |
| `client.py` | `MCPClient` 封装 + `fetch_catalog()` 同步包装 |
| `main.py` | 4 个接口 + 45 条自检 |
| `static/index.html` | 阅读器：左列清单 / 右侧渲染 / 路径遍历测试框 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 固定 resource | `doc://handbook/leave` (markdown) + `doc://changelog` (text/plain) |
| 模板 resource | `doc://kb/{topic}`，topic ∈ `deploy / onboarding / security` |
| `resources/list` 返回 | 2 条固定资源（不含模板） |
| `resources/templates/list` 返回 | 1 条模板，`uri_template = "doc://kb/{topic}"` |
| `list_topics()` 大小写无关 | `doc://kb/DEPLOY` 也能取到文档 |
| 读两次同一 URI | 内容完全一致（只读 / 幂等可断言） |
| 未知 topic | 客户端**抛 `MCPError`**，文本是 `Error creating resource from template` |
| 底层 `未知主题：xxx` | **不会**传到客户端（脱敏） |
| 路径遍历 `doc://kb/../etc/passwd` | 不会读到本地文件 |
| 工具数量 | 0（边界证据：服务端不必凑齐三类能力） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 身份、resource/template/tool 数量、可用 topic |
| GET | `/api/resources` | 同时给固定 resource 与 template 列表 |
| POST | `/api/read` | 按 URI 读；失败也 200，靠 `ok` 字段区分 |
| GET | `/` | 阅读器页面 |

## 常见坑

1. **把 `doc://kb/{topic}` 混进 `resources/list`**。SDK 会把它注册成「模板」，
   只能从 `resources/templates/list` 看到。客户端要**两个接口都拉**才能拿到完整目录。
2. **resource 失败时希望返回像 tool 的 `is_error=True` + 文本**。SDK 没有这条通道 ——
   resource loader 抛异常 → 客户端拿到的是脱敏后的 `MCPError`，
   连「哪个 topic 不存在」都看不到。这是 resource 不适合承载业务的核心证据。
3. **参数大小写敏感**。如果直接拿 `KB_TOPICS["Deploy"]` 会 KeyError。
   本单元在 loader 里做了 `.lower()`，并且断言 `doc://kb/DEPLOY` 能读到。
4. **以为路径里的 `..` 会被规范化**。SDK 不会，路由匹配是按模板字面量做的。
   本单元既验证模板不命中（不会读到 `/etc/passwd`），也写了一份纯函数 `fetch_kb` 用 `.lower()` 兜底。
5. **resource 想「校验权限」**。resource 没有参数通道就没有自己的决策权 ——
   权限放在 host（决定要不要把 uri 给模型读）、或者干脆做成 tool 走参数校验。

## 验收清单

- [ ] `python main.py` 输出 `结果：45 通过 / 0 失败`
- [ ] `/api/resources` 同时列出固定 resource 与 template，且 template 不混在固定列表里
- [ ] `doc://kb/DEPLOY` 大小写无关能读到「发布流程」
- [ ] `doc://kb/nope` 让客户端抛异常，且异常文本无底层原因
- [ ] 能不看文档说出：固定 resource 的 loader 函数不能有参数

## 下一单元

Unit 52 · Database Tool —— 暴露表结构和只读 SQL。
重点不是「写 SQL 工具」，是**「只读连接 vs 词法检查」**：一个能让写操作跑成，一个不能。