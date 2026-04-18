from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import LLMState

FILE_OPEN = '<file path="{path}">'
FILE_CLOSE = "</file>"
DEFAULT_END_SEQUENCE = FILE_CLOSE


class InterfaceLLM(ABC):
    """
    Common interface for all LLM backends.

    state carries backend-specific prefix-cache data between calls.
    Backends that manage their own caching (AnthropicLLM, DummyLLM) ignore
    it and pass it through unchanged. llama.cpp will use LlamaKVState to
    carry real KV tensors. AnthropicCachedLLM uses AnthropicCachedState to
    carry content blocks that are re-sent with cache_control markers.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[LLMState, str]:
        """
        Generate a completion.

        Returns (new_state, output_text).
        new_state covers the prompt prefix only — it does NOT include the
        generated output tokens, so it can be reused as a shared prefix.
        """
        ...

    def accumulate(self, prompt: str, state: LLMState) -> LLMState:
        """
        Extend state to cover one additional context chunk without producing
        meaningful output.

        Default: calls generate(max_tokens=1) and discards the text. This is
        semantically correct for llama.cpp, which must actually run the prefill
        to build its KV tensors.

        Backends that manage context client-side (AnthropicCachedLLM) override
        this to append a content block without any API call.
        """
        new_state, _ = self.generate(prompt=prompt, state=state, max_tokens=1)
        return new_state

    @abstractmethod
    def empty_state(self) -> LLMState:
        """
        Return the correct zero-state for this backend.

        Called by build_cached when there is no cached prefix, so the builder
        does not need to know which backend or state subclass is in use.
        """
        ...

    @abstractmethod
    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        """
        Return (total_input_tokens, total_output_tokens, total_latency_ms).

        If reset=True, zero all counters before returning the final totals.
        """
        ...
