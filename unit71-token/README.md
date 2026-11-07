# Unit 71 · Token 原理：手写 BPE

> 目标：**理解 token 是什么、BPE 怎么训练、以及为什么中文更费 token（更费钱）。**

---

## 一、token 是什么

模型看不到「字」也看不到「词」，它看到的是 **token**——训练时统计出来的「高频片段」。

- 常用词：1 个 token（`hello` → `hello`）
- 生僻词：被拆成多个 token（`antidisestablishmentarianism` → 一堆片段）

分词器（tokenizer）就是把文本切成 token 的工具，BPE 是最主流的一种。

## 二、BPE 怎么训练

```
初始：每个词 = 字符列表，词尾加 </w>（词边界）
循环 N 次：
  1. 统计所有相邻字符对的出现次数
  2. 合并出现最多的那一对（如 e+s → es）
  3. 更新所有词里的该相邻对
```

**关键认知**：BPE 是「从下往上」的——从字符出发反复合并，不是「从上往下」把词拆开。

## 三、为什么中文更费 token

| 语言 | 特点 | 相同语义的 token 数 |
| --- | --- | --- |
| 英文 | 空格分词，高频词整体成 token | 少 |
| 中文 | 无空格，字符多且组合多 | 多（约 1.5–2×） |

影响：**同样的内容，中文的 API 成本更高**。面试常问「中文场景怎么省 token」——
答案之一是：先压缩/精简再送入，或选对中文友好的分词器。

## 四、怎么跑

```bash
cd ~/ai-journey/unit71-token
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8071
curl "http://127.0.0.1:8071/tokenize?text=hello%20world"
curl "http://127.0.0.1:8071/cost?input_tokens=500000"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能解释 BPE 的训练循环（字符出发，反复合并高频对）
- [ ] 能说 token 和字符、单词的区别
- [ ] 能解释「为什么中文更费 token」
- [ ] 能算 token 成本（input 与 output 分开计价）

## 六、面试追问

**Q: 词表大小怎么定？**
→ 权衡：词表大 → 序列短（省 token）但 embedding 矩阵大（费内存/训练慢）；
词表小 → 反之。主流模型几万到十几万 token。

**Q: 生僻词怎么办？**
→ BPE 拆成子词片段，用已知片段拼出来。这也是 BPE 相对整词分词的优势：
不会出现「词表外」的词（OOV）。

**Q: 为什么 input 和 output 单价不同？**
→ output 是逐 token 生成的（不能并行），算力更贵；input 可以并行编码。
所以 output 单价通常更高（deepseek-flash 的 output 就是 input 的几倍）。

**Q: 怎么省 token？**
→ 精简 prompt（删废话）、少塞历史、输出限长（max_tokens）、
结构化输出代替自由文本、对中文场景选高效分词器。

---

## 七、资料

- [BPE 论文](https://arxiv.org/abs/1508.07909)
- [OpenAI tokenizer 在线演示](https://platform.openai.com/tokenizer)
- [HuggingFace Tokenizers](https://huggingface.co/docs/tokenizers/index)
- [deepseek 计费说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
