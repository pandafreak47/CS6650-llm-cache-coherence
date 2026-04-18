from __future__ import annotations

import re
from typing import Optional

from .interface import InterfaceLLM, FILE_OPEN, FILE_CLOSE, DEFAULT_END_SEQUENCE
from ..models import LLMState

# Matches every <file path="...">...</file> block in the prompt.
_FILE_BLOCK = re.compile(r'<file path="[^"]*">(.*?)</file>', re.DOTALL)


class DummyLLM(InterfaceLLM):
    """
    No-op implementation for pipeline testing.

    Parses the last <file ...>...</file> block from the prompt and returns
    its content unchanged — simulating an LLM that rewrites the target file
    without modifying anything.

    Output tokens and latency are always zero.
    Input tokens are approximated as len(prompt) // 4.
    """

    def __init__(self, end_sequence: str = DEFAULT_END_SEQUENCE):
        self._end_sequence = end_sequence
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0.0

    def empty_state(self) -> LLMState:
        return LLMState()

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
