"""Unit 4 · LLM 调用封装（沿用 Unit 3 的 `llm.py`，并修掉当时发现的两个坑）。

与 Unit 3 版本的差异（都是 Unit 3 实测踩出来的）：
  1. `finish_reason == "length"` 时直接告警 —— `deepseek-flash` 是推理模型，
     推理 token 和正文共用 max_tokens，实测即使"只输出一个词"也要 60~100 output token
     （其中 90%+ 是推理），max_tokens 设小了回答会被悄悄截断。
  2. 记录 `last_trace_id`（来自响应头 x-ds-trace-id），实验出问题时可直接报障。
  3. 支持 extra_body 透传 —— Unit 4 要试各种参数，但注意：**未知字段会被服务端静默忽略**
     （实测传 `effort` 非法值也不报错），所以判断参数是否生效只能看 usage 数据。

自检：uv run python llm.py
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
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
    """一次调用的元数据 —— Unit 4 的实验结论全靠它，别只看回答文本。"""

    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    elapsed_ms: float = 0.0
    trace_id: str | None = None
    truncated: bool = False


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

        self.meta = CallMeta(
            text=text,
            finish_reason=choice.finish_reason,
            prompt_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            completion_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            reasoning_tokens=reasoning,
            elapsed_ms=elapsed_ms,
            trace_id=dict(raw.headers).get("x-ds-trace-id"),
            truncated=truncated,
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
