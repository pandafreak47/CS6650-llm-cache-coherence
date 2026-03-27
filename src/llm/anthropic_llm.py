import os
import time
from typing import Optional

import anthropic

from models import KVState
from .base import BaseLLM, LLMResult


class AnthropicLLM(BaseLLM):
    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

    def generate(
        self,
        message: str,
        kv_state: KVState,  # unused — Anthropic manages caching server-side
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> LLMResult:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": message}],
        }
        if system:
            kwargs["system"] = system

        start = time.time()
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.APIError as e:
            raise RuntimeError(f"Anthropic API error: {e}") from e
        latency_ms = (time.time() - start) * 1000

        return LLMResult(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
