"""
Assembles an LLM prompt from a structured task request.

Currently a dummy implementation that formats the request fields into a
readable prompt without fetching any real file contents. Later this will:
  - Pull file contents from the repo (GitHub API or local git clone)
  - Populate KVState with prefix-cache data for llama.cpp reuse
  - Support different prompt templates per task type
"""

from models import KVState


def build_message(
    repo: str,
    context_files: list[str],
    target_file: str,
    task: str,
    branch: str = "main",
) -> tuple[str, KVState]:
    """
    Returns (prompt_string, kv_state).

    kv_state is empty for now. With llama.cpp it will carry the KV-cache
    tensors for the context_files prefix so agents can skip reprocessing
    shared context when working on the same repo.
    """
    lines: list[str] = [
        f"Repository: {repo} (branch: {branch})",
        "",
        "=== Context Files ===",
    ]

    for path in context_files:
        lines += [
            f"--- {path} ---",
            "[contents not yet fetched]",
            "",
        ]

    lines += [
        "=== Target File ===",
        f"--- {target_file} ---",
        "[contents not yet fetched]",
        "",
        "=== Task ===",
        task,
    ]

    prompt = "\n".join(lines)
    kv_state: KVState = {}  # placeholder — will hold prefix cache data

    return prompt, kv_state
