from __future__ import annotations

import os

from .interface import InterfaceLLM
from .anthropic_llm import AnthropicLLM
from .dummy_llm import DummyLLM

__all__ = ["InterfaceLLM", "AnthropicLLM", "DummyLLM", "create_llm"]


def create_llm() -> InterfaceLLM:
    """
    Instantiate the LLM backend selected by the LLM_BACKEND environment variable.

      LLM_BACKEND=anthropic  (default) — Anthropic Messages API
      LLM_BACKEND=dummy                — no-op, returns target file unchanged
      # LLM_BACKEND=llama              — llama.cpp (not yet implemented)
    """
    backend = os.getenv("LLM_BACKEND", "dummy").lower()
    model = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

    if backend == "anthropic":
        return AnthropicLLM(model=model)
    if backend == "dummy":
        return DummyLLM()
    # if backend == "llama":
    #     from .llama_llm import LlamaLLM
    #     model_path = os.environ["LLAMA_MODEL_PATH"]
    #     return LlamaLLM(model_path=model_path)

    raise ValueError(
        f"Unknown LLM_BACKEND={backend!r}. "
        "Valid options: 'anthropic', 'dummy'"
        # ", 'llama'"
    )
