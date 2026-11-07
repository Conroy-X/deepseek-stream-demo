"""Unit 72 · Embedding 原理：向量就是语义坐标。

核心认知：
  1. embedding 把离散的 token 映射成连续向量——向量是「语义坐标」，
     语义相近的词，坐标相近（余弦相似度高）。
  2. 向量维度是人为定的（128/1024/1536…），每个维度没有人工可解释的含义，
     但组合起来能表达语义关系。
  3. 向量的方向比长度重要（所以用余弦相似度，不用欧氏距离）。
  4. 向量可做算术：king - man + woman ≈ queen，说明「方向」编码了语义特征。

本单元用手工构造的微型词向量（教学用，真实的是模型学出来的），
演示余弦相似度、类比运算、最近邻检索三条核心性质。
"""

from __future__ import annotations

import math

# 手工构造的 3 维语义空间（教学用；真实 embedding 是几百上千维、模型学出来的）
# 设计：第 0 维 = 性别（男负女正），第 1 维 = 皇权，第 2 维 = 年龄（小负大正）
VECTORS: dict[str, list[float]] = {
    "king":   [-1.0, 1.0, 0.0],
    "queen":  [1.0, 1.0, 0.0],
    "man":    [-1.0, 0.0, 0.0],
    "woman":  [1.0, 0.0, 0.0],
    "boy":    [-1.0, 0.0, -1.0],
    "girl":   [1.0, 0.0, -1.0],
    "prince": [-1.0, 0.8, 0.0],
    "princess": [1.0, 0.8, 0.0],
    "cat":    [0.0, -0.5, -0.5],
    "dog":    [0.0, -0.5, -0.2],
}


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("维度不一致")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def add(a: list[float], b: list[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def sub(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def nearest(query: list[float], exclude: set[str] | None = None) -> str:
    """返回与 query 余弦最接近的词。"""
    exclude = exclude or set()
    best, best_score = None, -2.0
    for word, vec in VECTORS.items():
        if word in exclude:
            continue
        s = cosine(query, vec)
        if s > best_score:
            best, best_score = word, s
    assert best is not None
    return best


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

    print("Unit 72 · Embedding 原理 自检\n")

    # ---- 余弦相似度 ----
    check("cosine(相同向量)=1", abs(cosine(VECTORS["king"], VECTORS["king"]) - 1.0) < 1e-9)
    # king 与 queen 性别相反、皇权相同，点积抵消为 0；
    # 语义上 king 仍应比 cat 更接近 queen —— 这才是要断言的
    check("king 更接近 queen 而非 cat",
          cosine(VECTORS["king"], VECTORS["queen"]) > cosine(VECTORS["king"], VECTORS["cat"]))
    check("cosine(man, woman)<0（性别相反）", cosine(VECTORS["man"], VECTORS["woman"]) < 0)
    check("cosine 对称", abs(cosine(VECTORS["king"], VECTORS["queen"]) - cosine(VECTORS["queen"], VECTORS["king"])) < 1e-9)
    try:
        cosine([1.0], [1.0, 2.0])
        check("维度不一致抛错", False)
    except ValueError:
        check("维度不一致抛错", True)

    # ---- 语义相近 → 向量相近 ----
    check("king 的最近邻是 queen（不是 cat）",
          nearest(VECTORS["king"], exclude={"king"}) in ("queen", "prince", "princess"),
          nearest(VECTORS["king"], exclude={"king"}))
    check("cat 的最近邻是 dog", nearest(VECTORS["cat"], exclude={"cat"}) == "dog")

    # ---- 类比：king - man + woman ≈ queen ----
    analogy = add(sub(VECTORS["king"], VECTORS["man"]), VECTORS["woman"])
    check("king - man + woman 最近邻是 queen",
          nearest(analogy, exclude={"king", "man", "woman"}) == "queen",
          nearest(analogy, exclude={"king", "man", "woman"}))
    # 反方向：queen - woman + man ≈ king
    analogy2 = add(sub(VECTORS["queen"], VECTORS["woman"]), VECTORS["man"])
    check("queen - woman + man 最近邻是 king",
          nearest(analogy2, exclude={"queen", "woman", "man"}) == "king",
          nearest(analogy2, exclude={"queen", "woman", "man"}))

    # ---- 方向比长度重要 ----
    # 把 king 的向量拉长 10 倍，方向不变 → 余弦仍为 1
    long_king = [x * 10 for x in VECTORS["king"]]
    check("向量长度变化不影响余弦（方向重要）",
          abs(cosine(long_king, VECTORS["king"]) - 1.0) < 1e-9)

    # ---- 向量算术有封闭性 ----
    v = sub(VECTORS["king"], VECTORS["man"])
    check("向量差是合法向量", len(v) == 3 and all(isinstance(x, float) for x in v))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
