from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from .models import GitRepo


class GitClient:
    """
    Wraps the git CLI for reading files and committing changes.

    Lazily clones the repo into a temp directory on first use.
    Concurrent reads are safe; commits are serialized with a lock so that
    pull-before-push is atomic per worker instance.
    """

    def __init__(self, repo: GitRepo, github_token: str | None = None):
        self._repo = repo
        self._token = github_token or os.environ.get("GITHUB_TOKEN", "")
        self._workdir: Path | None = None
        self._commit_lock = threading.Lock()
        self._clone_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _auth_url(self) -> str:
        url = self._repo.url
        if self._token and "github.com" in url:
            url = url.replace("https://", f"https://{self._token}@")
        return url

    def _run(self, cmd: list[str], cwd: str | Path | None = None) -> str:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def _ensure_cloned(self) -> Path:
        with self._clone_lock:
            if self._workdir is not None:
                return self._workdir
            tmp = tempfile.mkdtemp(prefix="agent_repo_")
            self._run(
                ["git", "clone", "--branch", self._repo.branch, self._auth_url, tmp]
            )
            # Identity required for commits inside the container.
            self._run(["git", "config", "user.email", "agent@cs6650.local"], cwd=tmp)
            self._run(["git", "config", "user.name", "CS6650 Agent"], cwd=tmp)
            self._workdir = Path(tmp)
        return self._workdir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_file_content(self, file_path: str) -> str:
        """Return the current content of a file on the configured branch."""
        workdir = self._ensure_cloned()
        self._run(["git", "pull", "--ff-only"], cwd=workdir)
        return (workdir / file_path).read_text()

    def get_file_size(self, file_path: str) -> int:
        """Return the file's blob size in bytes (used for context ordering).

        Falls back to 0 if the file is not tracked or the path is wrong.
        """
        workdir = self._ensure_cloned()
        try:
            # Format: <mode> blob <hash>    <size>\t<path>
            output = self._run(
                ["git", "ls-tree", "-l", self._repo.branch, file_path],
                cwd=workdir,
            )
            parts = output.split()
            if len(parts) >= 4:
                return int(parts[3])
        except (subprocess.CalledProcessError, ValueError):
            pass
        return 0

    def commit_file(self, file_path: str, content: str, commit_message: str) -> None:
        """Write content, stage, commit, and push. Serialized per worker instance."""
        workdir = self._ensure_cloned()
        with self._commit_lock:
            self._run(["git", "pull", "--ff-only"], cwd=workdir)
            target = workdir / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            self._run(["git", "add", file_path], cwd=workdir)
            self._run(["git", "commit", "-m", commit_message], cwd=workdir)
            self._run(["git", "push"], cwd=workdir)
