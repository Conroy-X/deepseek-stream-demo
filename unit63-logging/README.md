# Unit 63 · Logging：结构化日志

> 目标：**一次请求的 LLM 调用、工具调用、耗时，能被一条 request_id 串起来，能被 grep 和聚合。**

---

## 一、为什么日志要结构化

传统日志是人肉翻的多行文本；结构化日志是**每行一条 JSON**：

```json
{"ts": 1731000000.123, "level": "info", "event": "llm_call", "request_id": "abc123",
 "model": "deepseek-flash", "prompt_tokens": 100, "completion_tokens": 50, "elapsed_ms": 1234.5}
```

好处：

- `grep '"event":"llm_call"'` 一分钟定位所有 LLM 调用
- `jq` 聚合出平均耗时、token 分布
- 字段固定，监控告警能直接吃

## 二、request_id 怎么贯穿

用 **contextvar**（协程安全的线程局部变量），而不是函数传参：

```python
# 中间件里
set_request_id(new_request_id())     # 每个请求一个 id

# 业务代码里（不管隔了多少层函数）
logger.info("llm_call", ...)         # 自动带上当前请求的 request_id
```

好处：LLM 调用封装在 `log_llm_call()` 里，调用方**完全不用知道** request_id 的存在。

## 三、必须记录的字段

| 日志类型 | 必备字段 |
| --- | --- |
| 所有日志 | `ts` / `level` / `event` / `request_id` |
| `llm_call` | `model` / `prompt_tokens` / `completion_tokens` / `elapsed_ms` |
| `tool_call` | `tool` / `args` / `ok` / `elapsed_ms` |
| `exception` | `where` / `exc_type` / `message` |

## 四、怎么跑

```bash
cd ~/ai-journey/unit63-logging
uv sync
uv run python main.py            # 离线自检 22 项

uv run uvicorn main:app --port 8063
curl -i "http://127.0.0.1:8063/ask?q=你好"   # 看响应头 X-Request-Id
cat logs/app.log                              # 看 JSON 行日志
```

## 五、完成标准

- [ ] `uv run python main.py` → 22 通过 / 0 失败
- [ ] `/ask` 响应头里有 `X-Request-Id`
- [ ] `logs/app.log` 里同一 request_id 出现在 request_start / llm_call / tool_call / request_end 四条
- [ ] 能解释「为什么用 contextvar 而不是函数传参」
- [ ] 能说出 llm_call 至少要记哪几个字段

## 六、面试追问

**Q: 日志里至少要有什么？**
→ 时间戳、级别、事件名、request_id；业务日志再加各自的领域字段
（LLM：model/token/耗时；工具：名称/参数/成败/耗时）。

**Q: 怎么把一个请求的所有日志串起来？**
→ request_id：入口生成，contextvar 或显式传参贯穿；响应头回传，
用户报障时报 id 就能捞全链路。

**Q: 为什么不用 print？**
→ print 没有级别、没有结构化、不能定向输出、并发下乱序；
日志器能分级、能落盘、能按 request_id 过滤。

**Q: 生产日志怎么收集？**
→ 本单元是文件落地教学版；生产用 structlog + 日志采集（Filebeat/Fluentd → ES/Loki），
JSON 行格式正是为了能被采集器直接解析。

---

## 七、资料

- [structlog 文档](https://www.structlog.org/en/stable/) —— 生产直接上这个
- [Python logging 官方](https://docs.python.org/3/howto/logging.html)
- [12-Factor App: Logs](https://12factor.net/logs)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/) —— 可观测性进阶
