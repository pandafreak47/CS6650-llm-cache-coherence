# LLM Cache Coherence — CS6650 Distributed Systems

**Course:** CS6650 Distributed Computing Systems
**Topic:** KV-cache coherence and prefix-cache reuse across distributed LLM coding agents

---

## Overview

This project investigates whether distributed AI coding agents can share and reuse LLM prefix-cache state to reduce redundant token computation, and what coherence guarantees are needed to do so correctly. Agents pull coding tasks from a shared queue, read and modify a shared GitHub repository, and call an LLM to perform each task. The central question is: when multiple agents share the same context files, can we avoid re-running the attention prefill computation for those files on every agent — and what breaks when we try?

---

## Architecture

```
Test Script ──► AWS SQS ──► AI Agent Workers ◄──► GitHub (Git Server)
                                    ▲
                                    │
                               KV Cache
```

### AWS SQS (Task Queue)

A FIFO queue distributes coding tasks to workers. Each message carries:

```json
{
  "git_repo":      { "url": "https://github.com/owner/repo", "branch": "test-abc123" },
  "context_files": ["src/utils.py", "src/models.py"],
  "target_file":   "src/main.py",
  "task_prompt":   "Add input validation to all public functions."
}
```

Messages are grouped by `target_file`. SQS delivers only one message per group at a time, giving an implicit per-file lock — no two workers will edit the same file concurrently. All code changes are assumed to be backward-compatible, so no further locking is required.

### Git Server (GitHub)

The shared repository is hosted on GitHub. Workers authenticate via a `GITHUB_TOKEN` injected at deploy time. Workers fetch file contents and commit results using the `git` CLI. Each test run starts from a fresh branch forked off `main`, ensuring a clean baseline.

### AI Agent Workers

One worker per pod (ECS Fargate task). Each worker runs this loop:

```
loop:
    receive message from SQS
    build prompt  (naive or cached)
    call LLM
    parse rewritten file from output
    commit & push to GitHub
    acknowledge message to SQS
```

Workers also expose a lightweight HTTP server for observability:

| Endpoint         | Description |
|------------------|-------------|
| `GET /health`    | Liveness check |
| `GET /status`    | `standby` or `processing` |
| `GET /metrics`   | Total input tokens, output tokens, LLM latency, request count |
| `POST /metrics/clear` | Reset all counters |

Pods are scaled horizontally via Terraform (ECS autoscaling on SQS queue depth). There is no intra-pod parallelism — one LLM call at a time per pod.

### KV Cache

Workers maintain a local LRU in-memory KV cache (fixed entry count, evicts least-recently-used). The cache maps a **set** of context file paths to the LLM's KV state after processing those files. Keys are order-independent — `{a.py, b.py}` and `{b.py, a.py}` are the same cache entry. Lookup finds the largest cached subset of the requested context files, so a partial hit is still useful.

After each successful commit, the worker invalidates every cache entry whose file set includes the modified file, preventing stale KV states from being reused on changed content.

The cache is backed by `KVCacheInterface`, making it straightforward to swap in a centralized Redis-backed implementation.

### Prompt Format

File contents are wrapped in XML tags (the format Anthropic Claude is explicitly trained on):

```xml
<file path="src/utils.py">
...file content...
</file>
```

The LLM is seeded to emit only the rewritten file content, terminated by `</file>`, and nothing else.

---

## Prompt-Build Strategies

### Naive

Fetches all context files and the target file from Git, assembles one large prompt string. KV state is always empty. Simple and correct for all LLM backends.

```
[context file 1] [context file 2] ... [target file] [task] → LLM
```

### Cached

Treats context files as a set and finds the largest cached subset. Orders the remaining (uncached) files by descending size — largest files contribute the most tokens and are most likely to be shared with future tasks. Processes only the uncached remainder incrementally, saving each new KV state. Passes only the target file + task as the final prompt, with the full context living in the KV state.

```
Context files: {ctx1, ctx2, ctx3}
Cache hit:     {ctx1, ctx2}  →  reuse KV state
Process:        ctx3         →  new KV state {ctx1, ctx2, ctx3} saved
Final prompt:  [target file] [task]  +  KV state  →  LLM
```

This strategy is the primary vehicle for the caching experiments and is most meaningful with llama.cpp, where KV state is a real reusable tensor.

---

## LLM Backends

All backends implement the same interface:

```python
class InterfaceLLM:
    def generate(prompt, kv_state, max_tokens, system) -> (KVState, str)
    def metrics(reset=False) -> (input_tokens, output_tokens, latency_ms)
```

| Backend       | KV State | Metrics | Purpose |
|---------------|----------|---------|---------|
| `DummyLLM`    | passthrough | approx. input tokens only | Pipeline testing, preliminary token-count experiments |
| `AnthropicLLM`| ignored (server-side caching) | full | API-based baseline |
| `LlamaLLM`    | full support (planned) | full | Primary experiment target |

---

## Experiment Matrix

See [ProjectTimeline.md](ProjectTimeline.md) for the full phase plan.

| Strategy \ Workers         | 1 | 3 | 5+ |
|----------------------------|---|---|----|
| Naive (no caching)         |   |   |    |
| Centralized KV Cache       |   |   |    |
| Smart caching order        |   |   |    |
| Distributed cache          |   |   |    |

Metrics collected per cell: total tokens computed, cache hit rate, mean task latency.

---

## Experiment Results

*Results will be filled in as experiments are completed.*

### Phase 1: DummyLLM Token Count Baseline (1 pod)

| Mode   | Tasks | Approx. Input Tokens | Cache Hit Rate |
|--------|-------|----------------------|----------------|
| Naive  |       |                      | N/A            |
| Cached |       |                      |                |

### Phase 2: Centralized KV Cache — DummyLLM validation

| Workers | Cache Entries Written | Cache Hit Rate | Notes |
|---------|-----------------------|----------------|-------|
| 3       |                       |                |       |

### Phase 3: Crash Recovery

| Scenario | Redelivery Delay (s) | Task Outcome | Notes |
|----------|--------------------|--------------|-------|
| Crash before LLM call | | | |
| Crash after LLM, before commit | | | |
| Crash after commit, before ack | | | Duplicate commit risk |

### Phase 4: llama.cpp — Full Experiment Matrix

| Strategy \ Workers         | 1 | 3 | 5+ |
|----------------------------|---|---|----|
| Naive (no caching)         |   |   |    |
| Centralized KV Cache       |   |   |    |

Metrics per cell: total tokens computed / cache hit rate / mean task latency (s).

### Phase 5: Smart Caching Order

| Ordering Strategy | Workers | Total Tokens | Cache Hit Rate | vs. Size-Based |
|-------------------|---------|--------------|----------------|----------------|
| Size descending (baseline) | 3 | | | — |
| Directory grouping | 3 | | | |
| Git recency | 3 | | | |

---

## Repository Structure

```
├── src/                    # Worker source code
│   ├── main.py             # Entry point: SQS polling loop + HTTP server
│   ├── models.py           # Pydantic models (SQSMessage, KVState, etc.)
│   ├── message_builder.py  # Naive and cached prompt-build implementations
│   ├── git_client.py       # git CLI wrapper (fetch, commit, push)
│   ├── sqs_client.py       # boto3 SQS wrapper
│   ├── kv_cache.py         # KVCacheInterface + InMemoryKVCache (LRU)
│   ├── commit.py           # Parses LLM output and commits to GitHub
│   ├── Dockerfile
│   └── llm/
│       ├── interface.py    # InterfaceLLM abstract base + file tag constants
│       ├── dummy_llm.py    # DummyLLM
│       ├── anthropic_llm.py
│       └── llama_llm.py    # Stub — not yet implemented
├── test_script/
│   └── test_runner.py      # Seeds SQS, creates test branch, times drain
├── terraform/              # AWS infrastructure (ECS, SQS, ECR, VPC)
│   └── modules/
│       ├── ecs/            # Fargate service + autoscaling
│       ├── sqs/            # FIFO queue
│       ├── ecr/            # Container registry
│       ├── network/        # VPC, subnets, security groups
│       └── logging/        # CloudWatch log groups
├── ProjectTimeline.md      # Phase-by-phase implementation plan
└── UpdatedProjectPlan.md   # Architecture design notes
```

---

## Configuration

All runtime behaviour is controlled via environment variables (set in `terraform/variables.tf`):

| Variable          | Default                      | Description |
|-------------------|------------------------------|-------------|
| `LLM_BACKEND`     | `dummy`                      | `dummy`, `anthropic`, or `llama` |
| `LLM_MODEL`       | `claude-haiku-4-5-20251001`  | Model ID (Anthropic only) |
| `ANTHROPIC_API_KEY` | —                          | Required when `LLM_BACKEND=anthropic` |
| `GITHUB_TOKEN`    | —                            | GitHub PAT with repo read/write |
| `SQS_QUEUE_URL`   | —                            | Injected by Terraform |
| `BUILD_MODE`      | `naive`                      | `naive` or `cached` |
| `KV_CACHE_SIZE`   | `100`                        | Max LRU entries per worker |
| `AWS_REGION`      | `us-east-1`                  | |
