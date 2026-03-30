from __future__ import annotations

from typing import Optional

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import KVState


class LlamaLLM(InterfaceLLM):
    """
    llama.cpp backend with full KVState support.

    This is the end-goal implementation that enables real prefix-cache reuse
    across workers sharing a centralized KV cache.  Not yet implemented.
    """

    def __init__(
        self,
        model_path: str,
        end_sequence: str = DEFAULT_END_SEQUENCE,
    ):
        raise NotImplementedError("LlamaLLM is not yet implemented")

    def generate(
        self,
        prompt: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[KVState, str]:
        raise NotImplementedError

    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        raise NotImplementedError
