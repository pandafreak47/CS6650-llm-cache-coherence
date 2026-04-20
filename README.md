<!-- [5 marks] Code!   This is a link to your shared repo, and can also include links to individual repos that were active during the creation of your final project.  The README should introduce the project and why you built it!  The idea here is to show "activity" along the way, with how the project progressed over time (commits, issues, etc., everything you have) -->

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

Workers maintain a state cache keyed by **set** of context file paths. Keys are order-independent — `{a.py, b.py}` and `{b.py, a.py}` resolve to the same entry. Lookup finds the largest cached subset of the requested context files, so a partial hit is still useful.

After each successful commit, the worker invalidates every cache entry whose file set includes the modified file, preventing stale states from being reused on changed content.

Two implementations share the `KVCacheInterface`:

- **`InMemoryKVCache`** — LRU `OrderedDict`, per-worker, capped at `KV_CACHE_SIZE` entries.
- **`RedisKVCache`** — shared across workers via ElastiCache. Entry count is also capped at `KV_CACHE_SIZE` using a Redis sorted set (score = insertion timestamp); the oldest entry is evicted on overflow. Stale-file invalidation cleans the sorted set alongside the primary key index. Redis is configured with `maxmemory-policy = noeviction` so it crashes loudly rather than silently dropping entries mid-experiment.

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

For `LlamaLLM`, accumulation runs the actual prefill computation on only the new tokens and stores real KV tensors — see [LlamaLLM](#llamallm--model--compute) below.

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
| `LlamaLLM`    | `LlamaKVState` (real KV tensors + prompt) | no (incremental eval only) | Primary experiment target |

#### LlamaLLM — Model & Compute

**Model:** [TinyLlama-1.1B-Chat-v1.0 Q4_K_M](https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF) — a 1.1B parameter model quantized to ~4 bits per weight. GGUF file is ~670 MB. Chosen because it runs entirely on CPU with no GPU dependency, which keeps the Fargate setup simple while still exercising real attention prefill computation. Output quality is low; that is intentional — the experiment measures token counts and latency, not answer correctness.

**Memory:** ~950 MB for model weights + ~300 MB KV cache (4096-token context) + ~150 MB Python/uvicorn overhead ≈ **~1.25 GB resident**. The default Fargate allocation for `LLM_BACKEND=dummy/anthropic` is 0.5 vCPU / 1 GB, which would OOM. Terraform automatically switches to **2 vCPU / 4 GB** when `llm_backend = "llama"` is set in `terraform.tfvars`.

**Download:** The model is not baked into the Docker image. Each worker downloads it from HuggingFace on first boot (configurable via `LLAMA_MODEL_URL`), saves it to `/tmp/model.gguf`, and reports download progress in `/health` until ready.

### LLM State Hierarchy

```
LLMState                    — base / empty state (DummyLLM, naive builds)
├── AnthropicCachedState    — ordered list of ContentBlock; re-sent with
│                             cache_control on the last block each call
└── LlamaKVState            — accumulated prompt text + zlib-compressed,
                              pickled LlamaState binary (real KV tensors)
```

### How LlamaLLM KV Caching Works

`LlamaLLM` with `BUILD_MODE=cached`:

1. `accumulate(file, state)` loads the previously saved `LlamaState` binary via `load_state()`, tokenizes the full extended prompt, and calls `eval()` on **only the new tokens** — tokens already in the KV cache (tracked via `n_tokens`) are skipped entirely.
2. After each `accumulate()`, `save_state()` captures the full KV tensor state (attention keys and values for all layers) and stores it as a zlib-compressed, pickled blob in `LlamaKVState.llama_state_b64`.
3. The final `generate()` call loads the saved state before inference. llama-cpp-python's internal prefix matcher detects that `eval_tokens` already covers the context files and skips those tokens in prefill — only the short task prompt runs through attention.
4. `total_input_tokens` in `/metrics` counts only tokens that actually ran through prefill; `total_cache_read_tokens` counts tokens served from the KV cache.

`BUILD_MODE=naive` with `LlamaLLM`: always passes the full reconstructed prompt with no state loaded — all tokens prefilled every call.

### How Anthropic Caching Works

`AnthropicLLM` with `BUILD_MODE=cached`:

1. `accumulate()` appends each context file as a `ContentBlock` — no API call.
2. The final `generate()` sends all blocks as structured message content with `cache_control: {"type": "ephemeral"}` on the **last** context block.
3. Anthropic caches the full prefix up to that checkpoint server-side for 5 minutes.
4. Workers sharing the same context file set retrieve the same `AnthropicCachedState` from the local cache and send byte-identical content — Anthropic's server-side prefix cache fires.
5. `/metrics` reports `total_cache_read_tokens` and `total_cache_creation_tokens` from Anthropic's usage response.

`BUILD_MODE=naive` with `AnthropicLLM` also works: the empty state means no content blocks are accumulated, so `generate()` sends a single full-prompt block with no `cache_control` marker (no caching benefit, but correct output).

---

## Repository Structure

```
├── src/                    # Worker source code
│   ├── main.py             # Entry point: SQS polling loop + HTTP server
│   ├── models.py           # LLMState hierarchy, SQSMessage, domain models
│   ├── message_builder.py  # build_naive() and build_cached()
│   ├── git_client.py       # git CLI wrapper (fetch, commit, push)
│   ├── sqs_client.py       # boto3 SQS wrapper
│   ├── kv_cache.py         # KVCacheInterface + InMemoryKVCache + RedisKVCache (both LRU)
│   ├── commit.py           # Parses LLM output and commits to GitHub
│   ├── Dockerfile
│   └── llm/
│       ├── interface.py    # InterfaceLLM abstract base + file tag constants
│       ├── anthropic_llm.py # AnthropicLLM with cache_control checkpoints
│       ├── dummy_llm.py    # DummyLLM
│       └── llama_llm.py    # LlamaLLM with real KV tensor caching
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
| `KV_CACHE_SIZE`     | `100`                       | Max LRU entries in the state cache (both memory and Redis backends) |
| `CACHE_BACKEND`     | `memory`                    | `memory` or `redis` |
| `REDIS_URL`         | —                           | Required when `CACHE_BACKEND=redis` (injected by Terraform) |
| `LLAMA_MODEL_PATH`  | `/tmp/model.gguf`           | Local path where the GGUF model is saved after download |
| `LLAMA_MODEL_URL`   | TinyLlama Q4_K_M on HuggingFace | Override to use a different GGUF model |
| `AWS_REGION`        | `us-east-1`                 | |
