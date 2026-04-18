from __future__ import annotations

from typing import Optional

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import LLMState, LlamaKVState


class LlamaLLM(InterfaceLLM):
    """
    llama.cpp backend with full LlamaKVState support.

    This is the end-goal implementation that enables real prefix-cache reuse
    across workers sharing a centralised KV cache. Not yet implemented.

    accumulate() uses the default InterfaceLLM implementation (calls
    generate(max_tokens=1)), which is semantically correct for llama.cpp —
    the prefill computation must actually run to produce the KV tensors.
    """

    def __init__(
        self,
        model_path: str,
        end_sequence: str = DEFAULT_END_SEQUENCE,
    ):
        raise NotImplementedError("LlamaLLM is not yet implemented")

    def empty_state(self) -> LlamaKVState:
        raise NotImplementedError

    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[LlamaKVState, str]:
        raise NotImplementedError

    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        raise NotImplementedError
