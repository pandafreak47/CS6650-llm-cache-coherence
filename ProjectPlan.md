# Project Plan & Timeline

## Architecture Overview

```
Agents / Load Test Script
         │  POST /task
         ▼
   ┌─────────────┐
   │  API Service │  (ECS Fargate, ecs_count replicas)
   │  FastAPI     │  — validates request, assigns request_id, enqueues
   └──────┬──────┘
          │  SQS FIFO queue  (llm-tasks.fifo)
          ▼
   ┌─────────────┐
   │   Workers   │  (ECS Fargate, ecs_worker_count replicas)
   │  worker.py  │  — dequeue → build_message → llm.generate → commit → ack
   └─────────────┘
```

**Key properties:**
- One task processed per worker container at a time (LLM consumes full memory)
- Scale workers independently via `ecs_worker_count` in terraform
- FIFO queue with per-message deduplication; SQS visibility timeout acts as the
  implicit file lock (prevents double-processing during Phase 0–1)
- Dead-letter queue captures tasks that fail `sqs_max_receive_count` times
- `LLM_BACKEND` and `QUEUE_BACKEND` env vars select implementations at runtime

---

## Phase 0 — Infrastructure (Days 1–2) ✓

Get the full request-to-worker pipeline running end-to-end with dummy
implementations before writing any interesting distributed logic.

**Deliverables:**
- ECS API service (FastAPI) + ECS worker service sharing one ECR image
- SQS FIFO queue wiring the two services together
- `LLM_BACKEND=dummy` worker that echoes the target file (no API spend)
- `QUEUE_BACKEND=memory` mode for fully local development
- CloudWatch logging with `request_id` threaded through every log line
- Load test script (`scripts/load_test.py`) that pushes templated tasks to SQS
- Terraform outputs: queue URL, API/worker cluster names, DLQ URL

**Baseline measurement:** confirm request_id appears in both API and worker
logs and that the DLQ stays empty under `load_test.py --count 50`.

---

## Phase 1 — Naive Baseline (Days 2–3)

Run the real LLM backend with multiple workers and no caching. Each worker
independently fetches all context files and processes them from scratch, even
when other workers have already processed identical file sets.

**Deliverables:**
- `message_builder.py` fetches real file contents from the repo (GitHub API
  or local git clone)
- `commit.py` writes the LLM output back to the repo and pushes a commit
- Run `load_test.py --template all --count 100` with `ecs_worker_count = 4`
- Record: total tokens, mean task latency, per-template token breakdown

**The point of this phase:** establish the control group. Show concretely how
many tokens are wasted when N workers each re-process the same shared context
files. Every subsequent phase is measured against this baseline.

---

## Phase 2 — Centralized Cache Coordinator (Days 3–5)

A single coordinator process builds a shared KV snapshot of processed context
files and writes it to Redis. Workers call `load_state()` to reuse it,
processing only their task-specific tokens.

**Deliverables:**
- Redis cache layer keyed on `hash(sorted(context_files))`
- Workers check cache before calling LLM; populate it on miss
- Demonstrate SPOF: kill the coordinator mid-run, show workers stall
- Measure token savings vs Phase 1 baseline

---

## Phase 3 — Consistency Under Context Updates (Days 5–7)

While workers are running, inject a simulated file change — overwrite the
shared context snapshot with a newer version. Implement and compare:

- **Strong consistency:** broadcast invalidation, block workers until they reload
- **Eventual consistency:** workers finish on stale snapshot, reconcile after

**Key demo moment:** eventual consistency produces workers with conflicting
assumptions about the codebase — a concrete, observable consistency violation.

---

## Phase 4 — Distributed Cache (Days 7–9)

Replace the centralized Redis coordinator with a distributed cache:

- **Consistent hashing** — `hash(sorted(context_files)) % N` maps a context
  set to a responsible node deterministically; no coordinator needed
- **Gossip protocol** — nodes discover membership and propagate cache state;
  demonstrate partition tolerance by network-isolating a node
- **Lamport timestamps** — total order on cache writes without a global clock;
  show stale reads without them, correct behavior with them
- **CAP tradeoff** — measure availability vs consistency during a partition;
  discuss what each choice means for cache coherence specifically

Each mechanism is a measurable experiment with a before/after graph.

---

## Stretch Goal — Prefix Trie Cache Manager

Rather than one shared snapshot per context-file set, the coordinator builds a
trie of snapshots at different prefix depths: system prompt only, system + file
A, system + file A + file B, etc. Incoming tasks are matched to the deepest
applicable trie node, maximising reuse regardless of file ordering.

The interesting research question is the **pre-warming policy**: which
combinations are worth building in advance given limited memory? Territory of
Belady's algorithm and LFU/LRU policy comparison.

---

## Metrics (every phase produces the same four numbers)

| Metric | Description |
|---|---|
| Total tokens computed | Across all workers for the full task set |
| Cache hit rate | Fraction of LLM calls that reused a cached prefix |
| Mean task latency | Wall-clock time from enqueue to commit |
| Consistency violations | Phase 3+ only: conflicting agent assumptions observed |

Four phases × four metrics = a presentable comparison graph and a clear
narrative arc from naive to novel.
