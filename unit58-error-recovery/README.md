# Unit 58 · Error Recovery

> SQL 报错是资产不是故障。三类典型错误 + 一类 MCP 工具层错误，每类都有修正策略。
> 最多 3 次重试，错误归一化后不含 addArguments / pydantic URL。

**完成标准**：能说清「错误归一化」要剥掉什么；能解释为什么修不好也要跑满 3 次（而不是提前退出）；
能说清 MCP 工具异常被脱敏后为什么模型看不到原因。

## 快速开始

```bash
cd ~/ai-journey/unit58-error-recovery
uv sync
uv run uvicorn main:app --port 8058
```

## 自检

```bash
uv run python main.py
# 结果：42 通过 / 0 失败
```

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `recovery.py` | normalize_error + fix_sql + run_with_recovery |
| `main.py` | 4 接口 + 42 自检 |
| `static/index.html` | 四类典型错误 + 自定义 SQL |

## 实测

| 项目 | 结果 |
| --- | --- |
| 列名拼错 authr → author_id | 2 轮修好 |
| 表名拼错 article → articles | 2 轮修好 |
| 语法错 SELECT FROM FROM | 2 轮修好（去重 FROM + 补 *） |
| 修不好 badcol + badtable | 3 轮后停手，不无限循环 |
| 归一化不含 addArguments | ✓ |
| 归一化不含 errors.pydantic.dev | ✓ |
| MCP 脱敏 Error executing tool xxx | 归一化为 tool_exception |

## 常见坑

1. **修不好就提前退出**。如果 fix_sql 改不动就 return，修不好的 case 只跑 1 轮。
   正确做法：即使改不动也继续跑，让 max_rounds 兜底 —— 这样跑满 3 次才停。
2. **错误历史被覆盖**。每轮的错误必须保留在 attempts 列表里，不能只留最后一条。
   否则模型（或人）看不到「前面试了什么」，会重复试同样的修正。
3. **把错误当成功**。有 error_raw 的轮次 fixed 必须 False，没 error_raw 的轮次 fixed 必须 True。
   这条状态机错了会导致「假成功」。

## 下一单元

Unit 59 · MCP 工程化 —— 日志、超时、并发、错误码。
