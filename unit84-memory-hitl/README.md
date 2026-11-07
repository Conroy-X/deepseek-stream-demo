# Unit 84 · Memory/HITL

> Memory 让 Agent 记住用户；HITL 是 AI 应用的责任边界。

---

## 一、本单元做什么

跨会话记忆（带 TTL）+ 危险操作人工审批门。

## 二、要点

- 记忆带 TTL
- 危险操作要审批
- 审批只对本人有效

## 三、怎么跑

```bash
cd ~/ai-journey/unit84-memory-hitl
uv sync
uv run python main.py        # 离线自检
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能用自己的话讲出上面每个要点
- [ ] 能说出本单元和之前哪些单元有关联

## 五、面试追问

- 结合要点准备 2-3 个「为什么」：为什么这么做？不这么做会怎样？有没有别的做法？

## 六、资料

- 见关联单元的 README（本单元是阶段六的整合/收口）
