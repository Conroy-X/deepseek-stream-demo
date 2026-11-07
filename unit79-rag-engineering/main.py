"""Unit 79 · RAG 工程化：把检索/重排/生成拼成配置化流水线。

核心认知：项目打磨阶段，把 Unit 76/77/78 的零件拼成一个**配置驱动**的流水线。
改参数（top_k / threshold / 是否重排）不碰代码，只改配置——这是可维护性的关键。
"""

from __future__ import annotations

from dataclasses import dataclass

DOCS = ["退款 3-5 个工作日到账", "年假提前 3 天申请", "密码 90 天更换"]


@dataclass
class RAGConfig:
    top_k: int = 2
    threshold: float = 0.3
    use_rerank: bool = True

    def validate(self) -> None:
        if not 1 <= self.top_k <= 10:
            raise ValueError("top_k 需在 1..10")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold 需在 0..1")


def search(q: str, docs: list[str], top_k: int) -> list[tuple[int, float]]:
    import re
    q_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", q))
    scored = []
    for i, d in enumerate(docs):
        d_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", d))
        overlap = len(q_terms & d_terms) / max(1, len(q_terms))
        scored.append((i, overlap))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:top_k]


def rerank(q: str, candidates: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """假重排：同分时按文档 id 稳定排序。"""
    return sorted(candidates, key=lambda kv: (-kv[1], kv[0]))


def pipeline(q: str, cfg: RAGConfig) -> dict:
    cfg.validate()
    hits = search(q, DOCS, cfg.top_k * 2)  # 初检多拿
    if cfg.use_rerank:
        hits = rerank(q, hits)[: cfg.top_k]
    best = hits[0] if hits and hits[0][1] >= cfg.threshold else None
    if best is None:
        return {"answer": "资料里没有相关内容。", "refused": True, "hits": []}
    return {"answer": DOCS[best[0]], "refused": False, "hits": [{"id": i, "score": round(s, 4)} for i, s in hits]}


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

    print("Unit 79 · RAG 工程化 自检\n")
    cfg = RAGConfig(top_k=2, threshold=0.5, use_rerank=True)
    check("配置校验通过", cfg.validate() is None)
    r = pipeline("退款多久到账", cfg)
    check("命中退款", "退款" in r["answer"] and not r["refused"], str(r))
    r2 = pipeline("今天天气", cfg)
    check("无关问题拒答", r2["refused"] is True, str(r2))
    check("top_k 截断", len(r["hits"]) <= cfg.top_k)
    # 「退款多久」与退款文档只共享 2/4 字 = 0.5 < 0.9 → 拒答
    r3 = pipeline("退款多久", RAGConfig(top_k=1, threshold=0.9, use_rerank=False))
    check("高阈值拒答", r3["refused"] is True)
    try:
        RAGConfig(top_k=99).validate()
        check("非法配置抛错", False)
    except ValueError:
        check("非法配置抛错", True)
    # 关闭重排也能跑
    r4 = pipeline("退款", RAGConfig(use_rerank=False))
    check("关闭重排可跑", "退款" in r4["answer"])
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
