#!/usr/bin/env python3
"""扫描仓库中的真实个人信息（PII）与硬编码密钥。

用法：
    python3 00-setup/check-pii.py          # 扫描全仓库
    python3 00-setup/check-pii.py --staged # 只扫描待提交的改动

退出码：0 = 干净，1 = 发现可疑内容

为什么需要这个脚本：
    本仓库公开可见。示例数据里的真实姓名、手机号、学校、公司名
    一旦提交，即使后来改写历史，GitHub 上的孤儿提交仍可能残留。
    所以必须在提交前拦住。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT_EXT = {".py", ".md", ".html", ".css", ".js", ".ts", ".sh", ".json", ".toml", ".txt", ".yaml", ".yml"}
TEXT_NAMES = {".gitignore", ".env.example", ".python-version", "SECURITY.md"}

# 允许出现的示例值（白名单）：example.com 是 RFC 2606 保留域名
WHITELIST_DOMAINS = ("example.com", "example.org", "example.net", "localhost")
# SSH 远程地址形如 git@github.com，不是邮箱
SSH_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "gitee.com", "ssh.github.com")
# 明确虚构的人名/机构名，不应报错
FICTIONAL_VALUES = ("张三", "李四", "王五", "赵六", "王小明", "某某", "示例", "某", "用户", "test", "demo")

PATTERNS: dict[str, re.Pattern[str]] = {
    "手机号": re.compile(r"(?<![\d*])1[3-9]\d{9}(?![\d*])"),
    "座机": re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)"),
    "身份证": re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
    "真实邮箱": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "硬编码密钥": re.compile(r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "姓名字段": re.compile(r"[\"']?(name|姓名)[\"']?\s*[:=：]\s*[\"']([\u4e00-\u9fa5]{2,4})[\"']"),
    "学校": re.compile(r"[\u4e00-\u9fa5]{2,8}(大学|学院)"),
    "公司": re.compile(r"[\u4e00-\u9fa5]{2,10}(有限公司|股份有限公司)"),
    "详细地址": re.compile(r"[\u4e00-\u9fa5]{2,6}(省|市)[\u4e00-\u9fa5]{2,6}(区|县)[\u4e00-\u9fa5]{2,12}(路|街|号)"),
}

# 这个文件自己的正则里含「有限公司」等字样，跳过自身
SELF_PATH = "00-setup/check-pii.py"

# 常见误报：技术名词里包含「学院/大学」等字样
FALSE_POSITIVE_HINTS = ("某某", "示例", "泛指", "虚构")


def iter_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in TEXT_EXT or p.name in TEXT_NAMES:
            out.append(p)
    return out


def scan_staged() -> int:
    """只扫 git 已暂存的改动。"""
    r = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "-U0"],
        capture_output=True, text=True,
    )
    added = [ln[1:] for ln in r.stdout.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    return _report([("(staged)", i + 1, line) for i, line in enumerate(added)])


def _report(hits: list[tuple[str, int, str]]) -> int:
    if not hits:
        print("✅ 未发现真实个人信息或密钥")
        return 0
    print(f"❌ 发现 {len(hits)} 处可疑内容：\n")
    for rel, ln, text in hits:
        print(f"  {rel}:{ln}")
        print(f"    {text}\n")
    print("处理建议：改为虚构示例数据（张三 / 某某大学 / 某科技公司 / example.com）")
    return 1


def main() -> int:
    if "--staged" in sys.argv:
        return scan_staged()

    hits: list[tuple[str, int, str]] = []
    for path in iter_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(ROOT))
        if rel == SELF_PATH:
            continue  # 跳过扫描器自身
        for i, line in enumerate(lines, 1):
            if any(h in line for h in FALSE_POSITIVE_HINTS):
                continue
            # 文档里举的反例可以显式标注放行
            if "pii-check: allow" in line:
                continue
            for label, pat in PATTERNS.items():
                for m in pat.finditer(line):
                    matched = m.group(0)
                    # SSH 远程地址不是邮箱
                    if label == "真实邮箱" and any(h in matched for h in SSH_HOSTS):
                        continue
                    # 邮箱白名单
                    if label == "真实邮箱" and any(d in matched for d in WHITELIST_DOMAINS):
                        continue
                    # 姓名字段：组 2 是等号/冒号后面的名字
                    if label == "姓名字段":
                        value = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                        if any(f in value for f in FICTIONAL_VALUES):
                            continue
                    # 学校 / 公司：含虚构标记的跳过
                    if label in ("学校", "公司") and any(f in matched for f in FICTIONAL_VALUES):
                        continue
                    # 脱敏号码（含 *）跳过
                    if "*" in matched:
                        continue
                    hits.append((rel, i, f"[{label}] {line.strip()[:130]}"))
                    break

    return _report(hits)


if __name__ == "__main__":
    sys.exit(main())
