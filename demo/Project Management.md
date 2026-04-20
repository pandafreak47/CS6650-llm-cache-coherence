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

**Status: Complete** | Seed: None (pre-reproducibility fix)

Goal: confirm the caching pipeline reduces token computation before involving real LLM latency.

| Mode   | Tasks | Input Tokens | Output Tokens | Cache Hit Rate | Bytes Written | Bytes Read |
|--------|-------|-------------|--------------|----------------|--------------|------------|
| Naive  | 50    | 37,180      | 14,437       | N/A            | 0            | 0          |
| Cached | 50    | 17,382      | 14,776       | 34.0%          | 90,044       | 38,274     |

Cached mode sent **53% fewer input tokens** than naive. 17 of 50 tasks hit a cached state. Output tokens are consistent, confirming correctness.

---

## Phase 2 — Centralized Redis Cache (DummyLLM)

**Status: Complete** | Seed: None (pre-reproducibility fix)

Goal: validate that a shared Redis cache enables cross-worker cache hits as worker count scales.

| Workers | Total Time | Avg/Task | Input Tokens | Hit Rate | Notes |
|---------|-----------|----------|-------------|----------|-------|
| 1       | 60.61 s   | 1.21 s   | 16,851      | 36.0%    | Post Git API update |
| 3       | 40.43 s   | 0.81 s   | 17,247      | 38.0%    | |
| 5       | 35.30 s   | 0.71 s   | 17,309      | 36.0%    | |

Hit rates remain consistent across worker counts, confirming the shared Redis cache enables cross-worker reuse. Total time improves with more workers as expected from parallelism.

**Problems encountered:**
- Initial 3-worker run showed a hit rate of 41.2% on 51 requests — one task was requeued after a worker restart. Fixed by the Git API update (replaced direct file-content fetching with a more robust approach), bringing the results to the "post Git API update" row above.

---

## Phase 3 — Anthropic API Caching

**Status: Complete** | Seed: None (pre-reproducibility fix)

Goal: measure server-side prefix cache savings using Anthropic's `cache_control` checkpoints.

| Workers | Total Time | Avg/Task | Input Tokens | LLM Latency | Hit Rate |
|---------|-----------|----------|-------------|-------------|----------|
| 1       | 203.08 s  | 4.06 s   | 57,597      | 109.87 s    | 42.0%    |
| 3       | 100.88 s  | 2.02 s   | 58,731      | 178.08 s    | 28.0%    |
| 5       | 131.08 s  | 2.62 s   | 95,322      | 773.57 s    | 34.5%    |

The 5-worker run processed 89 requests (not 50) due to SQS message redelivery under high concurrency. Total time increased from 3→5 workers due to Anthropic API rate limiting under concurrent load.

---

## Phase 4 — LlamaLLM (Initial — Broken)

**Status: Complete (documented as broken baseline)**

The initial `LlamaKVState` stored only accumulated prompt text — no real KV tensors. `accumulate()` called `generate(max_tokens=1)`, re-running full prefill on every call despite reporting cache hits. The "hits" retrieved a cached text string, but the LLM still reprocessed all context tokens.

| Workers | Total Time   | Avg/Task  | Input Tokens | Hit Rate |
|---------|-------------|-----------|-------------|----------|
| 1       | 5,479.41 s  | 109.59 s  | 82,208      | 38.0%    |
| 3       | 3,260.94 s  | 65.22 s   | 102,110     | 42.0%    |
| 5       | 1,029.82 s  | 20.60 s   | 96,284      | 30.0%    |

Input tokens were far higher than naive mode — the prompt was reconstructed and re-prefilled in full every call.

---

## Phase 4 (Fixed) — Real KV Tensor Caching

**Status: Complete** | Seed: None (pre-reproducibility fix, results consistent across runs)

**Implementation change:** Replaced text-only state with real binary KV tensors via llama-cpp-python's `save_state()`/`load_state()`. `accumulate()` now calls `model.eval()` on only new tokens (skipping tokens already in the restored KV state). `generate()` loads the saved state before inference, so llama-cpp-python's internal prefix matcher skips all accumulated context tokens — only the task prompt runs through prefill.

### Naive Baseline

| Workers | Total Time   | Avg/Task  | Input Tokens | LLM Latency   |
|---------|-------------|-----------|-------------|---------------|
| 1       | 5,040.47 s  | 100.81 s  | 56,388      | 4,976.88 s    |
| 3       | 2,707.25 s  | 54.15 s   | 83,801      | 7,682.91 s    |
| 5       | 1,250.67 s  | 25.01 s   | 61,492      | 5,440.60 s    |

### Redis Cached (compression on, `KV_COMPRESS=true`)

| Workers | Total Time   | Avg/Task  | Input Tokens | LLM Latency   | Hit Rate | Bytes Written |
|---------|-------------|-----------|-------------|---------------|----------|--------------|
| 1       | 3,909.85 s  | 78.20 s   | 26,714      | 3,097.28 s    | 40.0%    | ~1.16 GB     |
| 3       | 1,760.74 s  | 35.21 s   | 26,677      | 3,669.76 s    | 38.0%    | ~1.36 GB     |
| 5       | 1,430.08 s  | 28.60 s   | 38,409      | 5,674.07 s    | 42.0%    | ~1.21 GB     |

**1-worker comparison (directly comparable, same branch):** Naive 5,040s vs. cached 3,909s — **22% total time reduction**, **38% LLM latency reduction** (4,977s → 3,097s). Input tokens reduced from 56,388 to 26,714 (**53% fewer actual prefill tokens**).

---

## Compression vs. No Compression (Added Variable)

**Status: Complete** | In-memory backend, 1 worker, seed: None

Motivation: confirm whether zlib compression is net positive for in-memory vs. Redis backends. For in-memory, there is no network transfer — compression adds CPU overhead with no storage benefit. For Redis, compression reduces bytes transferred and stored in ElastiCache.

### In-Memory, 1 Worker

| Compression | Total Time   | LLM Latency  | Input Tokens | Hit Rate | Bytes Written |
|-------------|-------------|-------------|-------------|----------|--------------|
| On          | 5,861.86 s  | 4,561.12 s  | 29,731      | 34.0%    | ~1.20 GB     |
| Off         | 4,975.80 s  | 4,037.22 s  | 26,745      | 36.0%    | ~3.88 GB     |

Disabling compression reduced total time by **~15%** and LLM latency by **~11%** for in-memory. For in-memory, every `accumulate()` compresses a state that is immediately decompressed on the next call — pure overhead. For Redis, compression reduces bytes written (~1.16 GB vs. ~3.88 GB), reducing network pressure and ElastiCache memory use.

**Finding:** `KV_COMPRESS` was made a configurable env variable. The optimal setting depends on backend: `false` for in-memory, `true` for Redis.

---

## Reproducibility Improvements (Mid-Project)

Several changes were added mid-project after noticing that unseeded runs produced inconsistent results:

- **Task seed:** `test_runner.py` now auto-generates a random seed when `--seed` is omitted, prints the actual integer used, and allows exact re-runs with `--seed N`.
- **Model seed:** `LLAMA_SEED=42` fixes llama-cpp-python's internal RNG.
- **Temperature + seed = deterministic sampling:** `LLAMA_TEMPERATURE=0.8` with a fixed seed produces the same output every run.
- **Cache flush on metrics clear:** `POST /metrics/clear` now also calls `_cache.clear()`, flushing all Redis entries between runs to prevent cross-run state contamination.

The Seeded results (Seed 93) in `results.txt` were run after these improvements and show consistent hit rates across runs.

---

## Seeded Results (Seed 93, Redis Cached, Compression On)

| Workers | Total Time   | Avg/Task  | Input Tokens | LLM Latency   | Hit Rate |
|---------|-------------|-----------|-------------|---------------|----------|
| 1       | 5,201.88 s  | 104.04 s  | 27,909      | 4,337.43 s    | 42.0%    |
| 3       | 1,919.52 s  | 38.39 s   | 30,422      | 4,346.59 s    | 40.0%    |
| 5       | 1,157.03 s  | 23.14 s   | 30,664      | 3,806.52 s    | 39.2%    |

---

## Problems Encountered

| Problem | Root Cause | Resolution |
|---------|-----------|------------|
| KV caching produced no speedup initially | `accumulate()` was calling `generate(max_tokens=1)` — full prefill every call despite "hits" | Replaced with `model.eval(new_tokens)` on only uncached tokens; `save_state()`/`load_state()` for binary KV tensors |
| Redis `OutOfMemoryError` | LlamaState blobs contain full logit arrays (~64 MB uncompressed at 500 tokens); Redis ran out of memory | Added zlib compression (~3–5× reduction) + LRU entry-count cap via sorted set + upgraded to `cache.t3.medium` |
| `zlib.error: incorrect header check` | States written with `kv_compress=false` were loaded with `compress=true` after switching between runs | Auto-detect compression from zlib magic byte (`0x78` vs pickle `0x80`) in `_load_kv` — backward-compatible regardless of flag setting |
| Context overflow: `ValueError: Requested tokens exceed context window` | Task context files grew beyond 4096 tokens | Added try/except in `generate()` that retries with task prompt only on overflow; `LLAMA_N_CTX` made configurable |
| In-memory slower than Redis (5,861s vs 3,909s) on 1 worker | Different random task orderings (Seed: None) between runs; compression overhead in hot path | Identified root cause; added seeded runs for direct comparison; added compression toggle |
| Terraform `CacheClusterNotFound` on apply | ElastiCache deleted outside Terraform | `terraform state rm 'module.redis[0].aws_elasticache_cluster.this'` |
| Anthropic 5-worker run processed 89 tasks | SQS redelivery under rate-limiting — workers timed out and messages became re-visible | Documented as a limitation of the Anthropic backend under load; not a caching bug |

---

## Plan Changes Summary

| Original Plan | Final State |
|--------------|-------------|
| Crash recovery phase | Dropped — system handled cases correctly by design; results not interesting |
| LlamaLLM "broken" KV state (text only) | Discovered mid-project; documented as a broken baseline before fixing with real KV tensor serialization |
| Compression not in original plan | Added after Redis OOM issues; became its own experimental variable |
| 3 smart caching strategies compared | Only size-descending (the existing default) evaluated — time constraints prevented additional strategy implementations |
| Distributed KV cache (stretch goal) | Not implemented |
| Dependency-aware file locking (stretch goal) | Not implemented |
