"""Unit 86 · GitHub 整理：仓库卫生检查（面试前必做）。

GitHub 是技术简历的第二页。面试官会看：
  1. README —— 能不能 30 秒看懂这个项目
  2. .gitignore —— .env / .venv / *.db 进没进库
  3. 密钥泄露 —— 历史 commit 也算
  4. commit 信息 —— feat/fix/docs 规范吗
  5. 顶层结构 —— 目录是否可读

启动：uv run uvicorn main:app --port 8086
自检：uv run python main.py
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

COMMIT_RE = re.compile(r"^(feat|fix|docs|chore|refactor|test|perf|style)(\(.+\))?: .+")

# 不该进 git 的东西
MUST_IGNORE = [".env", ".venv", "__pycache__", "*.py[cod]", "*.db", "logs/"]

# 可能泄漏密钥的字符串模式
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
]

README_SECTIONS = ["安装", "运行", "用法", "快速开始", "结构", "完成标准"]


def audit_gitignore(text: str) -> dict:
    missing = [p for p in MUST_IGNORE if p not in text]
    return {"ok": not missing, "missing": missing, "entries": len(text.splitlines())}


def audit_commits(messages: list[str]) -> dict:
    conventional = [m for m in messages if COMMIT_RE.match(m)]
    return {
        "total": len(messages),
        "conventional": len(conventional),
        "rate": round(len(conventional) / len(messages), 2) if messages else 0.0,
        "bad_examples": [m for m in messages if not COMMIT_RE.match(m)][:3],
    }


def audit_readme(text: str) -> dict:
    if not text.strip():
        return {"ok": False, "issues": ["README 为空"]}
    issues = []
    if len(text) < 300:
        issues.append(f"README 只有 {len(text)} 字，太短（面试官 30 秒看不懂）")
    if not any(s in text for s in README_SECTIONS):
        issues.append("缺「怎么跑」类小节（安装/运行/用法）")
    if "```" not in text:
        issues.append("没有代码块示例")
    return {"ok": not issues, "issues": issues, "chars": len(text)}


def scan_secrets(text: str) -> list[str]:
    hits = []
    for pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0)[:12] + "…")
    return hits


def audit_repo(path: str | Path, commits: list[str] | None = None) -> dict:
    """对一个真实目录跑整套卫生检查。"""
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"目录不存在：{root}")

    checks: dict = {}
    score = 100

    # README
    rd = root / "README.md"
    if rd.exists():
        checks["readme"] = audit_readme(rd.read_text(encoding="utf-8", errors="ignore"))
        if not checks["readme"]["ok"]:
            score -= 15
    else:
        checks["readme"] = {"ok": False, "issues": ["缺 README.md"]}
        score -= 30

    # .gitignore
    gi = root / ".gitignore"
    if gi.exists():
        checks["gitignore"] = audit_gitignore(gi.read_text(encoding="utf-8", errors="ignore"))
        score -= len(checks["gitignore"]["missing"]) * 10
    else:
        checks["gitignore"] = {"ok": False, "missing": MUST_IGNORE}
        score -= 40

    # 敏感文件是否真的存在
    leaked = [n for n in (".env", ".env.local") if (root / n).exists()]
    checks["sensitive_files"] = {"ok": not leaked, "found": leaked}
    if leaked:
        score -= 30

    # 密钥扫描（扫顶层 py/md）
    secret_hits: list[str] = []
    for f in list(root.glob("*.py"))[:20] + list(root.glob("*.md"))[:20]:
        try:
            secret_hits += scan_secrets(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    checks["secrets"] = {"ok": not secret_hits, "hits": secret_hits}
    if secret_hits:
        score -= 40

    # commit 规范（外部传入）
    if commits is not None:
        checks["commits"] = audit_commits(commits)
        if checks["commits"]["rate"] < 0.8:
            score -= 10
    else:
        checks["commits"] = {"total": 0, "conventional": 0, "rate": 0.0, "skipped": True}

    return {
        "score": max(0, score),
        "checks": checks,
        "passed": score >= 80 and checks["readme"]["ok"] and checks["gitignore"]["ok"],
    }


HERE = Path(__file__).resolve().parent

app = FastAPI(title="Unit 86 · GitHub 整理", version="0.1.0")


@app.get("/audit", summary="检查本单元目录的卫生（示例）")
async def audit_here() -> dict:
    return audit_repo(HERE)


@app.get("/audit/commits", summary="检查一组 commit message 的规范率")
async def audit_commits_endpoint(messages: str) -> dict:
    msgs = [m.strip() for m in messages.split("|") if m.strip()]
    if not msgs:
        raise HTTPException(status_code=400, detail="用 | 分隔多条 commit message")
    return audit_commits(msgs)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/audit 检查本目录；/audit/commits?messages=feat:+x|fix: y"})


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    import tempfile

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

    print("Unit 86 · GitHub 整理 自检\n")

    # ---- .gitignore ----
    gi = audit_gitignore(".env\n.venv\n__pycache__/\n*.py[cod]\n*.db\nlogs/\n")
    check("完整 gitignore 通过", gi["ok"] is True, str(gi["missing"]))
    gi2 = audit_gitignore(".env\n")
    check("缺项被识别", gi2["ok"] is False and len(gi2["missing"]) == 5, str(gi2["missing"]))

    # ---- commit 规范 ----
    cm = audit_commits(["feat: 新增", "fix(unit61): 修 bug", "docs: 说明", "随便改改"])
    check("规范 commit 识别 3 条", cm["conventional"] == 3, str(cm))
    check("不规范被列出", len(cm["bad_examples"]) == 1)
    check("规范率 0.75", cm["rate"] == 0.75, str(cm["rate"]))

    # ---- README ----
    long_readme = "# 项目\n\n## 安装\n\n```bash\npip install -r requirements.txt\n```\n\n## 运行\n\n" + "说明" * 200
    check("合格 README 通过", audit_readme(long_readme)["ok"] is True, str(audit_readme(long_readme)))
    check("空 README 不通过", audit_readme("")["ok"] is False)
    check("过短 README 不通过", any("太短" in i for i in audit_readme("# 项目")["issues"]))
    check("无代码块被识别", any("代码块" in i for i in audit_readme("## 安装\n" + "字" * 400)["issues"]))

    # ---- 密钥扫描 ----
    check("无密钥返回空", scan_secrets("普通代码 print(1)") == [])
    # 构造一个假 token（不是真实凭据，仅测试匹配）
    fake = "ghp_" + "A" * 24
    check("检测到 ghp_ token", len(scan_secrets(fake)) == 1)
    check("检测到 sk- token", len(scan_secrets("sk-" + "B" * 24)) == 1)

    # ---- 目录审计（用真实目录）----
    rep = audit_repo(HERE)
    check("本目录审计 200", "score" in rep)
    check("本目录有 README", rep["checks"]["readme"]["ok"] is True, str(rep["checks"]["readme"]))
    check("本目录无 .env 泄漏", rep["checks"]["sensitive_files"]["ok"] is True)
    check("本目录无密钥", rep["checks"]["secrets"]["ok"] is True, str(rep["checks"]["secrets"]))

    # ---- 坏目录 ----
    with tempfile.TemporaryDirectory() as td:
        rep2 = audit_repo(td)
        check("空目录不通过", rep2["passed"] is False)
        check("空目录缺 README", rep2["checks"]["readme"]["ok"] is False)
        check("空目录缺 gitignore", rep2["checks"]["gitignore"]["ok"] is False)
        (Path(td) / ".env").write_text("X=1")
        rep3 = audit_repo(td)
        check("发现 .env 泄漏", rep3["checks"]["sensitive_files"]["ok"] is False)

    # ---- HTTP ----
    c = TestClient(app)
    check("HTTP /audit 200", c.get("/audit").status_code == 200)
    check("HTTP /audit 有 score", "score" in c.get("/audit").json())
    check("HTTP /audit/commits 200",
          c.get("/audit/commits", params={"messages": "feat: a|fix: b"}).status_code == 200)
    check("HTTP 空 messages 400", c.get("/audit/commits", params={"messages": ""}).status_code == 400)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())