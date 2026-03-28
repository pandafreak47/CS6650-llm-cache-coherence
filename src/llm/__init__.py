import os

from .base import BaseLLM, LLMResult
from .anthropic_llm import AnthropicLLM
from .dummy_llm import DummyLLM

__all__ = ["BaseLLM", "LLMResult", "AnthropicLLM", "DummyLLM", "create_llm"]


def create_llm() -> BaseLLM:
    """
    Instantiate the LLM backend selected by the LLM_BACKEND environment variable.

      LLM_BACKEND=anthropic  (default) — Anthropic Messages API
      LLM_BACKEND=dummy                — no-op, returns target_file; no credentials needed
      # LLM_BACKEND=llama              — llama.cpp (not yet implemented)
    """
    backend = os.getenv("LLM_BACKEND", "anthropic").lower()

    if backend == "anthropic":
        return AnthropicLLM()
    if backend == "dummy":
        return DummyLLM()
    # if backend == "llama":
    #     from .llama_llm import LlamaLLM
    #     return LlamaLLM()

    raise ValueError(
        f"Unknown LLM_BACKEND={backend!r}. "
        "Valid options: 'anthropic', 'dummy'"
        # ", 'llama'"
    )
