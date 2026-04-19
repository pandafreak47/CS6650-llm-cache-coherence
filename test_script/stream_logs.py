#!/usr/bin/env python3
"""
Stream CloudWatch logs from all running ECS worker tasks in real time.

Usage
-----
  python stream_logs.py                        # auto-discover tasks
  python stream_logs.py --cluster my-cluster --service my-service
  python stream_logs.py --log-group /ecs/my-service --region us-west-2

Each worker's output is prefixed with a short worker label (W1, W2, …)
so you can tell them apart in a single terminal.

Environment variables
---------------------
  AWS_REGION   — defaults to us-east-1
"""
from __future__ import annotations

import argparse
import os
import threading
import time

import boto3

from _config import AWS_REGION, CONTAINER, ECS_CLUSTER, ECS_SERVICE, LOG_GROUP

# ANSI colours — one per worker, cycles if >6
_COLOURS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[31m"]
_RESET = "\033[0m"

_print_lock = threading.Lock()


def _label_print(label: str, colour: str, text: str) -> None:
    with _print_lock:
        for line in text.rstrip("\n").splitlines():
            print(f"{colour}[{label}]{_RESET} {line}")


def discover_task_ids(cluster: str, service: str, region: str) -> list[str]:
    ecs = boto3.client("ecs", region_name=region)
    arns = ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING").get("taskArns", [])
    # Short task ID is the last segment of the ARN
    return [arn.split("/")[-1] for arn in arns]


def tail_log_stream(
    log_group: str,
    stream_name: str,
    label: str,
    colour: str,
    region: str,
    poll_interval: float,
    stop_event: threading.Event,
) -> None:
    logs = boto3.client("logs", region_name=region)
    next_token: str | None = None
    # Start from the tail — skip historical logs older than 60 s
    start_time = int((time.time() - 60) * 1000)

    while not stop_event.is_set():
        kwargs: dict = {"logGroupName": log_group, "logStreamName": stream_name, "startFromHead": True}
        if next_token:
            kwargs["nextToken"] = next_token
        else:
            kwargs["startTime"] = start_time

        try:
            resp = logs.get_log_events(**kwargs)
        except logs.exceptions.ResourceNotFoundException:
            # Stream doesn't exist yet — task may still be starting
            time.sleep(poll_interval)
            continue

        events = resp.get("events", [])
        if events:
            for event in events:
                _label_print(label, colour, event["message"])

        new_token = resp.get("nextForwardToken")
        if new_token != next_token:
            next_token = new_token
        else:
            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream CloudWatch logs from all ECS workers")
    parser.add_argument("--cluster", default=ECS_CLUSTER)
    parser.add_argument("--service", default=ECS_SERVICE)
    parser.add_argument("--log-group", default=LOG_GROUP)
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--region", default=AWS_REGION)
    parser.add_argument("--poll", type=float, default=2.0, help="Seconds between CloudWatch polls (default: 2)")
    args = parser.parse_args()

    print(f"Discovering tasks in {args.cluster}/{args.service}…")
    task_ids = discover_task_ids(args.cluster, args.service, args.region)
    if not task_ids:
        print("No running tasks found.")
        return

    print(f"Streaming logs for {len(task_ids)} task(s). Ctrl-C to stop.\n")

    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    for i, task_id in enumerate(task_ids):
        stream_name = f"ecs/{args.container}/{task_id}"
        label = f"W{i + 1}"
        colour = _COLOURS[i % len(_COLOURS)]
        _label_print(label, colour, f"→ stream: {args.log_group}/{stream_name}")
        t = threading.Thread(
            target=tail_log_stream,
            args=(args.log_group, stream_name, label, colour, args.region, args.poll, stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping…")
        stop_event.set()


if __name__ == "__main__":
    main()
