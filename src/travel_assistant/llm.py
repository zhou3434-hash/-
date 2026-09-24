"""DeepSeek 客户端封装。

设计要点
--------
1. 使用 OpenAI 兼容 SDK，base_url 指向 DeepSeek。
2. **思维链防护**：实测发现 `deepseek-flash` 会先产生 reasoning_tokens。
   若 max_tokens 过小，token 被推理吃光，`content` 会是空字符串，
   而 `finish_reason` 为 `length`，`response.choices[0].message.content == ""`。
   这类空回复在业务上极难排查，因此在客户端层做自动扩容重试。
3. 支持图片输入（data URL）与函数调用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import APIError, APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMEmptyResponseError(RuntimeError):
    """模型仅输出思维链、正文为空，且扩容重试后仍失败。"""


class DeepSeekClient:
    """异步 DeepSeek 客户端。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = AsyncOpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            timeout=self.settings.request_timeout,
            max_retries=0,  # 重试策略由本类自行控制，便于做空回复扩容
        )

    # ------------------------------------------------------------------ #
    # 底层调用
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """调用对话补全，返回规范化字典。

        返回结构::

            {
              "content": str,
              "tool_calls": list | None,
              "finish_reason": str,
              "usage": dict,
              "attempts": int,
              "max_tokens_used": int,
            }
        """
        base_budget = max_tokens or self.settings.deepseek_max_tokens
        # 依次尝试的 token 预算：思维链可能吃掉大部分额度
        budgets = [base_budget, base_budget * 2, base_budget * 4]

        last: dict[str, Any] | None = None
        for attempt, budget in enumerate(budgets, start=1):
            payload: dict[str, Any] = {
                "model": self.settings.deepseek_model,
                "messages": messages,
                "max_tokens": budget,
                "temperature": (
                    self.settings.deepseek_temperature if temperature is None else temperature
                ),
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            data = await self._request_with_retry(payload)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
            finish_reason = choice.get("finish_reason") or ""

            result = {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "usage": data.get("usage") or {},
                "attempts": attempt,
                "max_tokens_used": budget,
            }
            last = result

            # 有工具调用：直接返回（正文为空是正常的）
            if tool_calls:
                return result
            # 正文非空且正常结束：返回
            if content.strip():
                return result

            # 空正文 —— 典型的「思维链吃光 token」或模型没说话
            usage = result["usage"]
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            logger.warning(
                "第 %d 次尝试正文为空（finish_reason=%s, max_tokens=%d, reasoning_tokens=%s），扩容重试",
                attempt, finish_reason, budget, reasoning,
            )
            if attempt < len(budgets):
                continue

        # 全部失败
        usage = (last or {}).get("usage", {})
        raise LLMEmptyResponseError(
            "模型多次返回空正文。通常是思维链占满了 max_tokens。"
            f"最后一次 max_tokens={last.get('max_tokens_used') if last else '?'}，"
            f"finish_reason={last.get('finish_reason') if last else '?'}，usage={usage}。"
            "请调大 .env 中的 DEEPSEEK_MAX_TOKENS。"
        )

    async def _request_with_retry(
        self, payload: dict[str, Any], *, max_retries: int = 3
    ) -> dict[str, Any]:
        """带指数退避的网络重试。"""
        delay = 1.5
        last_err: Exception | None = None
        for i in range(max_retries + 1):
            try:
                resp = await self._client.chat.completions.create(**payload)
                return resp.model_dump()
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_err = e
                if i >= max_retries:
                    break
                logger.warning("请求失败（%s），%.1fs 后重试 %d/%d", type(e).__name__, delay, i + 1, max_retries)
                await asyncio.sleep(delay)
                delay *= 2
            except APIError as e:
                # 4xx 类错误重试无意义
                raise RuntimeError(f"DeepSeek API 返回错误：{e}") from e
        raise RuntimeError(f"DeepSeek API 多次请求失败：{last_err}") from last_err

    # ------------------------------------------------------------------ #
    # 便捷方法
    # ------------------------------------------------------------------ #
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """单轮文本补全，返回正文。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        r = await self.chat(
            messages, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode
        )
        return r["content"]

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> "DeepSeekClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
