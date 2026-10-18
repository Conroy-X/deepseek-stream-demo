"""Unit 4 · LLM 调用封装（沿用 Unit 3 的 `llm.py`，并修掉当时发现的两个坑）。

与 Unit 3 版本的差异（都是 Unit 3 实测踩出来的）：
  1. `finish_reason == "length"` 时直接告警 —— `deepseek-flash` 是推理模型，
     推理 token 和正文共用 max_tokens，实测即使"只输出一个词"也要 60~100 output token
     （其中 90%+ 是推理），max_tokens 设小了回答会被悄悄截断。
  2. 记录 `last_trace_id`（来自响应头 x-ds-trace-id），实验出问题时可直接报障。
  3. 支持 extra_body 透传 —— Unit 4 要试各种参数，但注意：**未知字段会被服务端静默忽略**
     （实测传 `effort` 非法值也不报错），所以判断参数是否生效只能看 usage 数据。

自检：python llm.py
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

load_dotenv()

# deepseek-flash 空闲时段参考价（元 / 百万 token）；高峰时段翻倍。
PRICE_INPUT_PER_M = 1.0
PRICE_OUTPUT_PER_M = 4.0


class LLMConfigError(RuntimeError):
    """配置缺失（例如没有 API Key）。"""


class LLMCallError(RuntimeError):
    """调用失败，已翻译成人类可读的提示。"""


@dataclass
class Usage:
    """累计用量统计。推理 token 已包含在 completion_tokens 里，单独记一份便于观察。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_cny(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * PRICE_INPUT_PER_M
            + self.completion_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    @property
    def reasoning_ratio(self) -> float:
        """推理 token 占输出的比例 —— Unit 3 实测这个值高得离谱（>90%）。"""
        return self.reasoning_tokens / self.completion_tokens if self.completion_tokens else 0.0

    def add(self, prompt_tokens: int, completion_tokens: int, reasoning_tokens: int = 0) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.reasoning_tokens += reasoning_tokens

    def summary(self) -> str:
        return (
            f"{self.calls} 次调用 · 输入 {self.prompt_tokens} · 输出 {self.completion_tokens}"
            f"（含推理 {self.reasoning_tokens}，占 {self.reasoning_ratio:.0%}）"
            f" · 约 ¥{self.cost_cny:.4f}"
        )


@dataclass
class Message:
    """一条对话消息。role 只能是 system / user / assistant。"""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class CallMeta:
    """一次调用的完整记录 —— 不只看解析结果，原始响应也留着。"""

    text: str = ""                      # 模型输出的原始字符串（未经任何解析！）
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    elapsed_ms: float = 0.0
    trace_id: str | None = None
    truncated: bool = False
    # --- 以下为「看到解析过程」所需 ---
    request: dict = field(default_factory=dict)     # 发出去的请求体（含 messages）
    raw_response: dict = field(default_factory=dict)  # 完整响应 JSON（choices/usage/…）
    raw_headers: dict = field(default_factory=dict)   # HTTP 响应头
    saved_path: str | None = None                     # 存盘位置

    def raw_preview(self, n: int = 400) -> str:
        """原始文本的可读预览，便于终端快速确认。"""
        return self.text[:n] + ("…" if len(self.text) > n else "")


def _save_raw(record: dict, tag: str = "call") -> str:
    """把一次调用的完整请求/响应写到 logs/，返回路径。"""
    if os.getenv("LLM_SAVE_RAW", "1") in ("0", "false", "False"):
        return ""
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ms = int((time.time() % 1) * 1000)
    path = log_dir / f"{stamp}-{ms:03d}-{tag}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


class LLMClient:
    """DeepSeek（OpenAI 兼容协议）的轻量封装。"""

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 400,
        timeout: float = 60.0,
    ) -> None:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

        if not api_key or api_key.startswith("sk-你的"):
            raise LLMConfigError(
                "缺少 DEEPSEEK_API_KEY。执行 `cp .env.example .env` 并填入真实 Key"
                "（https://platform.deepseek.com/api_keys）"
            )

        self.model = (model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")).strip()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.usage = Usage()
        self.meta = CallMeta()
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    # ------------------------------------------------------------------ 同步
    def chat(
        self,
        messages: Iterable[Message | dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        warn_truncated: bool = True,
    ) -> str:
        """非流式调用，返回回答文本；元数据存在 self.meta 里。"""
        payload = [m.to_dict() if isinstance(m, Message) else m for m in messages]
        kwargs: dict = {
            "model": self.model,
            "messages": payload,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body

        t0 = time.perf_counter()
        try:
            raw = self._client.chat.completions.with_raw_response.create(**kwargs)
        except AuthenticationError as exc:
            raise LLMCallError("API Key 无效或已过期（401）。注意：也可能是服务端瞬时抖动，先重试一次。") from exc
        except RateLimitError as exc:
            raise LLMCallError("触发限速（429），稍等几秒再试。") from exc
        except APITimeoutError as exc:
            raise LLMCallError("请求超时。可降低 max_tokens 或缩短 Prompt 后重试。") from exc
        except APIConnectionError as exc:
            raise LLMCallError("无法连接 api.deepseek.com。检查网络：curl -I https://api.deepseek.com") from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                raise LLMCallError("余额不足（402），请到平台充值。") from exc
            raise LLMCallError(f"服务返回错误 {exc.status_code}: {exc.message}") from exc

        elapsed_ms = (time.perf_counter() - t0) * 1000
        resp = raw.parse()
        choice = resp.choices[0]

        u = resp.usage
        reasoning = 0
        if u is not None and getattr(u, "completion_tokens_details", None) is not None:
            reasoning = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
        if u is not None:
            self.usage.add(u.prompt_tokens, u.completion_tokens, reasoning)

        text = choice.message.content or ""
        truncated = choice.finish_reason == "length"

        # 完整保留：响应体 / 响应头 / 请求体，全部可回看
        try:
            raw_json = resp.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            raw_json = {"_note": "model_dump 失败", "repr": repr(resp)[:2000]}

        self.meta = CallMeta(
            text=text,
            finish_reason=choice.finish_reason,
            prompt_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            completion_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            reasoning_tokens=reasoning,
            elapsed_ms=elapsed_ms,
            trace_id=dict(raw.headers).get("x-ds-trace-id"),
            truncated=truncated,
            request=kwargs,
            raw_response=raw_json,
            raw_headers={k: v for k, v in dict(raw.headers).items()},
        )
        self.meta.saved_path = _save_raw(
            {
                "request": kwargs,
                "response": raw_json,
                "headers": self.meta.raw_headers,
                "meta": {
                    "finish_reason": self.meta.finish_reason,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "prompt_tokens": self.meta.prompt_tokens,
                    "completion_tokens": self.meta.completion_tokens,
                    "reasoning_tokens": reasoning,
                    "trace_id": self.meta.trace_id,
                    "truncated": truncated,
                },
            }
        )

        if truncated and warn_truncated:
            print(
                f"  ⚠️ 回答被 max_tokens({kwargs['max_tokens']}) 截断了！"
                f"本次输出 {self.meta.completion_tokens} token，其中推理占 {reasoning}。"
                "→ 调大 max_tokens，或换一个更短的提问。"
            )

        return text

    def chat_one(self, user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        """单轮调用的小helper：system + user 两条消息。"""
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message("system", system_prompt))
        messages.append(Message("user", user_prompt))
        return self.chat(messages, **kwargs)


if __name__ == "__main__":
    client = LLMClient()
    print(f"[自检] model={client.model} max_tokens={client.max_tokens}")
    try:
        print("[回答]", client.chat_one("只回答两个字：成功"))
        print("[用量]", client.usage.summary())
        print(f"[元数据] finish_reason={client.meta.finish_reason} trace_id={client.meta.trace_id}")
    except LLMConfigError as exc:
        print(f"❌ 配置错误：{exc}")
