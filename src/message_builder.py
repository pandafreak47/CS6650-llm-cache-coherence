"""
Builds the (kv_state, prompt_string) pair that is fed to the LLM.

Two implementations are provided:
  build_naive  — fetches all files and packs them into one big prompt string.
  build_cached — incrementally builds KV state from cached context prefixes,
                 producing a short prompt (target + task only).  Designed for
                 llama.cpp; still correct but wasteful when used with Anthropic.
"""
from __future__ import annotations

from .git_client import GitClient
from .kv_cache import KVCacheInterface, make_key
from .llm.interface import InterfaceLLM, FILE_OPEN, FILE_CLOSE
from .models import KVState, SQSMessage

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

def build_naive(msg: SQSMessage, git: GitClient) -> tuple[KVState, str]:
    """
    Fetch every context file and the target file, assemble a single prompt.
    Returns (empty_KVState, full_context_string).
    """
    parts: list[str] = []

    for path in msg.context_files:
        content = git.get_file_content(path)
        parts.append(_wrap_file(path, content))

    target_content = git.get_file_content(msg.target_file)
    parts.append(_wrap_file(msg.target_file, target_content))

    prompt = "\n\n".join(parts) + "\n\n" + _task_seed(msg.task_prompt, msg.target_file)
    return KVState(), prompt


# ---------------------------------------------------------------------------
# Cached build: incremental KV state from shared context prefix
# ---------------------------------------------------------------------------

def build_cached(
    msg: SQSMessage,
    git: GitClient,
    llm: InterfaceLLM,
    cache: KVCacheInterface,
) -> tuple[KVState, str]:
    """
    Reuse a cached KV state for previously processed context files.

    Strategy
    --------
    1. Order context files by size descending (largest files first — they
       contribute the most to the prefix and are most likely to be shared).
    2. Find the longest cached prefix for that ordered list.
    3. Process only the remaining (uncached) files, saving each incremental
       KV state back to the cache.
    4. Build a short prompt containing only the target file and task.

    Returns (last_kv_state, short_prompt_string).

    Note: when used with AnthropicLLM, KV states are always empty, so the
    incremental calls still work but the short prompt will be missing context.
    Use build_naive with Anthropic until llama.cpp is integrated.
    """
    context_files = list(msg.context_files)

    # Order by descending file size so the most-shared content is at the top.
    sizes = {path: git.get_file_size(path) for path in context_files}
    ordered = sorted(context_files, key=lambda p: sizes[p], reverse=True)

    # Find the longest matching cached prefix.
    prefix_result = cache.find_best_prefix(ordered)
    if prefix_result is not None:
        start_idx, kv_state = prefix_result
    else:
        start_idx, kv_state = 0, KVState()

    # Process remaining files incrementally, extending the KV state.
    for i in range(start_idx, len(ordered)):
        path = ordered[i]
        content = git.get_file_content(path)
        file_str = _wrap_file(path, content)
        # max_tokens=1 minimises output — we only want the updated KV state.
        kv_state, _ = llm.generate(prompt=file_str, kv_state=kv_state, max_tokens=1)
        cache.put(make_key(ordered[: i + 1]), kv_state)

    # Short prompt: target file + task only (context lives in kv_state).
    target_content = git.get_file_content(msg.target_file)
    prompt = (
        _wrap_file(msg.target_file, target_content)
        + "\n\n"
        + _task_seed(msg.task_prompt, msg.target_file)
    )
    return kv_state, prompt
