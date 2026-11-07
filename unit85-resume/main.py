"""Unit 85 · 简历：自动校验器（不替你写，替你查）。

简历是求职漏斗的入口，要过四道硬检查：
  1. 无 PII —— 真实手机号/邮箱不要直接放公开仓库
  2. 动词开头 —— 「做了/负责」弱，「构建/优化/设计」强
  3. 每条有量化结果 —— 没数字等于没做
  4. 长度适中 —— 太长没人看，太短没信息

启动：uv run uvicorn main:app --port 8085
自检：uv run python main.py
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# 检测规则
# ---------------------------------------------------------------------------
PII_PATTERNS = {
    "手机号": re.compile(r"(?<![\d*])1[3-9]\d{9}(?![\d*])"),
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "身份证": re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
}

WEAK_VERBS = ["做了", "负责", "参与", "帮忙", "协助", "跟进"]
STRONG_VERBS = ["构建", "设计", "优化", "实现", "搭建", "开发", "重构", "主导", "落地", "抽象"]

# 量化信号：百分比、倍数、时间、数量
METRIC_PATTERNS = [
    re.compile(r"\d+\s*%"),
    re.compile(r"\d+(\.\d+)?\s*x\b", re.IGNORECASE),
    re.compile(r"\d+\s*(ms|s|分钟|小时|天|周)"),
    re.compile(r"(提升|降低|缩短|减少|增长)\s*\d"),
    re.compile(r"\d+\s*(个|条|张|台|人|次)"),
]


def find_pii(text: str) -> list[dict]:
    out = []
    for label, pat in PII_PATTERNS.items():
        for m in pat.finditer(text):
            out.append({"type": label, "value": m.group(0)})
    return out


def get_bullets(text: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def audit_resume(text: str, min_bullets: int = 3) -> dict:
    """跑完整校验，返回问题清单与得分。"""
    bullets = get_bullets(text)
    issues: list[str] = []
    warnings: list[str] = []

    # 1. PII
    pii = find_pii(text)
    if pii:
        issues.append(f"发现 {len(pii)} 处个人信息（{', '.join(p['type'] for p in pii)}），公开仓库请脱敏")

    # 2. 条目数
    if len(bullets) < min_bullets:
        issues.append(f"项目经历只有 {len(bullets)} 条，建议至少 {min_bullets} 条")

    # 3. 弱动词
    weak = [b for b in bullets if any(b.startswith(v) or v in b[:6] for v in WEAK_VERBS)]
    if weak:
        warnings.append(f"{len(weak)} 条用弱动词开头（{WEAK_VERBS[:3]}…），换成强动词")

    # 4. 无量化
    no_metric = [b for b in bullets if not any(p.search(b) for p in METRIC_PATTERNS)]
    if no_metric:
        issues.append(f"{len(no_metric)} 条没有量化结果（数字/百分比/耗时）")

    # 5. 长度
    total_chars = len(text)
    if total_chars > 4000:
        warnings.append(f"正文 {total_chars} 字，偏长（建议 2000 字内）")

    score = 100
    score -= len(pii) * 20
    score -= len(no_metric) * 10
    score -= len(weak) * 5
    score -= max(0, min_bullets - len(bullets)) * 10
    score = max(0, score)

    return {
        "score": score,
        "bullets": len(bullets),
        "strong_verb_count": sum(1 for b in bullets if any(b.startswith(v) for v in STRONG_VERBS)),
        "with_metric_count": len(bullets) - len(no_metric),
        "issues": issues,
        "warnings": warnings,
        "passed": len(issues) == 0,
    }


HERE = Path(__file__).resolve().parent
SAMPLE = """# 张三 · AI 应用开发工程师

## 项目经历

- 构建企业知识库 RAG 系统，回答准确率从 70% 提升到 92%
- 设计 Agent 工具调用框架，支持 5 个工具，越权调用拦截率 100%
- 优化向量检索延迟，P95 从 800ms 降到 120ms
"""

app = FastAPI(title="Unit 85 · 简历校验器", version="0.1.0")


@app.post("/audit", summary="校验简历文本（body 传纯文本）")
async def audit(text: str) -> dict:
    if not text.strip():
        raise HTTPException(status_code=400, detail="简历内容不能为空")
    return audit_resume(text)


@app.get("/audit/sample", summary="校验内置示例简历")
async def audit_sample() -> dict:
    return audit_resume(SAMPLE)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "GET /audit/sample 看示例；POST /audit?text=... 校验自己的简历"})


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 85 · 简历校验器 自检\n")

    # ---- 示例简历应该通过 ----
    r = audit_resume(SAMPLE)
    check("示例简历无 PII", not find_pii(SAMPLE))
    check("示例简历 3 条经历", r["bullets"] == 3, str(r["bullets"]))
    check("示例简历通过", r["passed"] is True, str(r["issues"]))
    check("三条都有量化", r["with_metric_count"] == 3, str(r["with_metric_count"]))
    check("强动词计数 >= 3", r["strong_verb_count"] >= 3, str(r["strong_verb_count"]))
    check("得分 100", r["score"] == 100, str(r["score"]))

    # ---- 反例：弱动词 + 无量化 ----
    weak_resume = """# 简历

- 负责日常维护工作
- 参与项目开发
- 帮忙处理线上问题
"""
    r2 = audit_resume(weak_resume)
    check("弱动词被识别", len(r2["warnings"]) > 0, str(r2["warnings"]))
    check("无量化被识别", any("量化" in i for i in r2["issues"]), str(r2["issues"]))
    check("弱简历不通过", r2["passed"] is False)
    check("弱简历得分低", r2["score"] < 100, str(r2["score"]))

    # ---- PII 检测 ----
    bad = "# 简历\n\n- 构建系统，提升 30%\n- 联系 13812345678\n- 优化性能 2x\n"  # pii-check: allow（测试夹具）
    r3 = audit_resume(bad)
    check("PII 被检测", len(find_pii(bad)) >= 1)
    check("PII 导致不通过", r3["passed"] is False)
    check("PII 扣分明显", r3["score"] <= 80, str(r3["score"]))

    # ---- 条目不足 ----
    r4 = audit_resume("# 简历\n\n- 构建系统，提升 30%\n")
    check("条目不足被识别", any("至少" in i for i in r4["issues"]), str(r4["issues"]))

    # ---- 量化信号识别 ----
    check("识别百分比", audit_resume("- 优化准确率 92%\n- 降低延迟 30%\n- 提升 2x\n")["with_metric_count"] == 3)
    check("识别耗时", bool(METRIC_PATTERNS[2].search("P95 从 800ms 降到 120ms")))

    # ---- HTTP ----
    c = TestClient(app)
    r5 = c.get("/audit/sample")
    check("HTTP 示例校验 200", r5.status_code == 200)
    check("HTTP 示例通过", r5.json()["passed"] is True)
    r6 = c.post("/audit", params={"text": SAMPLE})
    check("HTTP 自定义校验 200", r6.status_code == 200)
    check("HTTP 空内容 400", c.post("/audit", params={"text": "  "}).status_code == 400)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())