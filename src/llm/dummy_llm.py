from __future__ import annotations

import re
from typing import Optional

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import LLMState, AnthropicCachedState, ContentBlock

# Matches every <file path="...">...</file> block in the prompt.
_FILE_BLOCK = re.compile(r'<file path="[^"]*">(.*?)</file>', re.DOTALL)


class DummyLLM(InterfaceLLM):
    """
    No-op implementation for pipeline testing.

    Uses AnthropicCachedState so the cache stores real file text, making
    cache byte-size metrics realistic for baseline experiments. accumulate()
    appends content blocks without any I/O, matching AnthropicLLM's behaviour.

    generate() ignores state and parses the last <file> block from the prompt,
    returning it unchanged — simulating an LLM that rewrites without modifying.

    Input tokens: approximated as len(prompt) // 4 (short prompt in cached mode,
    full prompt in naive mode, correctly reflecting the caching savings).
    Output tokens: approximated as len(output) // 4.
    Latency: always zero.
    """

    def __init__(self, end_sequence: str = DEFAULT_END_SEQUENCE):
        self._end_sequence = end_sequence
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0.0

    def empty_state(self) -> AnthropicCachedState:
        return AnthropicCachedState(blocks=[])

    def accumulate(self, prompt: str, state: LLMState) -> AnthropicCachedState:
        """Append a content block without any I/O — mirrors AnthropicLLM.accumulate()."""
        existing = state.blocks if isinstance(state, AnthropicCachedState) else []
        return AnthropicCachedState(blocks=existing + [ContentBlock(text=prompt)])

    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[LLMState, str]:
        self._total_input_tokens += len(prompt) // 4

        matches = _FILE_BLOCK.findall(prompt)
        output = matches[-1].strip() if matches else ""
        self._total_output_tokens += len(output) // 4

        return state, output

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
