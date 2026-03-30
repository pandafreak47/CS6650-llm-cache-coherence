from __future__ import annotations

import os
import time
from typing import Optional

import anthropic

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import KVState


class AnthropicLLM(InterfaceLLM):
    """
    Anthropic API backend.

    Ignores KVState — Anthropic manages prefix caching server-side.
    The given KVState is always returned unchanged.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        end_sequence: str = DEFAULT_END_SEQUENCE,
    ):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._end_sequence = end_sequence
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0.0

    def generate(
        self,
        prompt: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[KVState, str]:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stop_sequences": [self._end_sequence],
        }
        if system:
            kwargs["system"] = system

        t0 = time.monotonic()
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise RuntimeError(f"Anthropic API error: {exc}") from exc
        latency_ms = (time.monotonic() - t0) * 1000

        output = response.content[0].text
        self._total_input_tokens += response.usage.input_tokens
        self._total_output_tokens += response.usage.output_tokens
        self._total_latency_ms += latency_ms

        # Return the given KVState unchanged — Anthropic ignores it.
        return kv_state, output

    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        result = (
            self._total_input_tokens,
            self._total_output_tokens,
            self._total_latency_ms,
        )
        if reset:
            self._total_input_tokens = 0
            self._total_output_tokens = 0
            self._total_latency_ms = 0.0
        return result
