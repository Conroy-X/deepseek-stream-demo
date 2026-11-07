"""Unit 71 · Token 原理：手写 BPE（Byte-Pair Encoding）分词器。

核心认知：
  1. token 是模型看到的最小单位——不是字符，也不是单词，是「高频片段」。
  2. BPE 思想：从单个字符出发，反复把「出现最多的相邻对」合并成一个新 token，
     直到词汇表达到目标大小。这样常用词是 1 个 token，生僻词被拆成多个 token。
  3. token 数 ≠ 字符数：英文一个单词常是 1 token，中文一个字常是 1-2 token，
     所以同样语义的中文往往消耗更多 token（影响成本，面试常问）。
  4. 计费 = (输入 token + 输出 token) × 单价，input 和 output 单价通常不同。

本单元手写一个教学版 BPE，把「训练合并规则 → 编码 → 解码」整条链路跑通。
"""

from __future__ import annotations

from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# BPE 训练
# ---------------------------------------------------------------------------
def _pair_counts(word: list[str]) -> Counter:
    return Counter(zip(word, word[1:]))


def train_bpe(corpus: list[str], num_merges: int) -> list[tuple[str, str, str]]:
    """训练 BPE，返回合并规则列表 [(a, b, merged), ...]，按训练顺序。

    corpus 是字符串列表；词按字符切分，词尾加一个特殊符号 </w> 表示边界
    （这样「词尾的合并」不会和「词中的合并」混在一起）。
    """
    # 1. 初始：每个词 = 字符列表，词尾加 </w>
    vocab: list[list[str]] = []
    for word in corpus:
        vocab.append(list(word) + ["</w>"])

    merges: list[tuple[str, str, str]] = []
    for _ in range(num_merges):
        # 2. 统计所有相邻对的出现次数
        counts: Counter = Counter()
        for word in vocab:
            counts.update(_pair_counts(word))
        if not counts:
            break
        # 3. 取出现最多的相邻对；并列时按字典序取，保证确定性
        best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        (a, b), _ = best
        merged = a + b
        merges.append((a, b, merged))
        # 4. 把所有 a+b 合并成 merged
        new_vocab = []
        for word in vocab:
            out = []
            i = 0
            while i < len(word):
                if i + 1 < len(word) and word[i] == a and word[i + 1] == b:
                    out.append(merged)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            new_vocab.append(out)
        vocab = new_vocab
    return merges


class BpeTokenizer:
    """用训练好的合并规则做编码/解码。"""

    def __init__(self, merges: list[tuple[str, str, str]]) -> None:
        self.merges = merges
        # 合并规则按训练顺序应用
        self._pairs = [(a, b, m) for a, b, m in merges]

    def encode(self, text: str) -> list[str]:
        """把文本切成 token 列表。"""
        tokens = list(text) + ["</w>"]
        for a, b, merged in self._pairs:
            out = []
            i = 0
            while i < len(tokens):
                if i + 1 < len(tokens) and tokens[i] == a and tokens[i + 1] == b:
                    out.append(merged)
                    i += 2
                else:
                    out.append(tokens[i])
                    i += 1
            tokens = out
        return tokens

    def decode(self, tokens: list[str]) -> str:
        """把 token 列表拼回文本（去掉词尾 </w>）。"""
        text = "".join(tokens)
        return text.replace("</w>", "")

    def vocab_size(self, base_chars: set[str]) -> int:
        return len(base_chars) + len(self.merges)


# ---------------------------------------------------------------------------
# 计费
# ---------------------------------------------------------------------------
def estimate_cost(input_tokens: int, output_tokens: int,
                  price_in: float = 1.0, price_out: float = 2.0) -> dict:
    """按每百万 token 单价计算成本。"""
    cost = input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost": round(cost, 6),
    }


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 71 · Token 原理 自检\n")

    # ---- BPE 训练：确定性 ----
    merges = train_bpe(["low", "lower", "newest", "widest"], num_merges=5)
    check("训练出 5 条合并规则", len(merges) == 5, str(len(merges)))
    # 第一条一定是出现最多的相邻对。语料里 e+s 出现于 newest/widest（各1，共2）
    # 而 l+o+w 出现于 low/lower（各1，共2）。训练里首条应是出现次数最多的对之一。
    first = merges[0]
    check("首条合并规则有 a+b", len(first) == 3 and first[2] == first[0] + first[1])

    # ---- 编码/解码往返 ----
    tok = BpeTokenizer(merges)
    text = "low"
    toks = tok.encode(text)
    check("编码后是 token 列表", isinstance(toks, list) and all(isinstance(t, str) for t in toks))
    check("解码还原原文", tok.decode(toks) == text)

    # ---- 合并后 token 数变少（分词的核心价值）----
    # 训练语料里 w+</w> 高频，训练后 'widest' 应该比逐字切分短
    toks_widest = tok.encode("widest")
    raw_chars = len(list("widest") + ["</w>"])
    check("BPE 后 token 数少于逐字", len(toks_widest) < raw_chars,
          f"{len(toks_widest)} vs {raw_chars}")

    # ---- 词汇表大小 = 基础字符 + 合并数 ----
    base = set()
    for w in ["low", "lower", "newest", "widest"]:
        base.update(list(w))
    base.add("</w>")
    check("vocab_size = 基础字符 + 合并数", tok.vocab_size(base) == len(base) + 5)

    # ---- 高频 vs 低频：token 数取决于频次，而不是文本长度 ----
    en_merges = train_bpe(["hello world", "hello there", "world peace"], num_merges=8)
    en_tok = BpeTokenizer(en_merges)
    frequent = len(en_tok.encode("hello world"))   # 常见短语，片段已被合并
    rare = len(en_tok.encode("hello quux"))        # quux 未见过，只能逐字
    check("常见短语 token 少", frequent <= 5, str(frequent))
    check("低频词 token 多（逐字）", rare > frequent, f"rare={rare} freq={frequent}")
    # 两者长度相近（11 vs 10），差异来自频次而非长度
    check("token 数取决于频次而非长度", rare > frequent and len("hello world") >= len("hello quux"),
          f"rare={rare} freq={frequent}")

    # ---- token 计费 ----
    c = estimate_cost(500_000, 100_000, price_in=1.0, price_out=2.0)
    check("总 token = 60 万", c["total_tokens"] == 600_000)
    # 500k/1M*1 + 100k/1M*2 = 0.5 + 0.2 = 0.7
    check("成本 = 0.7", abs(c["cost"] - 0.7) < 1e-6, str(c["cost"]))
    c2 = estimate_cost(1_000_000, 0)
    check("百万输入 token 成本 1.0", abs(c2["cost"] - 1.0) < 1e-6)

    # ---- 解码去除边界符 ----
    check("decode 去 </w>", "</w>" not in tok.decode(tok.encode("low")))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
