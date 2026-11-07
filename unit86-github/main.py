"""Unit 86 · GitHub 整理：仓库卫生检查（面试前必做）。

核心认知：GitHub 是技术简历的第二页。面试官会看：
  1. README 有没有（能不能 30 秒看懂项目）
  2. .gitignore 有没有（.env/.venv 进没进库）
  3. 有没有密钥泄露（历史 commit 也算）
  4. commit 信息是否可读（feat/fix/docs 规范）
"""

from __future__ import annotations

import re
from pathlib import Path


def check_repo(root: Path) -> dict:
    issues = []
    ok = []
    if (root / "README.md").exists():
        ok.append("README 存在")
    else:
        issues.append("缺 README.md")
    if (root / ".gitignore").exists():
        gi = (root / ".gitignore").read_text(encoding="utf-8")
        for must in (".env", ".venv", "__pycache__"):
            if must not in gi:
                issues.append(f".gitignore 缺 {must}")
            else:
                ok.append(f"忽略 {must}")
    else:
        issues.append("缺 .gitignore")
    if (root / "LICENSE").exists():
        ok.append("有 LICENSE")
    else:
        issues.append("缺 LICENSE（开源必须）")
    return {"ok": ok, "issues": issues}


COMMIT_RE = re.compile(r"^(feat|fix|docs|chore|refactor|test)(\(.+\))?:")


def check_commit_messages(messages: list[str]) -> dict:
    good = [m for m in messages if COMMIT_RE.match(m)]
    return {"total": len(messages), "conventional": len(good)}


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

    print("Unit 86 · GitHub 整理 自检\n")
    # 用当前仓库自己作检查对象
    repo_root = Path(__file__).resolve().parent.parent
    r = check_repo(repo_root)
    check("README 存在", "README 存在" in r["ok"], str(r["ok"]))
    check(".gitignore 忽略 .env", "忽略 .env" in r["ok"])
    check(".gitignore 忽略 .venv", "忽略 .venv" in r["ok"])
    check("无关键问题", all("缺 .env" not in i for i in r["issues"]), str(r["issues"]))

    # commit 规范
    msgs = ["feat: 新增 XX", "fix(unit20): 修 bug", "随便改改", "docs: 更新说明"]
    cm = check_commit_messages(msgs)
    check("规范 commit 识别", cm["conventional"] == 3, str(cm))
    check("不规范 commit 被计数", cm["total"] - cm["conventional"] == 1)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
