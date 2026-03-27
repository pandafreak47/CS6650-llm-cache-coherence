from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from models import KVState


@dataclass
class LLMResult:
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class BaseLLM(ABC):
    """
    Common interface for LLM backends.

    The kv_state parameter carries prefix-cache data between calls.
    Anthropic implementations ignore it (the API handles caching internally).
    llama.cpp implementations will use it to avoid reprocessing shared context.
    """

    @abstractmethod
    def generate(
        self,
        message: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> LLMResult: ...
