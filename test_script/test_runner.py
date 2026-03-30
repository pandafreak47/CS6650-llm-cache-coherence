#!/usr/bin/env python3
"""
Test runner for the CS6650 LLM cache-coherence experiment.

Steps
-----
1. Create a fresh branch off main in the target repo (all tests start from
   the same base, so results are comparable across runs).
2. Purge any leftover messages from the SQS queue.
3. Seed the queue with TASKS (repeated to reach --num-tasks if needed).
4. Poll until the queue is fully drained, timing the entire period.
5. Print per-task throughput stats.

Environment variables
---------------------
  SQS_QUEUE_URL     — FIFO queue URL (required)
  GITHUB_TOKEN      — Personal-access token with repo write permissions
  TEST_REPO_URL     — HTTPS URL of the target repo  (default below)
  TEST_BASE_BRANCH  — Branch to fork from           (default: main)
  AWS_REGION        — AWS region                    (default: us-east-1)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import random
import string

import boto3

# ---------------------------------------------------------------------------
# Defaults — override via env vars or CLI flags
# ---------------------------------------------------------------------------

DEFAULT_REPO_URL = os.environ.get(
    "TEST_REPO_URL", "https://github.com/your-org/cs6650-test-repo"
)
DEFAULT_BASE_BRANCH = os.environ.get("TEST_BASE_BRANCH", "main")

# Synthetic tasks seeded into the queue.
# Replace context_files / target_file with paths that actually exist in your
# test repo once you have a real repo URL.
TASKS: list[dict] = [
    {
        "context_files": ["src/utils.py"],
        "target_file": "src/main.py",
        "task_prompt": "Add docstrings to all public functions.",
    },
    {
        "context_files": ["src/models.py"],
        "target_file": "src/utils.py",
        "task_prompt": "Add type annotations to all function signatures.",
    },
    {
        "context_files": ["src/main.py", "src/utils.py"],
        "target_file": "src/models.py",
        "task_prompt": "Add field validators to all Pydantic models.",
    },
    {
        "context_files": ["src/utils.py", "src/models.py"],
        "target_file": "src/config.py",
        "task_prompt": "Convert all magic numbers to named constants.",
    },
    {
        "context_files": ["src/main.py"],
        "target_file": "src/exceptions.py",
        "task_prompt": "Define custom exception classes for each error type.",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_id(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def create_test_branch(repo_url: str, base_branch: str, new_branch: str, token: str) -> None:
    """Clone repo, create branch, push it, then delete the local clone."""
    auth_url = repo_url.replace("https://", f"https://{token}@") if token else repo_url
    with tempfile.TemporaryDirectory(prefix="branch-setup-") as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", base_branch, auth_url, tmp],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmp, "checkout", "-b", new_branch],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmp, "push", "-u", "origin", new_branch],
            check=True,
            capture_output=True,
        )
    print(f"  Created branch: {new_branch}")


def purge_queue(sqs, queue_url: str) -> None:
    print("  Purging existing queue messages…")
    try:
        sqs.purge_queue(QueueUrl=queue_url)
    except sqs.exceptions.PurgeQueueInProgress:
        print("  Purge already in progress.")
    # SQS purge can take up to 60 s; poll until depth drops to zero.
    for _ in range(70):
        time.sleep(1)
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )
        a = attrs["Attributes"]
        if int(a.get("ApproximateNumberOfMessages", 1)) == 0 and \
           int(a.get("ApproximateNumberOfMessagesNotVisible", 1)) == 0:
            print("  Queue empty.")
            return
    print("  Warning: queue may not be fully purged — proceeding anyway.")


def seed_tasks(sqs, queue_url: str, tasks: list[dict]) -> None:
    for task in tasks:
        body_str = json.dumps(task)
        # Message group ID = sanitised target_file path (gives per-file locking)
        group_id = task["target_file"].replace("/", "_").replace(".", "_")
        import hashlib
        dedup_id = hashlib.sha256(
            f"{group_id}:{body_str}:{time.time_ns()}".encode()
        ).hexdigest()[:40]
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=body_str,
            MessageGroupId=group_id,
            MessageDeduplicationId=dedup_id,
        )
    print(f"  Sent {len(tasks)} tasks.")


def wait_for_drain(sqs, queue_url: str, poll_interval: float = 5.0) -> float:
    """Block until the queue is empty. Returns elapsed seconds."""
    start = time.monotonic()
    while True:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )
        a = attrs["Attributes"]
        visible = int(a.get("ApproximateNumberOfMessages", 0))
        in_flight = int(a.get("ApproximateNumberOfMessagesNotVisible", 0))
        elapsed = time.monotonic() - start
        print(f"  [{elapsed:6.1f}s] visible={visible}  in-flight={in_flight}", end="\r")
        if visible + in_flight == 0:
            print()  # newline after the carriage-return line
            return elapsed
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="CS6650 SQS test runner")
    parser.add_argument(
        "--queue-url",
        default=os.environ.get("SQS_QUEUE_URL"),
        help="SQS FIFO queue URL (or set SQS_QUEUE_URL env var)",
    )
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=len(TASKS),
        help="Number of tasks to seed (tasks are cycled if more than TASKS list)",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="GitHub repo HTTPS URL",
    )
    parser.add_argument(
        "--base-branch",
        default=DEFAULT_BASE_BRANCH,
        help="Branch to fork the test branch from",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--skip-branch",
        action="store_true",
        help="Skip branch creation (useful when re-running against an existing branch)",
    )
    args = parser.parse_args()

    if not args.queue_url:
        print("Error: --queue-url or SQS_QUEUE_URL is required.", file=sys.stderr)
        sys.exit(1)

    github_token = os.environ.get("GITHUB_TOKEN", "")
    sqs = boto3.client("sqs", region_name=args.region)

    # --- Branch setup -------------------------------------------------------
    branch_id = f"test-{_random_id()}"
    if args.skip_branch:
        print(f"Skipping branch creation. Using base branch: {args.base_branch}")
        branch_id = args.base_branch
    else:
        print(f"Creating test branch '{branch_id}' from '{args.base_branch}'…")
        create_test_branch(args.repo_url, args.base_branch, branch_id, github_token)

    # --- Queue setup --------------------------------------------------------
    print("Preparing queue…")
    purge_queue(sqs, args.queue_url)

    # Build task list (cycle TASKS to reach num_tasks)
    cycle = (TASKS * ((args.num_tasks // len(TASKS)) + 1))[: args.num_tasks]
    seeded = [
        {**t, "git_repo": {"url": args.repo_url, "branch": branch_id}}
        for t in cycle
    ]

    print(f"Seeding {args.num_tasks} task(s)…")
    seed_tasks(sqs, args.queue_url, seeded)

    # --- Drain timing -------------------------------------------------------
    print("Waiting for queue to drain…")
    elapsed = wait_for_drain(sqs, args.queue_url)

    # --- Results ------------------------------------------------------------
    print("\n" + "=" * 50)
    print(f"  Tasks         : {args.num_tasks}")
    print(f"  Total time    : {elapsed:.2f} s")
    print(f"  Avg per task  : {elapsed / args.num_tasks:.2f} s")
    print("=" * 50)


if __name__ == "__main__":
    main()
