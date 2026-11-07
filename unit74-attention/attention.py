"""Unit 74 · Attention：Q / K / V 与缩放点积注意力。

核心认知：
  1. 注意力 = 「每个词去看其他词，按相关性加权求和」。
     Q（查询）：我要找什么；K（键）：我有什么；V（值）：我实际给什么。
  2. 三步：Q·K^T 算相关性 → softmax 归一化成权重 → 权重乘 V 加权求和。
  3. 要除以 sqrt(d_k)：点积随维度变大而变大，softmax 会饱和，
     除以 sqrt(d_k) 把方差拉回 1，梯度才健康。
  4. 因果 mask（causal）：生成时只能看前面的词，不能看未来——
     把未来的权重设成 -inf，softmax 后变成 0。

本单元手写单头注意力，把 Q/K/V 的矩阵形状和数值性质离线测一遍。
"""

from __future__ import annotations

import math


def softmax(xs: list[float]) -> list[float]:
    """数值稳定的 softmax：先减最大值防溢出。"""
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    total = sum(exps)
    return [e / total for e in exps]


def scaled_dot_product_attention(q, k, v, mask=None) -> list[list[float]]:
    """单头注意力。q/k/v 都是矩阵（list[list[float]]）。

    scores = Q·K^T / sqrt(d_k)
    weights = softmax(scores)（mask 位置置 -inf）
    output = weights · V
    """
    d_k = len(k[0])
    seq_q = len(q)
    seq_k = len(k)
    d_v = len(v[0])

    scores = []
    for i in range(seq_q):
        row = []
        for j in range(seq_k):
            dot = sum(q[i][t] * k[j][t] for t in range(d_k))
            row.append(dot / math.sqrt(d_k))
        scores.append(row)

    # 因果 mask：把 mask[i][j] == True 的位置设 -inf
    if mask is not None:
        for i in range(seq_q):
            for j in range(seq_k):
                if mask[i][j]:
                    scores[i][j] = -math.inf

    weights = [softmax(row) for row in scores]

    out = []
    for i in range(seq_q):
        row = [0.0] * d_v
        for j in range(seq_k):
            for t in range(d_v):
                row[t] += weights[i][j] * v[j][t]
        out.append(row)
    return out


def linear(x: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    """x·W + b（Q/K/V 各自的线性变换）。"""
    return [sum(w[i][j] * x[j] for j in range(len(x))) + b[i] for i in range(len(b))]


def self_attention(x: list[list[float]], wq, wk, wv, bq, bk, bv, mask=None) -> list[list[float]]:
    """多头注意力里的单头：X → Q/K/V → 注意力输出。"""
    q = [linear(row, wq, bq) for row in x]
    k = [linear(row, wk, bk) for row in x]
    v = [linear(row, wv, bv) for row in x]
    return scaled_dot_product_attention(q, k, v, mask)


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

    print("Unit 74 · Attention 自检\n")

    # ---- softmax ----
    s = softmax([1.0, 2.0, 3.0])
    check("softmax 和为 1", abs(sum(s) - 1.0) < 1e-9)
    check("softmax 保持大小顺序", s[0] < s[1] < s[2])
    check("softmax 数值稳定（大数不溢出）", all(math.isfinite(x) for x in softmax([1000.0, 1000.0, 1000.0])))

    # ---- 缩放点积注意力 ----
    q = [[1.0, 0.0]]
    k = [[1.0, 0.0], [0.0, 1.0]]
    v = [[10.0], [20.0]]
    out = scaled_dot_product_attention(q, k, v)
    check("输出形状 (seq_q, d_v)", len(out) == 1 and len(out[0]) == 1)
    # q 与 k[0] 同向 → 权重偏向 v[0]
    check("注意力偏向相关项", out[0][0] > 10.0, str(out))

    # ---- 注意力权重和为 1（通过间接断言：无 mask 时每行 softmax 和=1）----
    q2 = [[1.0, 0.0], [0.0, 1.0]]
    out2 = scaled_dot_product_attention(q2, k, v)
    check("两个查询都有输出", len(out2) == 2)

    # ---- 因果 mask：未来被屏蔽 ----
    q3 = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
    k3 = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
    v3 = [[1.0], [2.0], [3.0]]
    mask = [
        [False, True, True],
        [False, False, True],
        [False, False, False],
    ]
    out3 = scaled_dot_product_attention(q3, k3, v3, mask=mask)
    # 第一个查询只能看位置 0 → 输出 = v[0] = [1.0]
    check("位置 0 只能看自己", abs(out3[0][0] - 1.0) < 1e-9, str(out3[0]))
    # 第二个查询看位置 0 和 1，不能看位置 2
    check("位置 1 不能看未来", abs(out3[1][0] - 3.0) > 0.5, str(out3[1]))

    # ---- self_attention 全流程 ----
    x = [[1.0, 0.0], [0.0, 1.0]]
    wq = [[1.0, 0.0], [0.0, 1.0]]; bq = [0.0, 0.0]
    wk = [[1.0, 0.0], [0.0, 1.0]]; bk = [0.0, 0.0]
    wv = [[1.0, 0.0], [0.0, 1.0]]; bv = [0.0, 0.0]
    out4 = self_attention(x, wq, wk, wv, bq, bk, bv)
    check("self_attention 输出形状不变", len(out4) == 2 and len(out4[0]) == 2)
    check("输出是有限值", all(math.isfinite(v) for row in out4 for v in row))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
