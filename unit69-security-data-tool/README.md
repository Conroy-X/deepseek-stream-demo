# Unit 69 · AI Security：数据泄露与工具权限

> 目标：**输出也会泄露；工具也要权限。** 防注入之外，这两道关是 AI 应用安全的另一半。

---

## 一、输出泄露：模型可能把不该说的说出来

输入侧的注入（Unit 68）防住了，还有**输出侧**：

- 模型把上下文里的 PII 带进答案（客服场景：把用户 A 的邮箱说给用户 B）
- 模型被诱导念出训练数据里的敏感内容

防御：**输出过滤**——答案返回前过一遍敏感信息检测/脱敏。

```python
if contains_sensitive(raw_answer):
    return redact(raw_answer)   # a@corp.test → [邮箱已脱敏] <!-- pii-check: allow -->
```

规则检测覆盖：邮箱（example.com 示例除外）、手机号、身份证。

## 二、工具权限：最小权限

Agent 能调工具后，工具就是新的攻击面。每个工具声明所需权限：

```python
TOOL_PERMISSIONS = {
    "lookup_order": "orders:read",
    "refund": "orders:write",
    "query_db": "db:read",
}
```

调用链必须过：**权限检查 → 参数校验 → 执行**。

- `user` 只能查订单，不能退款
- `support` 能退款，但不能直接查库
- `admin` 才能查库

## 三、危险参数校验

就算有权限，参数本身也要查：

| 危险 SQL | 为什么拦 |
| --- | --- |
| `DROP TABLE` / `TRUNCATE` / `ALTER` | 结构性破坏 |
| `DELETE` 无 `WHERE` | 误删全表 |
| `--` 注释 / `/* */` | SQL 注入常用 |

教学版正则只能拦已知模式；生产要参数化查询，而不是拼 SQL。

## 四、怎么跑

```bash
cd ~/ai-journey/unit69-security-data-tool
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8069
curl "http://127.0.0.1:8069/ask?q=你好"                          # 输出被脱敏
curl "http://127.0.0.1:8069/data?user_id=alice&key=order_2"      # 403：读别人的数据
curl "http://127.0.0.1:8069/tool?role=user&name=refund"          # 403：越权工具
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 输出里的邮箱/手机号被脱敏
- [ ] 用户读不到别人的数据
- [ ] 角色调不了越权工具
- [ ] 危险 SQL 被拦
- [ ] 能说「输出过滤为什么和输入过滤一样重要」

## 六、面试追问

**Q: 为什么防了注入还要防输出泄露？**
→ 注入是「坏东西进来」，输出泄露是「好东西出去」。模型可能把训练数据、
上下文里的 PII 带出来。两个方向都要防。

**Q: 工具权限和 RBAC 什么关系？**
→ 同一套逻辑：RBAC 管「人 → 角色 → 权限」，工具权限是把「工具调用」
也当成一个权限点（orders:write / db:read）挂进 RBAC 表。

**Q: 规则检测敏感信息够吗？**
→ 不够。规则能抓标准格式（邮箱/手机号/身份证），抓不住变体
（「一三九一二三」这种口语化号码）。生产要 LLM 裁判 + 正则双保险。

**Q: SQL 安全检查够吗？**
→ 不够。黑名单永远有绕过。正解是**参数化查询**，让 SQL 注入从根上不可能。

---

## 七、资料

- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [OWASP SQL Injection](https://owasp.org/www-community/attacks/SQL_Injection)
- [OWASP API 安全](https://owasp.org/www-project-api-security/)
