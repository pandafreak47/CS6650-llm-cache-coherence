"""
Builds the (kv_state, prompt_string) pair that is fed to the LLM.

Two implementations are provided:
  build_naive  — fetches all files and packs them into one big prompt string.
  build_cached — incrementally builds KV state from cached context prefixes,
                 producing a short prompt (target + task only).  Designed for
                 llama.cpp; still correct but wasteful when used with Anthropic.

The ordering strategy used by build_cached to accumulate uncached context files
is controlled by the CACHE_ORDER environment variable (default: size_desc).
"""
from __future__ import annotations

import os

from .git_client import GitClient
from .kv_cache import KVCacheInterface, make_key
from .llm.interface import InterfaceLLM, FILE_OPEN, FILE_CLOSE
from .models import LLMState, SQSMessage

# ---------------------------------------------------------------------------
# Context-file ordering strategies
#
# Each strategy receives the set of uncached file paths and their sizes, and
# returns an ordered list. build_cached accumulates files in this order,
# saving an intermediate KV state after each one so future tasks can get
# partial cache hits on any prefix of the ordering.
# ---------------------------------------------------------------------------

def _order_size_desc(remaining: set[str], sizes: dict[str, int]) -> list[str]:
    """Largest files first (default). Maximises token savings per cached entry."""
    return sorted(remaining, key=lambda p: sizes[p], reverse=True)


def _order_size_asc(remaining: set[str], sizes: dict[str, int]) -> list[str]:
    """Smallest files first. Builds many small intermediate states quickly."""
    return sorted(remaining, key=lambda p: sizes[p], reverse=False)


def _order_frequency(remaining: set[str], sizes: dict[str, int]) -> list[str]:
    """Most-frequently-seen files first. Not yet implemented."""
    raise NotImplementedError(
        "CACHE_ORDER=frequency is not yet implemented. "
        "Requires a cross-worker frequency table (e.g. Redis HINCRBY)."
    )


def _order_directory(remaining: set[str], sizes: dict[str, int]) -> list[str]:
    """Group files by directory, then by size within each group. Not yet implemented."""
    raise NotImplementedError(
        "CACHE_ORDER=directory is not yet implemented."
    )


def _order_git_recency(remaining: set[str], sizes: dict[str, int]) -> list[str]:
    """Most-recently-committed files last (most stable context first). Not yet implemented."""
    raise NotImplementedError(
        "CACHE_ORDER=git_recency is not yet implemented. "
        "Requires a git log call per file."
    )


_ORDERING_STRATEGIES: dict[str, object] = {
    "size_desc":   _order_size_desc,
    "size_asc":    _order_size_asc,
    "frequency":   _order_frequency,
    "directory":   _order_directory,
    "git_recency": _order_git_recency,
}

CACHE_ORDER: str = os.environ.get("CACHE_ORDER", "size_desc").lower()

if CACHE_ORDER not in _ORDERING_STRATEGIES:
    raise ValueError(
        f"Unknown CACHE_ORDER '{CACHE_ORDER}'. "
        f"Valid values: {list(_ORDERING_STRATEGIES)}"
    )

_order_files = _ORDERING_STRATEGIES[CACHE_ORDER]

# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------

def _wrap_file(path: str, content: str) -> str:
    return f'{FILE_OPEN.format(path=path)}\n{content}\n{FILE_CLOSE}'


def _task_seed(task: str, target_file: str) -> str:
    """Instruction block + opening tag that seeds the LLM's rewrite output."""
    return (
        f"<task>\n{task}\n</task>\n\n"
        "Rewrite the target file to complete the task. "
        "Output ONLY the file content, nothing else:\n"
        f"{FILE_OPEN.format(path=target_file)}\n"
    )


# ---------------------------------------------------------------------------
# Naive build: one monolithic context string, empty KV state
# ---------------------------------------------------------------------------

def build_naive(msg: SQSMessage, git: GitClient) -> tuple[LLMState, str]:
    """
    Fetch every context file and the target file, assemble a single prompt.
    Returns (empty LLMState, full_context_string).
    """
    parts: list[str] = []

    for path in msg.context_files:
        content = git.get_file_content(path)
        parts.append(_wrap_file(path, content))

    target_content = git.get_file_content(msg.target_file)
    parts.append(_wrap_file(msg.target_file, target_content))

    prompt = "\n\n".join(parts) + "\n\n" + _task_seed(msg.task_prompt, msg.target_file)
    return LLMState(), prompt


# ---------------------------------------------------------------------------
# Cached build: incremental KV state from shared context prefix
# ---------------------------------------------------------------------------

def build_cached(
    msg: SQSMessage,
    git: GitClient,
    llm: InterfaceLLM,
    cache: KVCacheInterface,
) -> tuple[LLMState, str]:
    """
    Reuse a cached KV state for previously processed context files.

    Strategy
    --------
    1. Treat context files as a set — cache lookup is order-independent.
    2. Find the largest cached subset of the context files.
    3. Order the remaining (uncached) files by size descending and process
       them incrementally, saving each new KV state back to the cache.
    4. Build a short prompt containing only the target file and task.

    Returns (last_kv_state, short_prompt_string).

    Note: when used with AnthropicLLM, KV states are always empty, so the
    incremental calls still work but the short prompt will be missing context.
    Use build_naive with Anthropic until llama.cpp is integrated.
    """
    file_set = frozenset(msg.context_files)

    # Find the largest cached subset.
    prefix_result = cache.find_best_prefix(file_set)
    if prefix_result is not None:
        cached_files, kv_state = prefix_result
    else:
        cached_files, kv_state = frozenset(), llm.empty_state()

    # Order uncached files using the selected CACHE_ORDER strategy.
    remaining = file_set - cached_files
    sizes = {path: git.get_file_size(path) for path in remaining}
    ordered_remaining = _order_files(remaining, sizes)

    # Process remaining files incrementally, extending the KV state.
    processed = set(cached_files)
    for path in ordered_remaining:
        content = git.get_file_content(path)
        file_str = _wrap_file(path, content)
        kv_state = llm.accumulate(prompt=file_str, state=kv_state)
        processed.add(path)
        cache.put(make_key(processed), kv_state)

    # Short prompt: target file + task only (context lives in kv_state).
    target_content = git.get_file_content(msg.target_file)
    prompt = (
        _wrap_file(msg.target_file, target_content)
        + "\n\n"
        + _task_seed(msg.task_prompt, msg.target_file)
    )
    return kv_state, prompt
