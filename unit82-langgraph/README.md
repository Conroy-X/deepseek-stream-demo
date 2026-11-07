# Unit 82 · LangGraph 优化：子图 / 并行 / 流式事件

> 目标：**把 LangGraph 从「能跑一个图」推进到「能组织复杂流程」——子图复用、并行提速、流式可观测。**

---

## 一、三块优化

| 能力 | 解决什么问题 | 实现要点 |
| --- | --- | --- |
| **子图 subgraph** | 一组节点要复用（比如「生成+引用」在多个流程里都要用） | 子图自己是一个 Graph，父图把 `subgraph.run` 当一个节点 |
| **并行 parallel** | 检索和翻译互不依赖，串行跑浪费时间 | 同一层的多个成员基于**同一份 state** 跑，结果合并 |
| **流式事件 streaming** | 用户等了 10 秒才看到结果，不知道卡在哪 | 每个节点进出都吐一个事件 |

## 二、并行的关键细节

```python
merged = dict(state)
for m in members:
    part = self._run_node(m, state)   # 注意：都传原始 state，不是 merged
    merged.update(part)
```

**为什么都传原始 state**：并行意味着互不依赖，如果传 `merged`，
后面的节点就会看到前面节点的输出——那就不是并行了，而且结果依赖执行顺序。

合并用 `dict.update`：同名字段后者覆盖前者，这是简化的策略。
真实 LangGraph 用 **reducer**（比如 list 追加、dict 合并）来定义冲突解决规则。

## 三、流式事件

```python
events = [
    {"kind": "node_start", "node": "pre"},
    {"kind": "node_end",   "node": "pre"},
    {"kind": "node_start", "node": "par", "detail": "parallel x2"},
    ...
]
```

真实 LangGraph 是**异步 yield**（`async for event in graph.astream(...)`），
本单元同步收集后返回，便于离线断言。

## 四、怎么跑

```bash
cd ~/ai-journey/unit82-langgraph
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8082
curl "http://127.0.0.1:8082/run?q=年假"      # 最终 state
curl "http://127.0.0.1:8082/stream?q=年假"   # 事件流
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 子图能独立跑，也能被父图当节点调
- [ ] 并行组两个成员都执行，结果都进最终 state
- [ ] 事件流有 `node_start` / `node_end`，顺序正确
- [ ] 能解释「为什么并行时都传原始 state」

## 六、面试追问

**Q: 子图和普通节点的区别？**
→ 子图有自己的入口/出口和内部边，是一个封装的执行单元；父图只看到它的输入输出。
好处是复用和隔离——子图内部改结构，父图不用动。

**Q: 并行节点怎么合并结果？**
→ 简化版用 dict.update；真实场景用 reducer（append/merge/max），
因为「两个节点都往 results 里加东西」时，覆盖会丢数据。

**Q: 流式事件有什么用？**
→ 三个：用户体验（能看到进展）、可观测（知道卡在哪个节点）、
调试（重现执行路径）。生产里配合日志的 request_id 一起用。

---

## 七、资料

- [LangGraph Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [LangGraph Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)
- [Unit 37–40 LangGraph 基础](../../unit37-langgraph-state/)
