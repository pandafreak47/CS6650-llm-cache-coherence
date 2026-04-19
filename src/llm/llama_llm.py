from __future__ import annotations

import os
import time
from typing import Optional

from llama_cpp import Llama

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import LLMState, LlamaKVState


class LlamaLLM(InterfaceLLM):
    """
    llama.cpp backend using llama-cpp-python.

    Runs a GGUF-quantized model entirely on CPU. accumulate() uses the default
    InterfaceLLM implementation (generate with max_tokens=1), so the prefill
    actually runs and contributes to measured token counts and latency.
    """

    def __init__(
        self,
        model_path: str,
        end_sequence: str = DEFAULT_END_SEQUENCE,
        n_ctx: int = 2048,
    ):
        self._end_sequence = end_sequence
        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=os.cpu_count() or 4,
            verbose=False,
        )
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0.0

    def empty_state(self) -> LlamaKVState:
        return LlamaKVState()

    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[LlamaKVState, str]:
        t0 = time.monotonic()

        prior = state.prompt if isinstance(state, LlamaKVState) else ""
        full_prompt = (prior + "\n" + prompt).lstrip() if prior else prompt

        self._total_input_tokens += len(full_prompt) // 4

        result = self._model(
            full_prompt,
            max_tokens=max_tokens,
            stop=[self._end_sequence],
            echo=False,
        )

        output: str = result["choices"][0]["text"]
        self._total_output_tokens += len(output) // 4
        self._total_latency_ms += (time.monotonic() - t0) * 1000

        return LlamaKVState(prompt=full_prompt, token_count=len(full_prompt) // 4), output

    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        result = (self._total_input_tokens, self._total_output_tokens, self._total_latency_ms)
        if reset:
            self._total_input_tokens = 0
            self._total_output_tokens = 0
            self._total_latency_ms = 0.0
        return result
