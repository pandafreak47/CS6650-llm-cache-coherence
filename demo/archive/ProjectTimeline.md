# Project Timeline

## Phase 0 — Infrastructure & Scaffolding

**Goal:** Full pipeline running end-to-end in AWS with DummyLLM. No real LLM calls yet — just verify that every component works.

**Tasks:**
- Deploy ECS workers, SQS queue, and ECR via Terraform
- Verify worker loop: receive SQS message → build prompt → DummyLLM → commit to GitHub → ack
- Confirm `/health`, `/status`, `/metrics`, `/metrics/clear` endpoints respond correctly
- Confirm autoscaling triggers on queue depth
- Run test script: create branch, seed queue, verify drain

**Exit criteria:** `test_runner.py` with `LLM_BACKEND=dummy` completes a full run with 1 pod, messages drain, and commits appear on the test branch.

---

## Phase 1 — Preliminary Experiment: Token Count Baseline (DummyLLM)

**Goal:** Establish a rough token-count baseline before any real LLM is involved. Use the DummyLLM's character-based token approximation to compare naive vs. cached build modes on the same task set.

**Tasks:**
- Run test suite with `BUILD_MODE=naive`, 1 pod — record approximate total input tokens via `/metrics`
- Run same suite with `BUILD_MODE=cached`, 1 pod — record same metric
- Document how many tokens the cached mode avoids re-sending across the task set
- Record cache hit rate

**Experiment:** DummyLLM, 1 pod, naive vs. cached — token count comparison

| Mode   | Tasks | Approx. Input Tokens | Output Tokens | Cache Hit Rate | Cache Bytes Written | Cache Bytes Read |
|--------|-------|----------------------|---------------|----------------|---------------------|------------------|
| Naive  | 50    | 37,180               | 14,437        | N/A            | —                   | —                |
| Cached | 50    | 17,382               | 14,776        | 34.0%          | 90,044              | 38,274           |

Cached mode sent **53% fewer input tokens** than naive. 17 of 50 tasks hit a cached state, avoiding re-sending shared context files. Output tokens are consistent across modes (same files rewritten), confirming correctness.

**Exit criteria:** ✅ Numbers in the table above. Cached mode shows measurably fewer total tokens on the task set with repeated context files.

---

## Phase 2 — Centralized KV Cache

**Goal:** Build the centralized cache infrastructure before introducing a real LLM. This keeps memory pressure off individual pods — the KV states live in Redis, not in each worker's heap — and means the architecture is already in place when llama.cpp is added in Phase 5.

**Tasks:**
- Implement a Redis-backed `KVCacheInterface` (swap in via the existing interface — no worker code changes beyond wiring)
- Deploy Redis via Terraform (ElastiCache or a sidecar container)
- Validate with DummyLLM: confirm keys are written and read across multiple pods
- Stub out KV state serialization (real tensors come in Phase 5; for now serialize the empty dict)

**Exit criteria:** With 3+ pods running, cache keys written by one pod are successfully read by another. Validated via logs and `/metrics` hit-rate reporting.

<!-- ---

## Phase 3 — Crash Recovery

**Goal:** Characterize what happens to the system when a worker pod crashes mid-task. SQS visibility timeout provides the recovery mechanism — a message becomes re-visible if not acknowledged within the timeout window.

**Tasks:**
- Run a multi-worker test and kill a worker pod mid-processing (before ack)
- Measure: time until SQS redelivers the message, whether the partial commit causes issues, total additional latency
- Verify the redelivered task completes correctly
- Document failure scenarios: crash before commit vs. crash after commit but before ack (at-least-once delivery)

**Experiment:** Crash recovery scenarios

| Scenario | Redelivery Delay (s) | Task Outcome | Notes |
|----------|--------------------|--------------|-------|
| Crash before LLM call | | | |
| Crash after LLM, before commit | | | |
| Crash after commit, before ack | | | Duplicate commit risk |

**Exit criteria:** All three scenarios documented with observed behaviour. Duplicate-commit risk identified and noted. -->

---

## Phase 4 — llama.cpp Integration & Full Experiment Matrix

**Goal:** Replace DummyLLM with llama.cpp. The centralized cache and crash recovery are already in place — this phase swaps in the real backend and runs the full experiment matrix. Memory pressure per pod is bounded because KV states live in Redis, not in the worker heap.

**Model:** TinyLlama-1.1B-Chat-v1.0 Q4_K_M (~670 MB GGUF). Runs on CPU — no GPU required. Output quality is intentionally low; the experiment measures token counts and latency, not answer correctness.

**Compute:** TinyLlama needs ~1.25 GB resident memory (weights + KV cache + process overhead). Terraform automatically allocates **2 vCPU / 4 GB** when `llm_backend = "llama"` is set, vs. 0.5 vCPU / 1 GB for other backends. Model is downloaded from HuggingFace at worker boot; `/health` reports download progress until ready.

**Tasks:**
- ✅ Implement `LlamaLLM` with full `LlamaKVState` and `metrics()` support
- ✅ Auto-scale Fargate compute tier based on `llm_backend` (Terraform locals)
- ✅ Download model from HuggingFace on boot with `/health` progress reporting
- ✅ Implement KV state serialization/deserialization to/from Redis
- ✅ Run the full matrix: naive vs. centralized cache, across 1 / 3 / 5+ pods

**Experiment:** Full matrix, llama.cpp, real token computation

| Strategy \ Workers         | 1 | 3 | 5+ |
|----------------------------|---|---|----|
| Naive (no caching)         |   |   |    |
| Centralized KV Cache       |   |   |    |
| Centralized KV Cache - no compression       |   | N/A | N/A  |
| In Memory Cache        |   | N/A | N/A  |
| In Memory Cache - no compression       |   | N/A | N/A  |

note: compression and no compression tests and in memeory tests added later to show best network and compression overhead

Metrics per cell: total tokens computed, cache hit rate, mean task latency (s).

**Exit criteria:** Full matrix populated. Naive baseline established as control group; centralized cache shows measurable improvement in tokens computed and/or latency.

---

## Phase 5 — Smart Caching Order

**Goal:** Improve cache hit rate by ordering context files more intelligently than simple size-descending. Evaluate whether structural cues (directory proximity, shared git history) improve prefix reuse over the naive size-based order.

**Tasks:**
- Implement alternative ordering strategies (e.g. group files by directory, order by recency in git log)
- Compare cache hit rate and total token savings against Phase 4 (size-based order, centralized cache)
- Run across 1, 3, and 5+ pods

**Experiment:** Smart caching order, llama.cpp, 1 / 3 / 5+ workers

| Ordering Strategy | Workers | Total Tokens | Cache Hit Rate | vs. Size-Based |
|-------------------|---------|--------------|----------------|----------------|
| Size descending (baseline) | 3 | | | — |
| Directory grouping | 3 | | | |
| Git recency | 3 | | | |

**Exit criteria:** At least one alternative ordering strategy shows a statistically meaningful improvement in cache hit rate or total tokens over size-based ordering.

---

## Stretch Goal A — Distributed KV Cache

Rather than a single centralized cache, workers gossip their local KV states to each other. Each worker maintains its own LRU cache and periodically broadcasts which file-prefix keys it holds. Incoming tasks are routed to the worker most likely to have a cache hit. Evaluate whether the reduced coordination overhead outweighs the lower hit rate compared to a fully centralized cache.

---

## Stretch Goal B — Dependency-Aware File Locking

The current SQS message-group mechanism prevents two workers from editing the *same* file concurrently, but it does not account for *dependency* relationships between files. For example: if worker A is currently editing `src/utils.py` and a new task arrives that lists `src/utils.py` as a context file for editing `src/main.py`, that new task is operating on a file that is mid-change — its context is potentially stale before the task even begins.

This stretch goal adds a dependency-aware lock layer:

- Workers broadcast which files they are actively editing
- The scheduler checks whether any context file of an incoming task is currently being edited by another worker
- If so, the task is held (or deprioritized) until the dependency clears

This introduces real scheduling concerns — priority inversion, starvation of tasks with popular dependencies, and the question of whether blocking on a dependency is better or worse than running on a stale snapshot. It connects directly to the classical distributed systems literature on optimistic vs. pessimistic concurrency control.
