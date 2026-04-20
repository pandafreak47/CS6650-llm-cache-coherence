from __future__ import annotations

import base64
import os
import pickle
import time
import zlib
from typing import Optional

from llama_cpp import Llama

from .interface import InterfaceLLM, DEFAULT_END_SEQUENCE
from ..models import LLMState, LlamaKVState


def _save_kv(model: Llama) -> str:
    """Pickle + zlib-compress LlamaState → base64 string for JSON storage."""
    return base64.b64encode(zlib.compress(pickle.dumps(model.save_state()))).decode()


def _load_kv(model: Llama, b64: str) -> None:
    """Restore model KV state from base64 string."""
    model.load_state(pickle.loads(zlib.decompress(base64.b64decode(b64))))


class LlamaLLM(InterfaceLLM):
    """
    llama.cpp backend using llama-cpp-python.

    Runs a GGUF-quantized model entirely on CPU. accumulate() saves and restores
    real KV tensor state via save_state()/load_state(), so prefill is skipped for
    tokens already in the KV cache. generate() similarly loads the cached state
    before inference, relying on llama-cpp-python's internal prefix matcher to skip
    already-evaluated tokens.
    """

    def __init__(
        self,
        model_path: str,
        end_sequence: str = DEFAULT_END_SEQUENCE,
        n_ctx: int = 4096,
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
        self._total_cache_read_tokens = 0
        self._total_latency_ms = 0.0

    def empty_state(self) -> LlamaKVState:
        return LlamaKVState()

    def accumulate(self, prompt: str, state: LLMState) -> LlamaKVState:
        if isinstance(state, LlamaKVState) and state.llama_state_b64:
            _load_kv(self._model, state.llama_state_b64)
        else:
            self._model.reset()

        prior = state.prompt if isinstance(state, LlamaKVState) else ""
        extended = (prior + "\n" + prompt).lstrip() if prior else prompt

        tokens = self._model.tokenize(extended.encode())
        n_cached = self._model.n_tokens  # restored by load_state (0 after reset)
        new_tokens = tokens[n_cached:]

        if new_tokens:
            self._model.eval(new_tokens)

        return LlamaKVState(
            prompt=extended,
            token_count=self._model.n_tokens,
            llama_state_b64=_save_kv(self._model),
        )

    def generate(
        self,
        prompt: str,
        state: LLMState,
        max_tokens: int = 1024,
        system: Optional[str] = None,
    ) -> tuple[LlamaKVState, str]:
        t0 = time.monotonic()

        if isinstance(state, LlamaKVState) and state.llama_state_b64:
            _load_kv(self._model, state.llama_state_b64)
        else:
            self._model.reset()

        prior = state.prompt if isinstance(state, LlamaKVState) else ""
        full_prompt = (prior + "\n" + prompt).lstrip() if prior else prompt

        # Tokens already in KV cache — will be skipped by llama-cpp-python's prefix matcher
        n_cached_before = self._model.n_tokens
        self._total_cache_read_tokens += n_cached_before

        result = self._model(
            full_prompt,
            max_tokens=max_tokens,
            stop=[self._end_sequence],
            echo=False,
        )

        output: str = result["choices"][0]["text"]

        # Count only tokens that actually ran through prefill
        n_full = len(self._model.tokenize(full_prompt.encode()))
        self._total_input_tokens += n_full - n_cached_before
        self._total_output_tokens += len(self._model.tokenize(output.encode()))
        self._total_latency_ms += (time.monotonic() - t0) * 1000

        return LlamaKVState(prompt=full_prompt, token_count=n_full), output

    def metrics(self, reset: bool = False) -> tuple[int, int, float]:
        result = (self._total_input_tokens, self._total_output_tokens, self._total_latency_ms)
        if reset:
            self._total_input_tokens = 0
            self._total_output_tokens = 0
            self._total_cache_read_tokens = 0
            self._total_latency_ms = 0.0
        return result
