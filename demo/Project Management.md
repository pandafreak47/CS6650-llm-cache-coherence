<!-- [5 Marks] Project Management!  This is a representation of how you moved from your initial design to your final state (however you represented this is fine, show how you broke the problem down, who worked on what, problems encountered along the way etc.) -->

# Project Management — Shared Prefill Over Distributed AI Agents

## Original Plan vs. What Was Built

The original plan (see `UpdatedProjectPlan.md` / `ProjectTimeline.md`) outlined five phases: DummyLLM baseline, centralized Redis cache validation, crash/recovery testing, full LlamaLLM matrix, and smart caching ordering strategies. The final implementation diverged in three ways:

1. **Crash recovery testing was dropped.** The SQS at-least-once delivery guarantee with FIFO message groups handles the failure cases by design. Testing confirmed that a message re-appears after the visibility timeout and is re-processed correctly, but the results were not interesting enough to include — the system just works as expected.

2. **Compression vs. no-compression was added as an experimental variable.** `LlamaState` blobs contain full logit arrays (`n_tokens × vocab_size × 4 bytes`), which can reach 20–150 MB uncompressed per state. zlib compression (~3–5× reduction) was added as a configurable toggle (`KV_COMPRESS`), and both settings were benchmarked to isolate the compression/decompression CPU overhead from cache transfer savings.

3. **Smart caching order was not multi-strategy compared.** Only the default size-descending strategy was evaluated. Directory grouping and git-recency ordering were not implemented due to time constraints. FIXME - talk about the one test implimented in furture

---

## Phase 0 — Infrastructure & Scaffolding

**Status: Complete**

- Deployed ECS Fargate workers, SQS FIFO queue, ECR, and VPC via Terraform
- Worker loop verified: receive SQS message → build prompt → DummyLLM → commit to GitHub → ack
- Health, status, metrics, and metrics/clear endpoints all confirmed working
- Autoscaling on queue depth verified
- Test runner creates fresh branch per run, seeds queue, times drain

---

## Phase 1 — DummyLLM Token Count Baseline

**Status: Complete** (unseeded — results discarded)

Goal: confirm the caching pipeline reduces token computation before involving real LLM latency. These runs were done before the reproducibility fixes (no task seed, no model seed) and are not included in final results.

Key finding: cached mode reduced input tokens by ~53% vs. naive, validating the pipeline architecture before introducing real LLM latency. This was sufficient to proceed to Phase 2.

---

## Phase 2 — Centralized Redis Cache (DummyLLM)

**Status: Complete** (unseeded — results discarded)

Goal: validate that a shared Redis cache enables cross-worker cache hits as worker count scales with a mocked LLM (DummyLLM class). Ran 1, 3, and 5 workers. Hit rates remained consistent across worker counts, confirming the shared Redis cache enables cross-worker reuse. These runs were also unseeded and are not included in final results.

**Problems encountered:**
- An initial 3-worker run produced 51 requests instead of 50 — one task was requeued after a worker restart mid-processing. Fixed by updating the Git file-fetching code to be more robust before continuing.

---

## Phase 3 — Anthropic API Caching

**Status: Complete** (unseeded — results discarded)

Goal: measure server-side prefix cache savings using Anthropic's `cache_control` checkpoints. The Anthropic backend demonstrated that server-side caching works without local KV tensor management. The 5-worker run processed 89 tasks instead of 50 due to SQS redelivery under Anthropic API rate limiting — a limitation of the API backend under concurrent load, not a caching bug.

---

## Phase 4 — LlamaLLM (Initial — Broken KV State)

**Status: Complete (documented as broken baseline)**

The initial `LlamaKVState` stored only accumulated prompt text — no real KV tensors. `accumulate()` called `generate(max_tokens=1)`, re-running full prefill on every call despite reporting cache hits. The "hits" retrieved a cached text string, but the LLM still reprocessed all context tokens. Input token counts were far higher than naive mode, which exposed the bug. These runs were unseeded and results are discarded.

---

## Phase 4 (Fixed) — Real KV Tensor Caching

**Status: Complete**

**Implementation change:** Replaced text-only state with real binary KV tensors via llama-cpp-python's `save_state()`/`load_state()`. `accumulate()` now calls `model.eval()` on only new tokens (skipping tokens already in the restored KV state). `generate()` loads the saved state before inference, so llama-cpp-python's internal prefix matcher skips all accumulated context tokens — only the task prompt runs through prefill.

---

## Reproducibility Improvements (Mid-Project)

After early runs produced inconsistent results, the following changes were made before collecting final data:

- **Task seed:** `test_runner.py` now auto-generates a seed when `--seed` is omitted, prints the actual integer used, and allows exact re-runs with `--seed N`.
- **Model seed:** `LLAMA_SEED=42` fixes llama-cpp-python's internal RNG for sampling.
- **Temperature + seed = deterministic sampling:** `LLAMA_TEMPERATURE=0.8` with a fixed seed produces the same token sequence every run.
- **Cache flush on metrics clear:** `POST /metrics/clear` now also calls `_cache.clear()`, flushing all Redis entries between runs to prevent cross-run state contamination.

All final results below use **Seed 93** (task order) and **LLAMA_SEED=42** (model).

---

## KV State Compression — Design Decision

When LlamaLLM was first run against Redis, workers began crashing with `OutOfMemoryError`. `LlamaState` blobs include full logit arrays (`n_tokens × vocab_size × 4 bytes`), which reach 20–150 MB uncompressed per state. Redis was running out of memory within a single run.

The initial fix was zlib compression, which reduces blob size ~3–5× and also reduces the bytes transferred over the network to ElastiCache on every cache read and write — a real benefit in a distributed setting. This brought Redis memory use down to a manageable level and improved throughput.

Later, when evaluating the in-memory backend as a comparison point, it became clear that compression was hurting performance there: states are compressed, stored in a Python dict, and immediately decompressed on the next call — pure CPU overhead with no storage or network benefit. This motivated making compression a configurable toggle (`KV_COMPRESS` env var) so experiments could isolate the CPU cost of compression from the network transfer savings it provides in the Redis case.

---

## Problems Encountered

| Problem | Root Cause | Resolution |
|---------|-----------|------------|
| KV caching produced no speedup | `accumulate()` called `generate(max_tokens=1)` — full prefill every call despite cache hits | Replaced with `model.eval(new_tokens)` on only uncached tokens; `save_state()`/`load_state()` for binary KV tensors |
| Redis `OutOfMemoryError` | LlamaState blobs contain full logit arrays (~64 MB uncompressed at 500 tokens) | Added zlib compression (~3–5× reduction) + LRU entry-count cap via sorted set + upgraded to `cache.t3.medium` |
| `zlib.error: incorrect header check` | States written with `kv_compress=false` loaded with `compress=true` after switching between runs | Auto-detect compression via zlib magic byte (`0x78` vs pickle `0x80`) in `_load_kv` — backward-compatible regardless of flag |
| Context overflow: `ValueError: Requested tokens exceed context window` | Task context files grew beyond 4096 tokens after many commits modified them | Added try/except in `generate()` that retries with task prompt only on overflow; `LLAMA_N_CTX` made configurable |
| Inconsistent results across runs | No task seed — different task orderings mutate the branch differently, changing context file sizes for all subsequent tasks | Added deterministic task seed to `test_runner.py`; added `LLAMA_SEED` and `LLAMA_TEMPERATURE` to make model output deterministic |
| Terraform `CacheClusterNotFound` on apply | ElastiCache cluster deleted outside Terraform | `terraform state rm 'module.redis[0].aws_elasticache_cluster.this'` |

---

## Plan Changes Summary

| Original Plan | Final State |
|--------------|-------------|
| Crash recovery phase | Dropped — system handled all cases correctly by design; results not interesting |
| LlamaLLM "broken" KV state (text only) | Discovered mid-project; documented as a broken baseline before fixing |
| Compression not in original plan | Added after Redis OOM issues; became its own experimental variable |
| 3 smart caching strategies compared | Only size-descending (existing default) evaluated — time constraints |
| Distributed KV cache (stretch goal) | Not implemented |
| Dependency-aware file locking (stretch goal) | Not implemented |
