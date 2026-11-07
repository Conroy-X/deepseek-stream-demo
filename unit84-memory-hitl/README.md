# Unit 84 · Memory / HITL：记忆与人工审批

> 目标：**让 Agent 记住用户，同时守住「危险操作必须有人签字」这条线。**

---

## 一、Memory：跨会话记忆

```python
class Memory:
    def set(self, key, value, ttl=None)   # 带过期时间
    def get(self, key)                    # 过期自动清
    def keys(self)                        # 先清理过期再返回
```

三个要点：

1. **TTL 是必需品**，不是可选项——记忆会过期，否则越积越多且可能过时
2. **结构化存**（JSON 序列化），不是一堆散字符串
3. **分层**：短期（当前会话）/ 长期（用户画像）/ 工作记忆（任务中间态）

## 二、HITL：人工审批门

| 动作 | 需要审批 |
| --- | --- |
| 查订单、查资料 | ❌ 自动执行 |
| **退款、删用户、转账、删表** | ✅ 必须人工确认 |

```python
DANGEROUS_TOOLS = {"refund", "delete_user", "drop_table", "transfer_money"}
```

**关键设计：不能自己批自己。**

```python
if approver == user:
    return {"ok": False, "error": "不能自己审批自己的请求"}
```

这是最容易被忽略的合规点。审批要记录「谁批的」（`approver` 字段），
责任才可追溯。

## 三、完整流转

```
Agent 想退款
  → POST /hitl/request   （进入待审批）
  → 人工 POST /hitl/approve（由他人审批）
  → POST /tool/refund    （通过，执行）
```

未审批就执行 → **403**，这是硬门，不是提示。

## 四、怎么跑

```bash
cd ~/ai-journey/unit84-memory-hitl
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8084
curl -X PUT "http://127.0.0.1:8084/memory/user:rik?value=上海"
curl "http://127.0.0.1:8084/memory/user:rik"
curl -X POST "http://127.0.0.1:8084/hitl/request?tool=refund&user=rik"
curl -X POST "http://127.0.0.1:8084/hitl/approve?tool=refund&user=rik&approver=boss"
curl -X POST "http://127.0.0.1:8084/tool/refund?user=rik"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 记忆能写、能读、过期自动失效
- [ ] 危险操作未审批返回 403
- [ ] 自己批自己被拒
- [ ] 审批只对本人有效（给 alice 批的不能给 bob 用）
- [ ] 能说「哪些操作必须 HITL」

## 六、面试追问

**Q: 哪些操作必须 HITL？**
→ 不可逆 + 影响资金/权限/数据的：支付、退款、删数据、改权限、发外部消息。
判断标准：**做错了能不能撤销**。能撤销的可以自动，不能撤销的必须人签字。

**Q: Memory 会不会造成隐私问题？**
→ 会。要遵守三条：只存必要信息、设 TTL、可删除（提供「忘记我」接口）。
多租户还要做隔离（按 user_id 分区）。

**Q: 自批自为什么不行？**
→ 审批的意义是「第二双眼睛」。自己批自己等于没有审批，
在金融/医疗等强合规场景是严重违规。

**Q: 审批流程怎么防绕过？**
→ 审批状态放服务端（不是客户端），执行接口自己再校验一次（`execute` 里的 403），
不能只靠前端隐藏按钮。

---

## 七、资料

- [LangGraph Interrupts（HITL）](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [Unit 67 认证授权](../unit67-auth/)
- [Unit 69 工具权限](../unit69-security-data-tool/)