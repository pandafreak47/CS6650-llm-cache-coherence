from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import KVState

# ---------------------------------------------------------------------------
# Standardised file delimiters
#
# Anthropic recommends XML tags for document blocks in prompts. Claude is
# explicitly trained to interpret them, and they are unambiguous to regex-parse.
# ---------------------------------------------------------------------------

FILE_OPEN = '<file path="{path}">'   # format with path=...
FILE_CLOSE = "</file>"

# The LLM is told to stop generating after it writes the closing tag of the
# rewritten target file. Constructors accept a custom token to override this.
DEFAULT_END_SEQUENCE = FILE_CLOSE


class InterfaceLLM(ABC):
    """
    Common interface for all LLM backends.

    kv_state carries prefix-cache data between calls.  Some backends
    (Anthropic, Dummy) ignore it entirely; llama.cpp will use it to avoid
    reprocessing shared context tokens.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[KVState, str]:
        """
        Generate a completion.

        Returns (new_kv_state, output_text).
        The returned KVState covers the *prompt* only — it does NOT include
        the generated output tokens, so it can be reused as a shared prefix.
        """
        ...

    @abstractmethod
    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        """
        Return (total_input_tokens, total_output_tokens, total_latency_ms).

        If reset=True, zero all counters before returning the final totals.
        """
        ...
