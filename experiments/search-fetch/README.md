# experiments · DeepSeek Search / Fetch API 试用

> **这不是课程单元。** 它是 2026-10-17 DeepSeek 新开放的两个接口的试用脚本，
> 不属于 Unit 1–90 的学习序列，随时可以整个目录删掉（`rm -rf experiments/`）。

## 为什么值得试

Unit 31–45（Research Agent）迟早要「联网查资料」。以前要么自己接 Bing / SerpAPI / Tavily
（多一个账号、多一份账单），要么自己写爬虫（处理不了反爬）。现在 DeepSeek 官方的
Search / Fetch 直接用同一个 Key、同一套 Bearer 鉴权，返回的是**已经清洗成 Markdown 的正文**。

| 接口 | Endpoint | 作用 |
| --- | --- | --- |
| Search | `POST /v1/tools/search` | 按 query 搜网页，返回 `summary` / `snippet` / 分数 / 权威性 |
| Fetch | `POST /v1/tools/fetch` | 抓单个 URL，返回 `title` + Markdown `content` |

计费：**5 元 / 1000 次成功请求**（失败、空结果、超时不计费）。
另外，2026-10-17 公告说：在 DeepSeek Harness / Claude Code / Codex 里通过服务端工具调用
访问 Search/Fetch，目前**未计费**，从 **2026-11-01 00:00（北京时间）** 起开始按次收费。

官方文档：<https://api-docs.deepseek.com/zh-cn/guides/search>

## 跑起来

脚本刻意写成**零依赖**（只用标准库 `urllib` / `json` / `argparse`），任何 `python3` 都能跑：

```bash
cd ~/ai-journey/experiments/search-fetch

python3 demo.py --selfcheck                      # 离线：11 项断言，不联网
python3 demo.py -q "DeepSeek V4.1 Flash" --count 3 --dry-run  # 只打印请求
python3 demo.py -q "DeepSeek V4.1 Flash" --fetch-top --show-raw \
    --env-file ../../unit03-cli-chat/.env         # 真调用（约 ¥0.01）
```

Key 的取法：`--env-file` → 从当前目录往上找 `.env` → 环境变量 `DEEPSEEK_API_KEY`。
原始响应一律存 `logs/`（根 `.gitignore` 已忽略 `logs/`）。

## 实测（2026-10-23 18:16，空闲时段）

```
🔎 query：DeepSeek V4.1-Flash-1017 上下文 1M 思考模式    命中 3 条
[1] ★★ 2.06 ⭐ DeepSeek V4.1 Flash API — 價格與基準 · OrcaRouter
[2] ★★ 1.94 ⭐ 模型 & 价格（GitHub 镜像）
[3] ★★ 1.89 ⭐ Cloudflare Docs changelog
耗时 436ms

📄 Fetch：https://www.orcarouter.ai/zh-TW/models/deepseek/deepseek-v4.1-flash
正文 12954 字符 · 耗时 11.7s
```

几点观察，写 Research Agent 时用得上：

1. **Search 很快**（亚秒级），**Fetch 慢**（10s 级）—— 所以「先搜 5 条，再决定抓哪条」比「全抓」划算得多。
2. `rerank_score >= 1.5` 是官方说的「优质结果」线，`authority >= 3` 是「极权威」。做引用列表时按这两个阈值过滤，比拿 `summary` 硬塞进 prompt 靠谱。
3. 结果字段会缺（`date` / `site_name` / `title` 都可能是 `null`），解析必须给默认值 —— 这一点 `parse_results()` 里已经处理。
4. `need_content=true` 会显著变大，能不传就不传；需要正文时单独走 Fetch，便于按条数控制成本。

## 文件

| 文件 | 说明 |
| --- | --- |
| `demo.py` | 全部代码（约 330 行，零依赖） |
| `logs/` | 原始 JSON 响应（不提交） |
