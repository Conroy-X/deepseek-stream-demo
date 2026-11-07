# Unit 79 · 工程化收尾：权限、日志、错误页、成本面板

> 目标：**把项目从「能演示」打磨成「能交付」。** 四个横切面缺一不可。

---

## 一、四个横切面

| 横切面 | 做什么 | 不做会怎样 |
| --- | --- | --- |
| **权限** | 接口级 RBAC，每个路由声明所需权限 | 谁都能调管理接口 |
| **日志** | request_id + 耗时 + 调用链 | 出问题无法定位是哪个请求、哪一步慢 |
| **错误页** | 统一 JSON 错误格式，不泄漏堆栈 | 用户看到 500 和内部路径 |
| **成本面板** | token → 钱，实时可看 | 月底账单才知道超支 |

## 二、关键实现

```python
@app.middleware("http")
async def logging_middleware(request, call_next):
    rid = uuid.uuid4().hex[:8]
    _request_id.set(rid)          # contextvar：业务代码自动拿到
    t0 = time.perf_counter()
    resp = await call_next(request)
    state.logs.append({...})       # 耗时 + 状态
    resp.headers["X-Request-Id"] = rid
    return resp
```

成本 = `输入token/1M × 输入单价 + 输出token/1M × 输出单价`，input 和 output 必须分开算（Unit 71 学过）。

## 三、怎么跑

```bash
cd ~/ai-journey/unit79-rag-engineering
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8079
curl -H "X-Role: user" "http://127.0.0.1:8079/chat?q=你好"
curl -H "X-Role: admin" "http://127.0.0.1:8079/cost/panel"
curl "http://127.0.0.1:8079/chat"        # guest → 403
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] guest/user/admin 三档权限都按预期 200/403
- [ ] 响应头有 `X-Request-Id`，日志里有耗时
- [ ] 错误返回统一 JSON（ok/error/request_id）
- [ ] 成本面板的 cost = 入 + 出分开计价

## 五、面试追问

**Q: 为什么错误页不能直接返回异常堆栈？**
→ 堆栈会泄漏内部路径、依赖版本、业务字段；攻击者拿这些做进一步攻击。
对用户只给「服务内部错误 + request_id」，详细错误进日志。

**Q: request_id 为什么放中间件？**
→ 一次 HTTP 请求的所有日志要能串起来。中间件是唯一能覆盖「请求进入 → 离开」全链的位置。

**Q: 成本面板怎么做才准？**
→ 每次真实调用后按 usage 字段记账（input/output 分开），不能按字数估；
面板要能按时间/用户/接口维度聚合。

---

## 六、资料

- [structlog](https://www.structlog.org/en/stable/)
- [FastAPI 中间件](https://fastapi.tiangolo.com/tutorial/middleware/)
- [OWASP：错误处理](https://cheatsheetseries.owasp.org/cheatsheets/Error_Handling_Cheat_Sheet.html)
