from __future__ import annotations

import base64
import json
import logging
import os
import random
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .models import GitRepo

logger = logging.getLogger(__name__)


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

    def _github_api(self, method: str, path: str, body: dict | None = None) -> dict:
        """Make a GitHub REST API call. Returns parsed JSON response."""
        url = f"https://api.github.com{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def commit_file(self, file_path: str, content: str, commit_message: str) -> None:
        """Commit a file via the GitHub Contents API (compare-and-swap on SHA).

        Bypasses git push entirely — the API handles concurrent writers atomically
        so there is no branch-level livelock regardless of worker count.
        """
        # Parse owner/repo from URL: https://github.com/{owner}/{repo}
        parts = self._repo.url.rstrip("/").split("/")
        owner, repo = parts[-2], parts[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]

        api_path = f"/repos/{owner}/{repo}/contents/{file_path}"
        encoded = base64.b64encode(content.encode()).decode()

        for attempt in range(20):
            # Fetch current SHA (needed for updates; omit for new files)
            sha: str | None = None
            try:
                info = self._github_api("GET", f"{api_path}?ref={self._repo.branch}")
                sha = info["sha"]
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise

            body: dict = {
                "message": commit_message,
                "content": encoded,
                "branch": self._repo.branch,
            }
            if sha:
                body["sha"] = sha

            try:
                self._github_api("PUT", api_path, body)
                return
            except urllib.error.HTTPError as e:
                if e.code == 409 and attempt < 19:
                    # SHA mismatch — another worker committed; retry with fresh SHA
                    logger.warning(
                        "GitHub API conflict (attempt %d/20) for %s — retrying",
                        attempt + 1, file_path,
                    )
                    time.sleep(random.uniform(0.05, 0.2))
                    continue
                raise
