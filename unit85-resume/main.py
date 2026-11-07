"""Unit 85 · 简历：用校验器检查简历质量（不替你写，替你查）。

核心认知：简历不是「写满」就行，要过几道硬检查：
  1. 无 PII（真实手机号/邮箱不要直接放公开仓库）
  2. 每段经历都有「动作 + 结果」（STAR 的 A 和 R）
  3. 用动词开头（「做了/负责」弱，「构建/优化/设计」强）
"""

from __future__ import annotations

import re

RESUME_TEMPLATE = """# 张三 · AI 应用开发工程师

## 项目经历

- 构建企业知识库 RAG 系统，回答准确率从 70% 提升到 92%
- 设计 Agent 工具调用框架，支持 5 个工具，越权调用拦截率 100%
- 优化向量检索延迟，P95 从 800ms 降到 120ms
"""

WEAK_WORDS = ["做了", "负责", "参与", "帮忙"]
STRONG_WORDS = ["构建", "设计", "优化", "实现", "搭建", "开发", "重构"]

PII_PATTERNS = [
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def check_pii(text: str) -> list[str]:
    out = []
    for pat in PII_PATTERNS:
        if pat.search(text):
            out.append(pat.search(text).group(0))
    return out


def check_bullets(text: str) -> dict:
    bullets = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]
    weak = [b for b in bullets if any(w in b for w in WEAK_WORDS)]
    strong = [b for b in bullets if any(w in b for w in STRONG_WORDS)]
    has_result = [b for b in bullets if re.search(r"\d+%|\d+ms|提升|降低|缩短", b)]
    return {"total": len(bullets), "weak": weak, "strong_count": len(strong),
            "has_result_count": len(has_result), "has_result": has_result}


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

    print("Unit 85 · 简历 自检\n")
    pii = check_pii(RESUME_TEMPLATE)
    check("简历无 PII", len(pii) == 0, str(pii))
    r = check_bullets(RESUME_TEMPLATE)
    check("有 3 条项目经历", r["total"] == 3, str(r["total"]))
    check("无弱动词开头", len(r["weak"]) == 0, str(r["weak"]))
    check("每条都有结果数字", r["has_result_count"] == r["total"], str(r["has_result_count"]))
    # 反例：弱动词与无结果应被识别
    weak_resume = "- 负责日常维护\n- 参与项目开发\n"
    r2 = check_bullets(weak_resume)
    check("弱动词被识别", len(r2["weak"]) == 2, str(r2["weak"]))
    check("无结果被识别", r2["has_result_count"] == 0)
    # PII 检测
    bad = "电话 13812345678"  # pii-check: allow（测试夹具）
    check("PII 被识别", "13812345678" in check_pii(bad))  # pii-check: allow

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
