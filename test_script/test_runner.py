#!/usr/bin/env python3
"""
Test runner for the CS6650 LLM cache-coherence experiment.

Steps
-----
1. Clone the target repo and read deps.json to discover tasks.
2. Create a fresh branch off main in the target repo.
3. Reset metrics on all workers (POST /metrics/clear).
4. Purge any leftover messages from the SQS queue.
5. Sample --num-tasks tasks (randomly, with optional --seed), seed the queue.
6. Poll until the queue is fully drained, timing the entire period.
7. Collect and aggregate metrics from all workers, print results.

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
import hashlib
import json
import os
import random
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import boto3

from _config import AWS_REGION, ECS_CLUSTER, ECS_SERVICE, WORKER_PORT

# ---------------------------------------------------------------------------
# Defaults — override via env vars or CLI flags
# ---------------------------------------------------------------------------

DEFAULT_REPO_URL = os.environ.get(
    "TEST_REPO_URL", "https://github.com/pandafreak47/CS6650-test-repo"
)
DEFAULT_BASE_BRANCH = os.environ.get("TEST_BASE_BRANCH", "main")

# Prompts are randomly paired with each sampled task.
TASK_PROMPTS: list[str] = [
    "Add docstrings to all public functions and methods.",
    "Add type annotations to all function signatures.",
    "Add input validation to all public functions.",
    "Refactor any magic strings or numbers into named constants.",
    "Add a logging statement at the entry point of each public function.",
    "Replace bare except clauses with specific exception types.",
    "Add an __all__ list that exports only the public API.",
]

# ---------------------------------------------------------------------------
# Task discovery from deps.json
# ---------------------------------------------------------------------------

def load_task_pool(repo_url: str, base_branch: str, token: str) -> list[dict]:
    """
    Clone the target repo, read deps.json, and return a list of task dicts.
    Each task has context_files (the file's direct dependencies) and
    target_file.  Files with no dependencies are included with an empty
    context so leaf nodes can still be targeted.
    """
    auth_url = repo_url.replace("https://", f"https://{token}@") if token else repo_url
    with tempfile.TemporaryDirectory(prefix="deps-fetch-") as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", base_branch, auth_url, tmp],
            check=True,
            capture_output=True,
        )
        deps_path = os.path.join(tmp, "deps.json")
        if not os.path.exists(deps_path):
            print("Error: deps.json not found in repo root.", file=sys.stderr)
            sys.exit(1)
        with open(deps_path) as f:
            deps: dict = json.load(f)

    tasks = []
    for target, context in deps.items():
        if target.startswith("_"):   # skip metadata keys like _comment
            continue
        tasks.append({"target_file": target, "context_files": context})

    print(f"  Loaded {len(tasks)} tasks from deps.json "
          f"({sum(1 for t in tasks if t['context_files'])} with context, "
          f"{sum(1 for t in tasks if not t['context_files'])} leaf nodes).")
    return tasks


def sample_tasks(pool: list[dict], n: int, seed: int | None) -> list[dict]:
    """
    Draw n tasks from the pool.  Cycles through all tasks before repeating
    (so short runs cover the whole graph evenly), then fills remainder randomly.
    Each sampled task gets a random prompt from TASK_PROMPTS.
    """
    rng = random.Random(seed)
    # Shuffle a full pass through the pool, repeat until we have enough.
    full_passes = (n // len(pool)) + 1
    ordered = pool * full_passes
    rng.shuffle(ordered)
    selected = ordered[:n]
    for task in selected:
        task["task_prompt"] = rng.choice(TASK_PROMPTS)
    return selected

# ---------------------------------------------------------------------------
# ECS worker discovery
# ---------------------------------------------------------------------------

def discover_worker_urls(cluster: str, service: str, port: int, region: str) -> list[str]:
    """
    Resolve the current public IPs of all RUNNING tasks in an ECS service
    and return them as http://IP:port base URLs.
    """
    ecs = boto3.client("ecs", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)

    task_arns = ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING") \
                   .get("taskArns", [])
    if not task_arns:
        print(f"  Warning: no running tasks found in {cluster}/{service}.")
        return []

    tasks = ecs.describe_tasks(cluster=cluster, tasks=task_arns).get("tasks", [])

    eni_ids = []
    for task in tasks:
        for attachment in task.get("attachments", []):
            for detail in attachment.get("details", []):
                if detail["name"] == "networkInterfaceId":
                    eni_ids.append(detail["value"])

    if not eni_ids:
        print("  Warning: no network interfaces found on running tasks.")
        return []

    interfaces = ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids) \
                    .get("NetworkInterfaces", [])

    urls = []
    for iface in interfaces:
        ip = iface.get("Association", {}).get("PublicIp")
        if ip:
            urls.append(f"http://{ip}:{port}")

    print(f"  Discovered {len(urls)} worker(s): {', '.join(urls)}")
    return urls


# ---------------------------------------------------------------------------
# Worker metrics
# ---------------------------------------------------------------------------

def _http(method: str, url: str, timeout: int = 5) -> dict | None:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError) as exc:
        print(f"  Warning: {method} {url} failed: {exc}")
        return None


def clear_worker_metrics(worker_urls: list[str]) -> None:
    """POST /metrics/clear on every worker."""
    for url in worker_urls:
        result = _http("POST", f"{url}/metrics/clear")
        if result is not None:
            print(f"  Cleared metrics on {url}")


def collect_worker_metrics(worker_urls: list[str]) -> dict:
    """
    GET /metrics from every worker and return aggregated totals.

    Additive fields (tokens, bytes, counts) are summed across workers.
    Per-worker breakdowns are included under 'workers' for inspection.
    """
    _ADDITIVE = [
        "total_input_tokens",
        "total_output_tokens",
        "total_requests",
        "total_cache_read_tokens",
        "total_cache_creation_tokens",
        "cache_bytes_written",
        "cache_bytes_read",
        "cache_hit_count",
        "cache_miss_count",
        "total_latency_ms",
    ]

    aggregated: dict = {k: 0 for k in _ADDITIVE}
    per_worker = []

    for url in worker_urls:
        m = _http("GET", f"{url}/metrics")
        if m is None:
            print(f"  Warning: could not reach {url} — excluded from aggregate.")
            continue
        per_worker.append({"url": url, **m})
        for key in _ADDITIVE:
            aggregated[key] += m.get(key, 0)

    aggregated["workers"] = per_worker
    return aggregated


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
        group_id = task["target_file"].replace("/", "_").replace(".", "_")
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
            print()
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
        "--workers",
        default=os.environ.get("WORKER_URLS", ""),
        help="Comma-separated worker base URLs (e.g. http://1.2.3.4:8080). "
             "Also via WORKER_URLS env var. If omitted, discovered from ECS "
             "using --cluster/--service.",
    )
    parser.add_argument(
        "--cluster",
        default=ECS_CLUSTER,
        help=f"ECS cluster name for automatic worker discovery (default: {ECS_CLUSTER}).",
    )
    parser.add_argument(
        "--service",
        default=ECS_SERVICE,
        help=f"ECS service name for automatic worker discovery (default: {ECS_SERVICE}).",
    )
    parser.add_argument(
        "--worker-port",
        type=int,
        default=WORKER_PORT,
        help=f"HTTP port the workers listen on (default: {WORKER_PORT}).",
    )
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=50,
        help="Number of tasks to seed (default: 50)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for task sampling (omit for non-deterministic)",
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
        default=AWS_REGION,
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

    # Resolve worker URLs — explicit list takes priority, then ECS discovery.
    worker_urls = [u.strip() for u in args.workers.split(",") if u.strip()]
    if not worker_urls:
        print(f"Discovering workers from ECS ({args.cluster}/{args.service})…")
        worker_urls = discover_worker_urls(
            args.cluster, args.service, args.worker_port, args.region
        )

    # --- Load task pool from repo -------------------------------------------
    print(f"Loading task pool from {args.repo_url} ({args.base_branch})…")
    pool = load_task_pool(args.repo_url, args.base_branch, github_token)

    # --- Branch setup -------------------------------------------------------
    branch_id = f"test-{_random_id()}"
    if args.skip_branch:
        print(f"Skipping branch creation. Using base branch: {args.base_branch}")
        branch_id = args.base_branch
    else:
        print(f"Creating test branch '{branch_id}' from '{args.base_branch}'…")
        create_test_branch(args.repo_url, args.base_branch, branch_id, github_token)

    # --- Reset worker metrics ------------------------------------------------
    if worker_urls:
        print(f"Resetting metrics on {len(worker_urls)} worker(s)…")
        clear_worker_metrics(worker_urls)

    # --- Queue setup --------------------------------------------------------
    print("Preparing queue…")
    purge_queue(sqs, args.queue_url)

    print(f"Sampling {args.num_tasks} task(s) (seed={args.seed})…")
    sampled = sample_tasks(pool, args.num_tasks, args.seed)
    seeded = [
        {**t, "git_repo": {"url": args.repo_url, "branch": branch_id}}
        for t in sampled
    ]

    print(f"Seeding {args.num_tasks} task(s)…")
    seed_tasks(sqs, args.queue_url, seeded)

    # --- Drain timing -------------------------------------------------------
    print("Waiting for queue to drain…")
    elapsed = wait_for_drain(sqs, args.queue_url)

    # --- Collect and print results ------------------------------------------
    print("\n" + "=" * 56)
    print(f"  Tasks         : {args.num_tasks}")
    print(f"  Seed          : {args.seed}")
    print(f"  Workers       : {len(worker_urls) or '(not provided)'}")
    print(f"  Total time    : {elapsed:.2f} s")
    print(f"  Avg per task  : {elapsed / args.num_tasks:.2f} s")

    if worker_urls:
        print()
        m = collect_worker_metrics(worker_urls)
        hits = m["cache_hit_count"]
        misses = m["cache_miss_count"]
        hit_rate = hits / (hits + misses) if (hits + misses) > 0 else 0.0
        # Show config from first responsive worker (all workers share the same env)
        first = m["workers"][0] if m["workers"] else {}
        print(f"  --- Config ---")
        print(f"  LLM backend   : {first.get('llm_backend', '?')}")
        print(f"  Build mode    : {first.get('build_mode', '?')}")
        print(f"  Cache backend : {first.get('cache_backend', '?')}")
        print(f"  --- LLM metrics (aggregated) ---")
        print(f"  Input tokens  : {m['total_input_tokens']:,}")
        print(f"  Output tokens : {m['total_output_tokens']:,}")
        print(f"  Requests      : {m['total_requests']}")
        print(f"  LLM latency   : {m['total_latency_ms'] / 1000:.2f} s total")
        if m["total_cache_read_tokens"] or m["total_cache_creation_tokens"]:
            print(f"  --- Anthropic server cache ---")
            print(f"  Cache read tokens   : {m['total_cache_read_tokens']:,}")
            print(f"  Cache create tokens : {m['total_cache_creation_tokens']:,}")
        print(f"  --- Local state cache ---")
        print(f"  Bytes written : {m['cache_bytes_written']:,}")
        print(f"  Bytes read    : {m['cache_bytes_read']:,}")
        print(f"  Hits          : {hits}  Misses: {misses}  Rate: {hit_rate:.1%}")

        if len(m["workers"]) > 1:
            print(f"\n  --- Per-worker breakdown ---")
            for w in m["workers"]:
                w_hits = w.get("cache_hit_count", 0)
                w_misses = w.get("cache_miss_count", 0)
                w_rate = w_hits / (w_hits + w_misses) if (w_hits + w_misses) > 0 else 0.0
                print(f"  {w['url']}")
                print(f"    input={w.get('total_input_tokens',0):,}  "
                      f"requests={w.get('total_requests',0)}  "
                      f"hit_rate={w_rate:.1%}  "
                      f"bytes_read={w.get('cache_bytes_read',0):,}")

    print("=" * 56)


if __name__ == "__main__":
    main()
