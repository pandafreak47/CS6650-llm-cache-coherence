"""
Commit handler — applies LLM output back to the repository.

Currently a dummy implementation that logs intent without touching the
filesystem or git. Later this will:
  - Parse result.content to extract the modified file (or a unified diff)
  - Write the file into a local clone of the repo
  - Stage, commit, and push via gitpython or subprocess git
  - Respect the file-level lock on target_file to avoid write conflicts
    between concurrent agents
"""

import logging

from llm.base import LLMResult
from models import TaskRequest

logger = logging.getLogger(__name__)


def commit(req: TaskRequest, result: LLMResult) -> None:
    logger.info(
        "commit | repo=%s branch=%s target=%s | %d tokens in %.1f ms",
        req.repo,
        req.branch,
        req.target_file,
        result.input_tokens + result.output_tokens,
        result.latency_ms,
    )
    logger.debug("commit | content preview: %.120s", result.content)
