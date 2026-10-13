"""Day 3 附加 · 怎么拿到响应头（x-ds-trace-id 等）—— 四种方式实测对比。

直接运行：
    python headers_demo.py

本文件在 2026-10-14 于本机实测通过（openai 3.28.0 / DeepSeek）。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from httpx2 import Client  # ⚠️ 注意：openai 3.28 内部用的是 httpx2，不是 httpx
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    timeout=60,
)
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")


def demo_a_with_raw_response() -> str | None:
    """方式 A（首选）：with_raw_response —— 一次调用同时拿到原始 body、响应头、状态码。"""
    print("=" * 70)
    print("方式 A：with_raw_response（推荐，debug_response.py 用的就是它）")
    print("=" * 70)

    raw = client.chat.completions.with_raw_response.create(
        model=MODEL,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )

    headers = dict(raw.headers)  # 全部响应头，键是小写
    print(f"HTTP 状态码        : {raw.status_code}")
    print(f"请求 URL           : {raw.url}")
    print(f"响应头数量          : {len(headers)}")
    print(f"x-ds-trace-id      : {headers.get('x-ds-trace-id')}")
    print(f"server             : {headers.get('server')}")
    print(f"eo-cache-status    : {headers.get('eo-cache-status')}   ← 腾讯 EdgeOne CDN 的缓存命中情况")
    print(f"raw.http_response  : {raw.http_response!r}   ← 底层 httpx2.Response 对象")
    print()
    print("  完整 13 个头：")
    for k, v in headers.items():
        print(f"    {k}: {v}")

    # 想让业务代码也能用，就先 parse 出正常对象：
    resp = raw.parse()
    print(f"\n  raw.parse() 之后照常用：{resp.choices[0].message.content!r}")
    return headers.get("x-ds-trace-id")


def demo_b_event_hooks() -> None:
    """方式 B（生产级）：给 HTTP 客户端挂事件钩子，所有请求自动记录头，业务代码零改动。"""
    print()
    print("=" * 70)
    print("方式 B：httpx2 事件钩子（生产级，日志里自动带 trace-id）")
    print("=" * 70)

    records: list[dict] = []

    def on_response(response):
        records.append(
            {
                "status": response.status_code,
                "trace_id": response.headers.get("x-ds-trace-id"),
                "url": str(response.request.url),
            }
        )

    http_client = Client(event_hooks={"response": [on_response]})
    hooked = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=60,
        http_client=http_client,  # ← 唯一的新增，业务代码不用改
    )
    # Client 是用

    hooked.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )

    for r in records:
        print(f"  自动记录 → {r['status']} {r['url']}  trace_id={r['trace_id']}")
    print("  用途：Day 13 做日志系统时，把 trace_id 写进日志，线上报错可直接拿它找服务商。")


def demo_c_and_d_notes() -> None:
    """方式 C / D 不适合写成代码，这里只给命令。"""
    print()
    print("=" * 70)
    print("方式 C / D：命令行拿响应头")
    print("=" * 70)
    print("""
方式 C：SDK 自己的调试日志（注意局限）
    OPENAI_LOG=debug python 01_hello.py 2>&1 | grep -i "http"
    → 实测输出：
        Building HTTP request: method=POST retries_taken=0
        Sending HTTP Request: POST
        HTTP Response: POST 200
    ⚠️ 它只告诉你「状态码」，**不打印响应头**，所以拿不到 x-ds-trace-id。
       它的价值在于看「重试了几次（retries_taken）」——Day 13 调试重试逻辑时很有用。

方式 D：curl 裸请求（不依赖 SDK，排查「到底是 SDK 还是网络的问题」）
    set -a; . ./.env; set +a
    curl -sS -D - -o /dev/null -X POST https://api.deepseek.com/chat/completions \\
      -H "Authorization: Bearer $DEEPSEEK_API_KEY" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
    → -D - 把响应头打到 stdout，-o /dev/null 丢掉 body。实测输出 HTTP/2 200 + 12 个头。

三个容易踩的坑（本机实测）：
  1. 普通 create() 的返回对象上 **没有** .response / ._response / .request_id；
     只有 ._request_id（DeepSeek 不返回 x-request-id，所以它是 None）。
     要拿头就必须用 with_raw_response 或事件钩子。
  2. openai 3.28 内部用 **httpx2**，写 `import httpx` 会 ModuleNotFoundError；
     自定义客户端要 `from httpx2 import Client`。
  3. DeepSeek 的追踪 ID 叫 **x-ds-trace-id**，不是标准的 x-request-id；
     用 SDK 的 request_id 属性会拿到 None。
""")


if __name__ == "__main__":
    demo_a_with_raw_response()
    demo_b_event_hooks()
    demo_c_and_d_notes()
