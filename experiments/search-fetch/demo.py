"""DeepSeek Search / Fetch API 试用（2026-10-17 上线的新能力）。

不是课程单元，是一次「新能力试用」，所以刻意写成零依赖：
    只用 Python 标准库（urllib / json / argparse），不需要 pip install，
    也不吃某个单元的 .venv —— 任何 python3 都能跑。

它封装两个接口：
    Search  POST https://api.deepseek.com/v1/tools/search   按 query 搜网页
    Fetch   POST https://api.deepseek.com/v1/tools/fetch    抓单个网页 → Markdown

鉴权与 DeepSeek 主 API 相同（同一个 Bearer Key）。计费：Search / Fetch 都是
5 元 / 1000 次**成功**请求（失败、无结果、超时不计费）。

用法：
    python3 demo.py --selfcheck                     # 纯离线，校验请求构造与响应解析
    python3 demo.py -q "DeepSeek V4.1 Flash" --dry-run
    python3 demo.py -q "DeepSeek V4.1 Flash" --count 5 --fetch-top --show-raw

Key 的取法（按顺序）：
    1. --env-file 指定的文件
    2. 从当前目录往上找第一个 .env（所以在本目录跑会自动用到仓库里的 .env）
    3. 环境变量 DEEPSEEK_API_KEY

原始响应一律落盘到 logs/（.gitignore 已忽略），符合仓库「原始响应必须可见」的约定。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE_URL = "https://api.deepseek.com"
SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"
LOG_DIR = Path(__file__).parent / "logs"


# --------------------------------------------------------------------- 配置
def load_api_key(env_file: str | None = None) -> str:
    """按 指定文件 → 向上找 .env → 环境变量 的顺序取 Key。"""
    candidates: list[Path] = []
    if env_file:
        candidates.append(Path(env_file))
    else:
        for parent in [Path.cwd(), *Path.cwd().parents]:
            if (parent / ".env").is_file():
                candidates.append(parent / ".env")
                break

    for path in candidates:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "DEEPSEEK_API_KEY":
                v = v.strip().strip("'\"")
                if v and "你的" not in v:
                    return v
                raise SystemExit(f"❌ {path} 里的 DEEPSEEK_API_KEY 还是占位值。")

    v = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if v:
        return v
    raise SystemExit(
        "❌ 找不到 DEEPSEEK_API_KEY。用 --env-file 指定，或 export DEEPSEEK_API_KEY=sk-..."
    )


def build_request(path: str, body: dict, api_key: str, base_url: str = BASE_URL) -> urllib.request.Request:
    """构造请求。单独抽出来，selfcheck 才能在不联网的情况下断言请求长什么样。"""
    return urllib.request.Request(
        url=f"{base_url.rstrip('/')}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # 有 UA 更不容易被目标站点为难；curl 默认会带，urllib 默认不带
            "User-Agent": "ai-journey-search-demo/1.0",
        },
        method="POST",
    )


@dataclass
class CallResult:
    ok: bool
    status: int
    payload: dict = field(default_factory=dict)
    error: str = ""
    raw: str = ""
    elapsed_ms: int = 0


FRIENDLY = {
    400: "请求不合法（字段值问题，改参数重试）",
    401: "API Key 无效或缺失",
    402: "余额不足，先充值",
    403: "Fetch 被安全策略拒绝（这个 URL 抓不了）",
    404: "抓不到可用的网页内容（URL 失效 / 站点拦截 / 不是网页）",
    408: "读取请求体超时",
    413: "请求体过大",
    429: "触发限速（Search 100 QPS / Fetch 25 QPS），退避后重试",
    500: "服务端内部错误（可重试）",
    504: "下游超时（可重试）",
}


def call(path: str, body: dict, api_key: str, timeout: float = 30.0) -> CallResult:
    """发一次请求，把「网络异常 / HTTP 错误 / 成功」统一成 CallResult。"""
    req = build_request(path, body, api_key)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
        elapsed = int((time.perf_counter() - start) * 1000)
        return CallResult(True, status, json.loads(raw), raw=raw, elapsed_ms=elapsed)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        elapsed = int((time.perf_counter() - start) * 1000)
        msg = FRIENDLY.get(exc.code, f"HTTP {exc.code}")
        # 服务端会返回 OpenAI 兼容的 error 结构，能解析就说得更具体
        try:
            detail = json.loads(raw)["error"]["message"]
            msg = f"{msg}｜{detail}"
        except Exception:
            pass
        return CallResult(False, exc.code, error=msg, raw=raw, elapsed_ms=elapsed)
    except urllib.error.URLError as exc:
        return CallResult(False, 0, error=f"连不上 api.deepseek.com：{exc.reason}")
    except TimeoutError:
        return CallResult(False, 0, error="请求超时（30s）")
    except json.JSONDecodeError as exc:
        return CallResult(False, 0, error=f"响应不是合法 JSON：{exc}")


# --------------------------------------------------------------------- 解析
def parse_results(payload: dict) -> list[dict]:
    """把 results 规范化，缺字段也给出默认值 —— 线上数据经常缺 site_name / date。"""
    out = []
    for r in payload.get("results") or []:
        out.append(
            {
                "score": float(r.get("rerank_score") or 0.0),
                "title": (r.get("title") or "").strip() or "(无标题)",
                "url": r.get("url") or "",
                "snippet": (r.get("snippet") or "").strip(),
                "summary": (r.get("summary") or "").strip(),
                "content": (r.get("content") or "").strip(),
                "site": r.get("site_name") or "",
                "date": (r.get("date") or "")[:10],
                "authority": r.get("authority", -1),
            }
        )
    return out


def quality(score: float) -> str:
    """>=1.5 官方说的「优质结果」，>=3 是强相关。"""
    if score >= 3.0:
        return "★★★"
    if score >= 1.5:
        return "★★"
    if score > 0:
        return "★"
    return "·"


def save_raw(tag: str, text: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.json"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------- 展示
def print_results(query: str, results: list[dict]) -> None:
    print(f"\n🔎 query：{query}    命中 {len(results)} 条")
    if not results:
        print("（空结果。注意：空结果不计费）")
        return
    for i, r in enumerate(results, 1):
        flag = "⭐" if r["authority"] >= 3 else "  "
        print(f"\n[{i}] {quality(r['score'])} {r['score']:.2f} {flag} {r['title']}")
        print(f"    {r['url']}")
        meta = " · ".join(x for x in (r["site"], r["date"], f"authority={r['authority']}") if x)
        if meta:
            print(f"    {meta}")
        if r["summary"]:
            print(f"    {r['summary'][:180]}…" if len(r["summary"]) > 180 else f"    {r['summary']}")


# --------------------------------------------------------------------- selfcheck
def selfcheck() -> int:
    """不联网、不花钱，只验证「请求构造」和「响应解析」两段纯逻辑。"""
    checks: list[tuple[str, bool]] = []

    req = build_request(SEARCH_PATH, {"query": "x", "count": 3}, "sk-test")
    checks.append(("Search 用到 POST", req.get_method() == "POST"))
    checks.append(("URL 拼对", req.full_url == f"{BASE_URL}{SEARCH_PATH}"))
    checks.append(("Authorization 头正确", req.get_header("Authorization") == "Bearer sk-test"))
    checks.append(("Content-Type 是 json", req.get_header("Content-type") == "application/json"))
    checks.append(("body 是 UTF-8 JSON 且中文不转义",
                   json.loads(req.data.decode("utf-8")) == {"query": "x", "count": 3}))

    sample = {
        "results": [
            {
                "rerank_score": 2.4,
                "title": "示例",
                "url": "https://example.com/a",
                "snippet": "短摘要",
                "summary": "长摘要",
                "content": "正文",
                "site_name": "Example",
                "date": "2026-06-01T00:00:00+00:00",
                "authority": 3,
            },
            {"title": None, "url": None},  # 故意缺字段
        ]
    }
    parsed = parse_results(sample)
    checks.append(("完整字段解析正确", parsed[0]["score"] == 2.4 and parsed[0]["date"] == "2026-06-01"))
    checks.append(("缺字段不炸、给默认值", parsed[1]["title"] == "(无标题)" and parsed[1]["url"] == ""))
    checks.append(("空字段被 strip", parsed[1]["snippet"] == ""))
    checks.append(("空 results 返回空列表", parse_results({}) == []))
    checks.append(("quality 分档", (quality(3.2), quality(1.6), quality(0.4), quality(0)) == ("★★★", "★★", "★", "·")))
    checks.append(("404 有中文解释", "抓不到" in FRIENDLY[404]))

    print("Search / Fetch 试用 · 离线自检\n")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    failed = sum(1 for _, ok in checks if not ok)
    print(f"\n结果：{len(checks) - failed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DeepSeek Search / Fetch API 试用（零依赖）")
    p.add_argument("-q", "--query", help="搜索词（1–128 字符）")
    p.add_argument("--count", type=int, default=5, help="返回条数 1–50（默认 5）")
    p.add_argument("--need-content", action="store_true", help="要求返回网页正文（体积大）")
    p.add_argument("--fetch-top", action="store_true", help="再抓第一条结果的正文（Markdown）")
    p.add_argument("--dry-run", action="store_true", help="只打印请求，不发送")
    p.add_argument("--show-raw", action="store_true", help="打印原始 JSON 响应")
    p.add_argument("--env-file", help="指定 .env 路径")
    p.add_argument("--selfcheck", action="store_true", help="纯离线自检")
    args = p.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if not args.query:
        p.error("需要 -q/--query（或用 --selfcheck）")
    if not 1 <= args.count <= 50:
        p.error("--count 必须在 1–50 之间")

    body = {"query": args.query, "count": args.count, "need_content": args.need_content}

    if args.dry_run:
        print("（dry-run，不发送请求）")
        print(f"POST {BASE_URL}{SEARCH_PATH}")
        print("Authorization: Bearer <已隐藏>")
        print("Content-Type: application/json")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0

    key = load_api_key(args.env_file)

    res = call(SEARCH_PATH, body, key)
    if not res.ok:
        print(f"❌ Search 失败（HTTP {res.status}）：{res.error}")
        if res.raw:
            print(f"   原始响应已存：{save_raw('search-error', res.raw)}")
        return 1

    log = save_raw("search", res.raw)
    if args.show_raw:
        print("原始响应：")
        print(json.dumps(res.payload, ensure_ascii=False, indent=2))

    results = parse_results(res.payload)
    print_results(args.query, results)
    print(f"\n耗时 {res.elapsed_ms}ms · 原始响应：{log}")

    if args.fetch_top and results:
        url = results[0]["url"]
        print(f"\n📄 Fetch：{url}")
        fr = call(FETCH_PATH, {"url": url}, key)
        if not fr.ok:
            print(f"❌ Fetch 失败（HTTP {fr.status}）：{fr.error}")
            return 1
        flog = save_raw("fetch", fr.raw)
        if args.show_raw:
            print(json.dumps(fr.payload, ensure_ascii=False, indent=2))
        content = (fr.payload.get("content") or "")
        print(f"标题：{fr.payload.get('title') or '(无)'}")
        print(f"正文 {len(content)} 字符，前 600 字符：\n")
        print(content[:600] + ("…" if len(content) > 600 else ""))
        print(f"\n耗时 {fr.elapsed_ms}ms · 原始响应：{flog}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
