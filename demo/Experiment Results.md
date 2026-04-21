<!-- [10 marks] Experiments!  This is a detailed representation of experiments and results in a report that can be shared as a .pdf.  This should be no more than 5 pages, and should show off your technical abilities without drowning the reader in data and details!  For each experiment, please provide:  
    The purpose of this experiment, the tradeoff you were exploring, the limitations of what you did
    The results, in detail, with appropriate charts and graphs
    An analysis of the results, what evidence they revealed in terms of the tradeoffs they were designed to address, what your conclusions are based on this evidence, and what limitations there are to this analysis -->

# Experiments — Shared Prefill Over Distributed AI Agents

All experiments use TinyLlama-1.1B-Chat Q4_K_M on ECS Fargate (2 vCPU / 4 GB per worker), 50 tasks, Seed 93 (task order), LLAMA_SEED=42, LLAMA_TEMPERATURE=0.8.

---

## Experiment 1 — Naive vs. Centralized KV Cache (LlamaLLM)

**Purpose:** Measure whether sharing real KV tensor state across workers reduces total prefill computation and wall time, and characterize the tradeoff between accumulate overhead and prefill savings at different worker counts.

**Tradeoff:** The cached strategy requires calling `accumulate()` for each uncached context file before every task — this extends the KV state incrementally and stores blobs to Redis (~800 MB–1.1 GB total written per run). This overhead only pays off if cross-worker cache hits avoid enough prefill computation to outweigh the accumulate cost.

**Limitations:** TinyLlama's context window (2048 training tokens; run at 4096) means some tasks overflow and fall back to task-prompt-only inference. Output quality is intentionally low — the experiment measures compute cost, not correctness. Input token counts are not directly comparable across different worker-count runs when workers modify the shared branch concurrently, so later tasks read different file contents depending on which commits landed first.

### Results

| Strategy | Workers | Total Time | Avg/Task | Input Tokens | LLM Latency | Hit Rate |
|----------|---------|-----------|----------|-------------|-------------|----------|
| Naive    | 1       | 3,752.89 s | 75.06 s | 60,117      | 3,688.52 s  | N/A      |
| Naive    | 3       | 1,966.43 s | 39.33 s | 78,242      | 5,520.35 s  | N/A      |
| Naive    | 5       | 1,339.98 s | 26.80 s | 83,274      | 5,803.06 s  | N/A      |
| Redis Cached | 1  | 5,201.88 s | 104.04 s | 27,909    | 4,337.43 s  | 42.0%    |
| Redis Cached | 3  | 1,919.52 s | 38.39 s | 30,422     | 4,346.59 s  | 40.0%    |
| Redis Cached | 5  | 1,157.03 s | 23.14 s | 30,664     | 3,806.52 s  | 39.2%    |

### Analysis

The caching strategy reduces prefill tokens by **~54%** consistently across all worker counts (60–83K naive → ~28–31K cached). However, the wall-time benefit depends entirely on worker count:

- **1 worker:** Cached is **39% slower** than naive (5,202s vs. 3,753s). With only one worker, there are no cross-worker cache hits — every state must still be accumulated from scratch. The Redis round-trips for writing and reading ~800 MB of KV blobs add latency that outweighs the prefill savings.

- **3 workers:** Nearly tied — cached 1,920s vs. naive 1,966s (~3% faster). The crossover point. Cross-worker hits begin to appear (40% hit rate), shared states start saving real prefill time, but the per-worker overhead is still significant.

- **5 workers:** Cached is **14% faster** (1,157s vs. 1,340s), while computing 63% fewer input tokens. The shared Redis cache delivers consistent ~39% hit rates across workers, and the prefill savings dominate the accumulate overhead.

The key insight is that the KV cache overhead is **fixed per task** (you always pay the accumulate cost), but the **savings are only realized on hits**. With one worker, hits are rare because the cache is cold for most of the run. With more workers, earlier workers warm the cache and later tasks — across all workers — benefit.

LLM latency increases from 1W→3W in naive mode (3,688s → 5,520s) because each worker runs prefill independently on the full context, so total LLM time scales with workers × tasks. In cached mode, shared states mean the total LLM compute is bounded by unique context combinations, not worker count.

---

## Experiment 2 — Cache Backend and Compression Overhead (1 Worker)

**Purpose:** Determine the cost of serialization and network transfer in the Redis backend vs. in-process in-memory storage, and whether zlib compression is net positive for each.

**Tradeoff:** Redis enables cross-worker sharing but adds network I/O per cache operation. In-memory avoids network overhead but cannot share state across workers. Compression reduces bytes transferred over the network (~3.8× reduction) at the cost of CPU time on every `accumulate()` and `generate()` call.

**Limitations:** 1-worker experiments cannot demonstrate cross-worker sharing — this is purely a measurement of serialization and network overhead. All runs have the same hit rate (42%) and identical input tokens (27,909), isolating the overhead as the only variable.

### Results

| Backend   | Compression | Total Time   | LLM Latency  | Bytes Written |
|-----------|-------------|-------------|-------------|--------------|
| In-Memory | Off         | 3,925.53 s  | 3,387.36 s  | ~3.12 GB     |
| In-Memory | On          | 4,949.32 s  | 4,133.08 s  | ~0.82 GB     |
| Redis     | Off         | 5,159.06 s  | 4,295.51 s  | ~3.12 GB     |
| Redis     | On          | 5,201.88 s  | 4,337.43 s  | ~0.82 GB     |

### Analysis

**In-memory vs. Redis (compression off):** In-memory is **24% faster** in total time (3,926s vs. 5,159s) and **21% faster** in LLM latency (3,387s vs. 4,296s). The delta (~1,233s wall time over 50 tasks) is entirely due to Redis network I/O — each `accumulate()` writes a ~60 MB blob and each `generate()` reads one, adding ~25s of network overhead per task on average.

**Compression on in-memory:** Slower than compression off by **1,024s** (4,949s vs. 3,926s). For in-memory, compression is pure overhead: the blob is compressed immediately before storing and decompressed immediately on the next read, with no storage or transfer benefit. The CPU cost of compressing ~3 GB of KV data dominates.

**Compression on Redis vs. off Redis:** Nearly identical total time (5,202s vs. 5,159s, ~0.8% difference). Compression reduces bytes written from ~3.12 GB to ~0.82 GB, relieving ElastiCache memory pressure substantially, but the network time saved is offset by CPU compression cost — a wash in total latency.

**Conclusion:** For in-memory backends, compression should be disabled. For Redis, compression is necessary to stay within ElastiCache memory limits (without it, Redis OOMs on a single run) and has negligible latency cost. `KV_COMPRESS` is exposed as a configurable env variable to allow this distinction.

---

## Experiment 3 — Smart Caching Order (Planned)

**Purpose:** Evaluate whether ordering uncached context files by a smarter heuristic than file size improves cache hit rate, and consequently reduces total prefill tokens.

**Current implementation:** Uncached context files are accumulated largest-first (size-descending). The rationale is that larger files contribute more tokens and are more likely to be shared with future tasks, maximizing the reuse value of each saved state.

**Planned comparison:** Run the same 50-task workload (Seed 93, 3 workers, Redis cached, compression on) with an alternative ordering strategy and compare hit rate and input tokens against the size-descending baseline (1,919.52s, 30,422 tokens, 40.0% hit rate).

**Why 3 workers:** At 1 worker, caching is already slower than naive — smart ordering cannot overcome the baseline overhead. At 5 workers, the benefit is already present; the ordering effect is harder to isolate from parallelism gains. At 3 workers, caching and naive are nearly tied, so any improvement in hit rate from smarter ordering has the clearest marginal impact.

| Ordering Strategy | Workers | Total Time | Input Tokens | Hit Rate | vs. Size-Based |
|-------------------|---------|-----------|-------------|----------|----------------|
| Size descending (baseline) | 3 | 1,919.52 s | 30,422 | 40.0% | — |
| TBD | 3 | | | | |

---
