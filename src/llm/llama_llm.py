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


def _save_kv(model: Llama, compress: bool = True) -> str:
    """Pickle LlamaState → base64 string; optionally zlib-compress before encoding."""
    data = pickle.dumps(model.save_state())
    if compress:
        data = zlib.compress(data)
    return base64.b64encode(data).decode()


def _load_kv(model: Llama, b64: str, compress: bool = True) -> None:
    """Restore model KV state from base64 string.

    Auto-detects compression from magic bytes (zlib=0x78, pickle=0x80) so
    states written with a different compress setting are still readable.
    """
    data = base64.b64decode(b64)
    if data[0] == 0x78:  # zlib magic byte — decompress regardless of flag
        data = zlib.decompress(data)
    model.load_state(pickle.loads(data))


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
        n_ctx: int = int(os.environ.get("LLAMA_N_CTX", "4096")),
    ):
        self._end_sequence = end_sequence
        # LLAMA_SEED=-1 (default) means random; any non-negative int fixes the seed.
        _seed_raw = int(os.environ.get("LLAMA_SEED", "-1"))
        self._seed = _seed_raw if _seed_raw >= 0 else Llama.LLAMA_DEFAULT_SEED if hasattr(Llama, "LLAMA_DEFAULT_SEED") else 0xFFFFFFFF
        self._temperature = float(os.environ.get("LLAMA_TEMPERATURE", "0.8"))
        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=os.cpu_count() or 4,
            seed=self._seed,
            verbose=False,
        )
        self._compress = os.environ.get("KV_COMPRESS", "1").strip() not in ("0", "false", "no")
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cache_read_tokens = 0
        self._total_latency_ms = 0.0

    def empty_state(self) -> LlamaKVState:
        return LlamaKVState()

    def accumulate(self, prompt: str, state: LLMState) -> LlamaKVState:
        if isinstance(state, LlamaKVState) and state.llama_state_b64:
            _load_kv(self._model, state.llama_state_b64, self._compress)
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
            llama_state_b64=_save_kv(self._model, self._compress),
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
            _load_kv(self._model, state.llama_state_b64, self._compress)
        else:
            self._model.reset()

        prior = state.prompt if isinstance(state, LlamaKVState) else ""
        full_prompt = (prior + "\n" + prompt).lstrip() if prior else prompt

        # Tokens already in KV cache — will be skipped by llama-cpp-python's prefix matcher
        n_cached_before = self._model.n_tokens
        self._total_cache_read_tokens += n_cached_before

        try:
            result = self._model(
                full_prompt,
                max_tokens=max_tokens,
                stop=[self._end_sequence],
                echo=False,
                temperature=self._temperature,
            )
        except ValueError as exc:
            if "exceed context window" not in str(exc):
                raise
            # Full prompt overflows n_ctx — retry with task prompt only (no cached context).
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Context overflow (%s) — retrying with prompt only (no cached prefix)", exc
            )
            self._model.reset()
            n_cached_before = 0
            full_prompt = prompt
            result = self._model(
                full_prompt,
                max_tokens=max_tokens,
                stop=[self._end_sequence],
                echo=False,
                temperature=self._temperature,
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
