"""Unit 75 · LLM 原理：下一个 token 预测 + 损失 + 采样。

核心认知：
  1. LLM 的训练目标极其简单：**给定前面的 token，预测下一个 token**。
     训练 = 让「正确的下一个 token」概率尽量大（最小化交叉熵）。
  2. 训练分三阶段：
     - Pretraining：海量文本学「语言本身」（下一个 token）
     - SFT：用问答对微调，学「按指令回答」
     - RLHF：用人类偏好进一步对齐（先训奖励模型，再强化学习）
  3. 推理时怎么「生成」：
     - greedy：永远选概率最高的 token —— 确定但单调
     - temperature 采样：按温度缩放后随机抽 —— 有创造性
     - temperature→0 等价 greedy，temperature 越高越随机

本单元训练一个微型 n-gram 语言模型，把「预测 → 损失 → 采样」全链路跑通。
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# 微型 n-gram 语言模型
# ---------------------------------------------------------------------------
class TinyLM:
    """1-gram（只看前一个 token）语言模型。"""

    def __init__(self) -> None:
        self.counts: dict[str, Counter] = defaultdict(Counter)

    def train(self, corpus: list[list[str]]) -> None:
        """语料是若干 token 序列（含 <s> 和 </s>）。"""
        for seq in corpus:
            for ctx, nxt in zip(seq, seq[1:]):
                self.counts[ctx][nxt] += 1

    def probs(self, context: str) -> dict[str, float]:
        """P(next | context)。未见过的 context 返回空（表示无法预测）。"""
        c = self.counts.get(context)
        if not c:
            return {}
        total = sum(c.values())
        return {tok: n / total for tok, n in c.items()}

    def next_token_distribution(self, context: str, temperature: float = 1.0) -> dict[str, float]:
        """按 temperature 缩放概率（温度→0 退化为 greedy 用）。"""
        p = self.probs(context)
        if not p:
            return {}
        if temperature <= 0:
            best = max(p, key=p.get)
            return {best: 1.0}
        # 对 log 概率除以 temperature 再 softmax
        logits = {tok: math.log(prob) / temperature for tok, prob in p.items()}
        m = max(logits.values())
        exps = {tok: math.exp(v - m) for tok, v in logits.items()}
        total = sum(exps.values())
        return {tok: v / total for tok, v in exps.items()}

    def greedy_next(self, context: str) -> str | None:
        p = self.probs(context)
        if not p:
            return None
        return max(p, key=p.get)

    def sample_next(self, context: str, temperature: float = 1.0, rng: random.Random | None = None) -> str | None:
        dist = self.next_token_distribution(context, temperature)
        if not dist:
            return None
        rng = rng or random
        # 按概率加权随机
        r = rng.random()
        acc = 0.0
        for tok, prob in dist.items():
            acc += prob
            if r <= acc:
                return tok
        return max(dist, key=dist.get)

    def cross_entropy(self, seq: list[str]) -> float:
        """对一条序列算平均交叉熵：-1/N Σ log P(next|ctx)。越低越好。"""
        losses = []
        for ctx, nxt in zip(seq, seq[1:]):
            p = self.probs(ctx)
            if not p or nxt not in p:
                losses.append(math.log(1e-9))  # 没见过的当作极低概率
            else:
                losses.append(math.log(p[nxt]))
        return -sum(losses) / len(losses)


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

    print("Unit 75 · LLM 原理 自检\n")

    corpus = [
        ["<s>", "我", "爱", "北京", "</s>"],
        ["<s>", "我", "爱", "编程", "</s>"],
        ["<s>", "你", "爱", "北京", "</s>"],
        ["<s>", "我", "爱", "北京", "</s>"],  # 北京 出现更多
    ]
    lm = TinyLM()
    lm.train(corpus)

    # ---- 下一个 token 预测 ----
    p = lm.probs("我")
    check("「我」后面有预测", "爱" in p)
    check("「爱」后面有北京/编程", set(lm.probs("爱")) == {"北京", "编程"})
    check("概率和为 1", abs(sum(lm.probs("爱").values()) - 1.0) < 1e-9)
    # 北京 出现 2 次（语料 4 条里「爱→北京」2 次，「爱→编程」1 次）
    check("高频后继概率更高", lm.probs("爱")["北京"] > lm.probs("爱")["编程"])

    # ---- greedy ----
    check("greedy 选最高频", lm.greedy_next("爱") == "北京")
    check("greedy 未知上下文返回 None", lm.greedy_next("不存在") is None)

    # ---- temperature ----
    rng = random.Random(42)
    check("temperature=0 退化为 greedy", lm.sample_next("爱", temperature=0, rng=rng) == "北京")
    # 高温下多次采样应能采到「编程」（概率较低的那个）
    rng2 = random.Random(7)
    samples = [lm.sample_next("爱", temperature=2.0, rng=rng2) for _ in range(100)]
    check("高温采样能采到低频 token", "编程" in samples)
    check("低温采样更集中", all(lm.sample_next("爱", temperature=0.01, rng=random.Random(i)) == "北京" for i in range(10)))

    # ---- 交叉熵 ----
    seen_seq = ["<s>", "我", "爱", "北京", "</s>"]
    unseen_seq = ["<s>", "我", "爱", "不存在词", "</s>"]
    ce_seen = lm.cross_entropy(seen_seq)
    ce_unseen = lm.cross_entropy(unseen_seq)
    check("见过序列损失低", ce_seen < ce_unseen, f"{ce_seen:.3f} vs {ce_unseen:.3f}")
    check("损失是正数", ce_seen > 0)

    # ---- 分布无 predict 时不崩 ----
    check("未知上下文分布为空", lm.next_token_distribution("xx") == {})

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
