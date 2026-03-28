import time
from typing import Optional

from models import KVState
from .base import BaseLLM, LLMResult


class DummyLLM(BaseLLM):
    """
    No-op LLM for local testing. Skips all network calls and returns the
    target_file path (passed via kv_state by build_message) as its content.
    Useful for validating the full request → build → commit pipeline without
    spending API tokens or requiring credentials.
    """

    def generate(
        self,
        message: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> LLMResult:
        
        return LLMResult(
            content="<<SKIP>>", # Skip sequence to indicate no-op response 
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
        )
