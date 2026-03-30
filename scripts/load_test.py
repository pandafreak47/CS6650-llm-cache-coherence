#!/usr/bin/env python3
"""
Push templated TaskRequest messages into the SQS queue for load testing.

Each template targets a different scenario — varying context file count,
target file, and task complexity — so you can observe how the system
behaves under realistic mixed workloads.

Usage:
    # Send 20 mixed tasks to a queue
    python scripts/load_test.py --queue-url <URL> --template all --count 20

    # Preview what would be sent without hitting AWS
    python scripts/load_test.py --dry-run --template multi_context --count 5

    # Flood a single target_file to test lock/ordering behavior (Phase 3+)
    python scripts/load_test.py --queue-url <URL> --template same_target --count 10

Environment variables:
    SQS_QUEUE_URL   — alternative to --queue-url
    AWS_REGION      — defaults to us-east-1
"""

import argparse
import json
import os
import sys
import uuid

# ---------------------------------------------------------------------------
# Task templates
# ---------------------------------------------------------------------------
# Each template maps to a TaskRequest body. The load tester injects a unique
# request_id before sending so FIFO deduplication never collapses messages.
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, dict] = {
    # Minimal context — one helper file, short task
    "simple": {
        "repo": "test-org/sample-repo",
        "context_files": ["src/utils.py"],
        "target_file": "src/main.py",
        "task": "Add input validation to the process() function",
        "branch": "main",
        "max_tokens": 512,
    },
    # Several context files — tests prefix-cache reuse across agents
    "multi_context": {
        "repo": "test-org/sample-repo",
        "context_files": ["src/models.py", "src/utils.py", "src/config.py"],
        "target_file": "src/api.py",
        "task": "Add a DELETE /item/{id} endpoint following the existing patterns",
        "branch": "main",
        "max_tokens": 1024,
    },
    # Heavy context — stresses token budget and cache capacity
    "large_context": {
        "repo": "test-org/sample-repo",
        "context_files": [
            "src/models.py",
            "src/utils.py",
            "src/config.py",
            "src/auth.py",
            "src/db.py",
        ],
        "target_file": "src/main.py",
        "task": "Refactor the request handler to use the new middleware pattern",
        "branch": "feature/refactor",
        "max_tokens": 2048,
    },
    # Multiple tasks share the same target_file — useful for testing the
    # file-level lock mechanism (Phase 2+) and FIFO ordering (if group ID
    # is switched to target_file).
    "same_target": {
        "repo": "test-org/sample-repo",
        "context_files": ["src/utils.py"],
        "target_file": "src/shared.py",
        "task": "Add a helper function for string normalisation",
        "branch": "main",
        "max_tokens": 512,
    },
    # Non-default branch — exercises branch handling in build_message / commit
    "feature_branch": {
        "repo": "test-org/sample-repo",
        "context_files": ["src/models.py"],
        "target_file": "src/feature_x.py",
        "task": "Implement the stub methods in FeatureX according to the model",
        "branch": "feature/x",
        "max_tokens": 1024,
    },
}

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _build_message(template_name: str, idx: int) -> dict:
    body = dict(TEMPLATES[template_name])
    body["request_id"] = str(uuid.uuid4())
    return body


def push_messages(
    queue_url: str | None,
    region: str,
    template_name: str,
    count: int,
    dry_run: bool,
) -> None:
    template_cycle = list(TEMPLATES.keys()) if template_name == "all" else [template_name]

    sqs = None
    if not dry_run:
        try:
            import boto3
            sqs = boto3.client("sqs", region_name=region)
        except ImportError:
            print("ERROR: boto3 is not installed. Run: pip install boto3", file=sys.stderr)
            sys.exit(1)

    sent = 0
    for i in range(count):
        tpl_name = template_cycle[i % len(template_cycle)]
        body = _build_message(tpl_name, i)

        if dry_run:
            print(f"[dry-run] [{i + 1}/{count}] template={tpl_name} request_id={body['request_id']}")
            print(json.dumps(body, indent=2))
            print()
        else:
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(body),
                # Unique group per message → unordered FIFO (dedup without
                # serialisation). Switch to body["target_file"] in Phase 3+
                # to enforce per-file ordering.
                MessageGroupId=str(uuid.uuid4()),
                MessageDeduplicationId=body["request_id"],
            )
            print(f"[{i + 1}/{count}] sent template={tpl_name} request_id={body['request_id']}")
            sent += 1

    if not dry_run:
        print(f"\nDone — {sent}/{count} messages sent to {queue_url}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push load-test tasks into the SQS queue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--queue-url",
        default=os.environ.get("SQS_QUEUE_URL"),
        help="SQS queue URL (or set SQS_QUEUE_URL env var)",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--template",
        choices=[*TEMPLATES.keys(), "all"],
        default="all",
        help="Which template to use; 'all' cycles through every template",
    )
    parser.add_argument("--count", type=int, default=10, help="Number of messages to send")
    parser.add_argument("--dry-run", action="store_true", help="Print messages without sending")

    args = parser.parse_args()

    if not args.dry_run and not args.queue_url:
        parser.error("--queue-url (or SQS_QUEUE_URL) is required unless --dry-run is set")

    push_messages(
        queue_url=args.queue_url,
        region=args.region,
        template_name=args.template,
        count=args.count,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
