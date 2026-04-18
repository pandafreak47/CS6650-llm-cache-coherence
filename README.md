# Shared Prefill Over Distributed AI Agents — CS6650 Distributed Systems

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
                               LLM State Cache
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

The shared repository is hosted on GitHub. Workers authenticate via a `GITHUB_TOKEN` injected at deploy time. Workers fetch file contents and commit results using the `git` CLI. Each test run starts from a fresh branch forked off `main`, ensuring a clean baseline. [repo](https://github.com/pandafreak47/CS6650-test-repo)

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

| Endpoint              | Description |
|-----------------------|-------------|
| `GET /health`         | Liveness check |
| `GET /status`         | `standby` or `processing` |
| `GET /metrics`        | Total input tokens, output tokens, LLM latency, request count, Anthropic cache read/write tokens |
| `POST /metrics/clear` | Reset all counters |

Pods are scaled horizontally via Terraform (ECS autoscaling on SQS queue depth). There is no intra-pod parallelism — one LLM call at a time per pod.

### LLM State Cache

Workers maintain a local LRU in-memory state cache (fixed entry count, evicts least-recently-used). The cache maps a **set** of context file paths to the backend-specific `LLMState` after processing those files. Keys are order-independent — `{a.py, b.py}` and `{b.py, a.py}` are the same cache entry. Lookup finds the largest cached subset of the requested context files, so a partial hit is still useful.

After each successful commit, the worker invalidates every cache entry whose file set includes the modified file, preventing stale states from being reused on changed content.

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

Fetches all context files and the target file from Git, assembles one large prompt string. State is always empty. Simple and correct for all LLM backends.

```
[context file 1] [context file 2] ... [target file] [task] → LLM
```

### Cached

Treats context files as a set and finds the largest cached subset. Orders the remaining (uncached) files by descending size — largest files contribute the most tokens and are most likely to be shared with future tasks. Calls `llm.accumulate()` for each uncached file to extend the state without generating output, saving each new state to the cache. Passes only the target file + task as the final prompt, with the full context carried in the state.

```
Context files: {ctx1, ctx2, ctx3}
Cache hit:     {ctx1, ctx2}  →  reuse state
Accumulate:     ctx3         →  new state {ctx1, ctx2, ctx3} saved
Final prompt:  [target file] [task]  +  state  →  LLM
```

For `AnthropicLLM`, accumulation is free — no API call is made. The state holds content blocks that are re-sent with `cache_control` markers on the real generation call, triggering Anthropic's server-side prefix cache.

For `LlamaLLM` (planned), accumulation runs the actual prefill computation and stores real KV tensors.

---

## LLM Backends

All backends implement the same interface:

```python
class InterfaceLLM:
    def generate(prompt, state, max_tokens, system) -> (LLMState, str)
    def accumulate(prompt, state) -> LLMState   # no-output context extension
    def empty_state() -> LLMState               # backend-specific zero state
    def metrics(reset=False) -> (input_tokens, output_tokens, latency_ms)
```

| Backend       | State type | Accumulate API call? | Purpose |
|---------------|------------|----------------------|---------|
| `DummyLLM`    | `LLMState` (passthrough) | yes (max_tokens=1, cheap) | Pipeline testing, token-count experiments |
| `AnthropicLLM`| `AnthropicCachedState` (content blocks) | no | API-based caching experiments |
| `LlamaLLM`    | `LlamaKVState` (KV tensors, planned) | yes (real prefill) | Primary experiment target |

### LLM State Hierarchy

```
LLMState                    — base / empty state (DummyLLM, naive builds)
├── AnthropicCachedState    — ordered list of ContentBlock; re-sent with
│                             cache_control on the last block each call
└── LlamaKVState            — serialised KV-cache tensors (stub)
```

### How Anthropic Caching Works

`AnthropicLLM` with `BUILD_MODE=cached`:

1. `accumulate()` appends each context file as a `ContentBlock` — no API call.
2. The final `generate()` sends all blocks as structured message content with `cache_control: {"type": "ephemeral"}` on the **last** context block.
3. Anthropic caches the full prefix up to that checkpoint server-side for 5 minutes.
4. Workers sharing the same context file set retrieve the same `AnthropicCachedState` from the local cache and send byte-identical content — Anthropic's server-side prefix cache fires.
5. `/metrics` reports `total_cache_read_tokens` and `total_cache_creation_tokens` from Anthropic's usage response.

`BUILD_MODE=naive` with `AnthropicLLM` also works: the empty state means no content blocks are accumulated, so `generate()` sends a single full-prompt block with no `cache_control` marker (no caching benefit, but correct output).

---

## Experiment Matrix

See [ProjectTimeline.md](ProjectTimeline.md) for the full phase plan.

| Strategy \ Workers         | 1 | 3 | 5+ |
|----------------------------|---|---|----|
| Naive (no caching)         |   |   |    |
| Centralized LLM State Cache|   |   |    |
| Smart caching order        |   |   |    |
| Distributed cache          |   |   |    |

Metrics collected per cell: total tokens computed, cache hit rate, mean task latency.

---

## Experiment Results

### Phase 1: DummyLLM Token Count Baseline (1 pod)

50 tasks sampled from the test repo dependency graph (fixed seed). Token count approximated as `len(prompt) // 4` by DummyLLM.

| Mode   | Tasks | Approx. Input Tokens | Cache Hit Rate |
|--------|-------|----------------------|----------------|
| Naive  | 50    | 34,364               | N/A            |
| Cached | 50    | 27,508               | ~38%           |

Cached mode sent **~20% fewer tokens** than naive. Savings come from context files shared across tasks (`utils/validators.py`, `db/connection.py`, `models/user.py`) being processed once and reused from the cache.

### Phase 2: Centralized LLM State Cache — DummyLLM validation

| Workers | Cache Entries Written | Cache Hit Rate | Notes |
|---------|-----------------------|----------------|-------|
| 3       |                       |                |       |

### Phase 3: Crash Recovery

| Scenario | Redelivery Delay (s) | Task Outcome | Notes |
|----------|--------------------|--------------|-------|
| Crash before LLM call | | | |
| Crash after LLM, before commit | | | |
| Crash after commit, before ack | | | Duplicate commit — expected and benign, since committing the same change twice is idempotent |

### Phase 4: llama.cpp — Full Experiment Matrix

| Strategy \ Workers         | 1 | 3 | 5+ |
|----------------------------|---|---|----|
| Naive (no caching)         |   |   |    |
| Centralized LLM State Cache|   |   |    |

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
│   ├── models.py           # LLMState hierarchy, SQSMessage, domain models
│   ├── message_builder.py  # build_naive() and build_cached()
│   ├── git_client.py       # git CLI wrapper (fetch, commit, push)
│   ├── sqs_client.py       # boto3 SQS wrapper
│   ├── kv_cache.py         # KVCacheInterface + InMemoryKVCache (LRU)
│   ├── commit.py           # Parses LLM output and commits to GitHub
│   ├── Dockerfile
│   └── llm/
│       ├── interface.py    # InterfaceLLM abstract base + file tag constants
│       ├── anthropic_llm.py # AnthropicLLM with cache_control checkpoints
│       ├── dummy_llm.py    # DummyLLM
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

| Variable            | Default                     | Description |
|---------------------|-----------------------------|-------------|
| `LLM_BACKEND`       | `dummy`                     | `dummy`, `anthropic`, or `llama` |
| `LLM_MODEL`         | `claude-haiku-4-5-20251001` | Model ID (Anthropic only) |
| `ANTHROPIC_API_KEY` | —                           | Required when `LLM_BACKEND=anthropic` |
| `GITHUB_TOKEN`      | —                           | GitHub PAT with repo read/write |
| `SQS_QUEUE_URL`     | —                           | Injected by Terraform |
| `BUILD_MODE`        | `naive`                     | `naive` or `cached` |
| `KV_CACHE_SIZE`     | `100`                       | Max LRU entries per worker |
| `AWS_REGION`        | `us-east-1`                 | |
