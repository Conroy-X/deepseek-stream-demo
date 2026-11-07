"""Unit 73 · Transformer 架构：位置编码 + 残差 + LayerNorm + FFN。

核心认知：
  1. Transformer 能并行处理整个序列（不像 RNN 一个词一个词地串行），
     所以它成了 LLM 的主流架构。
  2. 但「并行」带来一个问题：注意力本身不关心词的顺序，
     所以必须显式加**位置编码**（positional encoding）告诉模型「谁在前谁在后」。
  3. 每个 block = 注意力 + 残差 + LayerNorm + FFN，重复 N 次。
     残差让梯度能直达底层（解决深层难训练），LayerNorm 稳定数值。
  4. Attention 内部细节（Q/K/V）在 Unit 74 展开。

本单元实现一个微型 Transformer block 的前向传播，把形状和性质离线测一遍。
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# 位置编码（正弦）
# ---------------------------------------------------------------------------
def positional_encoding(seq_len: int, d_model: int) -> list[list[float]]:
    """返回 (seq_len, d_model) 的位置编码矩阵。

    偶数维用 sin，奇数维用 cos，频率随维度指数下降。
    """
    pe = []
    for pos in range(seq_len):
        row = []
        for i in range(d_model):
            angle = pos / (10000 ** (2 * (i // 2) / d_model))
            row.append(math.sin(angle) if i % 2 == 0 else math.cos(angle))
        pe.append(row)
    return pe


# ---------------------------------------------------------------------------
# LayerNorm
# ---------------------------------------------------------------------------
def layer_norm(x: list[float], eps: float = 1e-5) -> list[float]:
    """对单个向量做 LayerNorm：减去均值、除以标准差。"""
    n = len(x)
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / n
    std = math.sqrt(var + eps)
    return [(v - mean) / std for v in x]


# ---------------------------------------------------------------------------
# FFN：两层 MLP + ReLU
# ---------------------------------------------------------------------------
def ffn(x: list[float], w1: list[list[float]], b1: list[float],
        w2: list[list[float]], b2: list[float]) -> list[float]:
    """x → W1·x + b1 → ReLU → W2·h + b2。"""
    hidden = [max(0.0, sum(w1[i][j] * x[j] for j in range(len(x))) + b1[i])
              for i in range(len(b1))]
    out = [sum(w2[k][i] * hidden[i] for i in range(len(hidden))) + b2[k]
           for k in range(len(b2))]
    return out


# ---------------------------------------------------------------------------
# 残差连接
# ---------------------------------------------------------------------------
def residual(x: list[float], sublayer_out: list[float]) -> list[float]:
    """x + sublayer(x)。残差让梯度有直达路径。"""
    return [a + b for a, b in zip(x, sublayer_out)]


# ---------------------------------------------------------------------------
# 微型 transformer block（注意力用恒等代替，Unit 74 换成真注意力）
# ---------------------------------------------------------------------------
def transformer_block(x: list[float], w1, b1, w2, b2) -> list[float]:
    """简化 block：x → (identity attention) → +x → LN → FFN → +h → LN。"""
    # 子层 1：attention（本单元用恒等，结构先搭好）
    h1 = residual(x, x)                       # x + x
    h1 = layer_norm(h1)
    # 子层 2：FFN
    f = ffn(h1, w1, b1, w2, b2)
    h2 = residual(h1, f)                      # h1 + f
    h2 = layer_norm(h2)
    return h2


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

    print("Unit 73 · Transformer 架构 自检\n")

    # ---- 位置编码 ----
    pe = positional_encoding(seq_len=4, d_model=8)
    check("形状 (seq_len, d_model)", len(pe) == 4 and all(len(r) == 8 for r in pe))
    check("位置 0 偶数维为 sin(0)=0", pe[0][0] == 0.0)
    check("位置 0 奇数维为 cos(0)=1", abs(pe[0][1] - 1.0) < 1e-9)
    check("不同位置编码不同", pe[0] != pe[1])
    check("值域在 [-1, 1]", all(-1.0 <= v <= 1.0 for r in pe for v in r))

    # ---- LayerNorm ----
    x = [1.0, 3.0, 5.0, 7.0]
    ln = layer_norm(x)
    check("LayerNorm 后均值≈0", abs(sum(ln) / len(ln)) < 1e-6, str(sum(ln) / len(ln)))
    var = sum(v * v for v in ln) / len(ln)
    check("LayerNorm 后方差≈1", abs(var - 1.0) < 1e-4, str(var))

    # ---- FFN 非线性 ----
    w1 = [[1.0, 0.0], [0.0, 1.0]]
    b1 = [0.0, 0.0]
    w2 = [[1.0, -1.0]]
    b2 = [0.0]
    out = ffn([1.0, 2.0], w1, b1, w2, b2)
    check("FFN 输出维度正确", len(out) == 1)
    check("FFN 含 ReLU 非线性", abs(out[0] - (1.0 - 2.0)) < 1e-9, str(out[0]))

    # ---- 残差 ----
    r = residual([1.0, 2.0], [10.0, 20.0])
    check("残差 = x + sublayer", r == [11.0, 22.0])
    # 恒等子层：residual(x, x) = 2x，再 LayerNorm 后仍是常量向量（均值归一）
    h = layer_norm(residual([3.0, 3.0], [3.0, 3.0]))
    check("恒等子层后 LayerNorm 稳定", abs(sum(h)) < 1e-6)

    # ---- 完整 block 形状与数值稳定 ----
    x0 = [0.5, -0.5, 1.0, -1.0]
    w1b = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    b1b = [0.0, 0.0]
    w2b = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]
    b2b = [0.0, 0.0, 0.0, 0.0]
    out_block = transformer_block(x0, w1b, b1b, w2b, b2b)
    check("block 输出维度不变", len(out_block) == len(x0))
    check("block 输出数值稳定（有限值）", all(math.isfinite(v) for v in out_block))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
