# Unit 81 · Research Agent 优化：引用准确性、去重、报告模板

> 目标：**把 Research Agent 从「能跑」打磨到「报告可交付」——每条结论有据可查，来源不重复。**

---

## 一、三个优化点

| 优化点 | 问题 | 做法 |
| --- | --- | --- |
| **去重** | 多轮检索/多工具命中同一段，报告里重复三遍 | 按 `(source, page, text)` 去重，保留首次顺序 |
| **引用准确性** | 报告写了 `[3]`，但来源只有 2 条 | 引用编号必须落在 `1..len(sources)` 内，且指向真实来源 |
| **报告模板** | 自由发挥，有时没结论有时没来源 | 固定四段：结论 / 来源 / 局限 |

## 二、报告模板

```markdown
# 研究报告：<问题>

## 结论
- 年假天数按工龄递增 [1]

## 来源
[1] handbook.md 第 1 页：年假满一年 5 天，满三年 10 天。

## 局限
本报告仅基于知识库检索结果，未覆盖外部信息。
```

**「局限」这一段最容易被忽略，但面试官很爱问**：你凭什么说这个结论可靠？

## 三、引用校验

```python
def check_citations(report):
    for c in report.conclusions:
        if not 1 <= c["cite"] <= len(report.sources):
            return False, [f"引用 [{c['cite']}] 超出来源范围"]
    return True, []
```

这是**可自动化的质量门**：报告生成后先过一遍，不合格不返回。

## 四、怎么跑

```bash
cd ~/ai-journey/unit81-agent-optimize
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8081
curl -G --data-urlencode "q=年假怎么算" "http://127.0.0.1:8081/research"
curl -G --data-urlencode "q=今天天气" "http://127.0.0.1:8081/research"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 5 条原始结果去重后剩 3 条
- [ ] 每条结论的 `[n]` 都能对上来源
- [ ] 报告有「结论 / 来源 / 局限」三段
- [ ] 无关问题明确说「资料不足」而不是硬编

## 六、面试追问

**Q: 引用准确性怎么保证？**
→ 生成后用规则校验：`[n]` 必须落在来源范围内；更严的做法是逐字比对
（结论里的关键短语要能在来源文本里找到）。

**Q: 为什么去重不能只按 URL？**
→ 同一文档的不同页/不同片段是不同证据；按 `(source, page, text)` 才既去重又不丢证据。

**Q: 报告模板为什么固定？**
→ 固定结构才能评测（每段都可断言），也才能让用户快速定位「结论在哪、依据在哪」。
自由格式的 Agent 输出几乎无法评测。

---

## 七、资料

- [Open Deep Research](https://github.com/langchain-ai/open_deep_research)
- [Unit 45 Research Agent](../../unit45-project3/)
