"""
Parses the LLM output and commits the rewritten file back to the repository.
"""
from __future__ import annotations

from .git_client import GitClient


def commit_changes(
    git: GitClient,
    target_file: str,
    llm_output: str,
    task_prompt: str,
) -> None:
    """
    Write llm_output to target_file, then commit and push.

    The LLM is instructed to emit only raw file content (no markdown fences,
    no explanation) and to stop at the FILE_CLOSE end-sequence, so llm_output
    should already be clean file content.  We strip leading/trailing whitespace
    as a safety measure.
    """
    content = llm_output.strip()
    # Truncate the task prompt to keep commit messages under 72 chars.
    summary = task_prompt.replace("\n", " ")[:60]
    commit_message = f"agent: {summary}"
    git.commit_file(target_file, content, commit_message)
