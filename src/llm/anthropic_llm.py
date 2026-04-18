from __future__ import annotations

import os
import time
from typing import Any, Optional

import anthropic

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import LLMState, AnthropicCachedState, ContentBlock


class AnthropicLLM(InterfaceLLM):
    """
    Anthropic API backend with explicit prompt-cache checkpoints.

    Context files are accumulated as typed ContentBlock instances inside
    AnthropicCachedState via accumulate() — no API call during accumulation.
    On generate(), all blocks are sent with cache_control on the last context
    block, instructing Anthropic's server to cache that prefix. Workers
    sharing the same context file set send identical blocks in the same order,
    so Anthropic's prefix cache fires across workers.

    Use BUILD_MODE=cached to get caching. BUILD_MODE=naive sends a single
    prompt block with no cache markers (no caching benefit, but correct).

    Extra attrs (not in InterfaceLLM.metrics() tuple):
        total_cache_read_tokens      — tokens read from Anthropic's cache
        total_cache_creation_tokens  — tokens written to Anthropic's cache
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
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0

    def empty_state(self) -> AnthropicCachedState:
        return AnthropicCachedState(blocks=[])

    def accumulate(self, prompt: str, state: LLMState) -> AnthropicCachedState:
        """
        Append a content block for prompt to state without any API call.

        If state is not an AnthropicCachedState (e.g. a plain LLMState from
        build_naive), it is treated as an empty starting point.
        """
        existing = state.blocks if isinstance(state, AnthropicCachedState) else []
        return AnthropicCachedState(blocks=existing + [ContentBlock(text=prompt)])

    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[AnthropicCachedState, str]:
        """
        Run a real Anthropic API call.

        Builds the message content from state.blocks (the shared context
        prefix), injects cache_control on the last context block, then
        appends the current prompt (target file + task) as the final block
        with no cache marker — it changes every task.

        If state is not an AnthropicCachedState, it is treated as empty.
        This allows pairing with build_naive without crashing.
        """
        blocks = state.blocks if isinstance(state, AnthropicCachedState) else []

        message_content: list[dict[str, Any]] = []
        for i, block in enumerate(blocks):
            entry: dict[str, Any] = {"type": "text", "text": block.text}
            if i == len(blocks) - 1:
                # cache_control on the last context block only — tells Anthropic
                # to cache everything up to this point server-side.
                entry["cache_control"] = {"type": "ephemeral"}
            message_content.append(entry)

        # The target file + task has no cache marker: it is unique per task.
        message_content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": message_content}],
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

        usage = response.usage
        self._total_input_tokens += usage.input_tokens
        self._total_output_tokens += usage.output_tokens
        self._total_latency_ms += latency_ms
        self.total_cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.total_cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

        output = response.content[0].text
        # Return state unchanged — the caller (build_cached) already cached it
        # before calling generate(), so no update is needed.
        return state if isinstance(state, AnthropicCachedState) else self.empty_state(), output

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
            self.total_cache_read_tokens = 0
            self.total_cache_creation_tokens = 0
        return result
