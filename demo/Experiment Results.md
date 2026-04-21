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

## Experiment 3 — Smart Caching Order

**Purpose:** Evaluate whether ordering uncached context files by a smarter heuristic than the default size-descending strategy improves cache efficiency and reduces wall time.

**Background:** The order in which uncached context files are accumulated determines which intermediate states get saved. Accumulating `A→B→C` saves keys `{A}`, `{A,B}`, `{A,B,C}`. A future task with context `{B,C}` misses all of these even though B and C were processed — because `{B,C}` was never a standalone cached key. Different orderings produce different intermediate states and therefore different hit patterns.

**Tradeoff:** The ordering strategy is fixed at deploy time and affects every task in the run. A strategy that creates more reusable intermediate states should lower total prefill tokens and LLM latency, but hit rate alone does not fully capture this — a hit on a large multi-file state saves more computation than a hit on a single small file state.

**Limitations:** All runs use the same 50-task seed, so task ordering is controlled. However, workers commit to the shared branch concurrently, meaning context file contents accumulate changes across the run — later tasks read slightly different file sizes than earlier ones, introducing minor token count variability between runs. The frequency strategy builds its table from zero each run (cleared on `/metrics/clear`), so its behavior shifts as the run progresses: early tasks use the fallback order, later tasks increasingly use observed frequencies.

**Why 3 workers:** At 1 worker, caching is already slower than naive — ordering cannot overcome the Redis overhead. At 5 workers, the benefit is already clear; the ordering effect is harder to isolate. At 3 workers, naive and cached are nearly tied, making the ordering's marginal impact most visible.

### Results (Seed 93, 3 Workers, Redis Cached, Compression On)

| Ordering Strategy | Total Time | Avg/Task | Input Tokens | LLM Latency | Hit Rate | Bytes Written |
|-------------------|-----------|----------|-------------|-------------|----------|--------------|
| `size_desc` (baseline) | 1,919.52 s | 38.39 s | 30,422 | 4,346.59 s | 40.0% | ~1.03 GB |
| `size_asc` | 1,500.18 s | 30.00 s | 25,092 | 3,261.74 s | 40.0% | ~680 MB |
| `frequency` + `size_desc` fallback | 2,060.97 s | 41.22 s | 30,631 | 4,885.25 s | 42.0% | ~962 MB |
| `frequency` + `size_asc` fallback | 1,333.83 s | 26.68 s | 26,800 | 2,986.35 s | 40.0% | ~823 MB |

### Analysis

**`size_asc` dramatically outperforms `size_desc`** despite an identical 40% hit rate: 22% faster wall time (1,500s vs. 1,920s), 25% less LLM latency, 17% fewer input tokens, and 34% less data written to Redis. The intuition is that small files tend to be broadly shared utilities (validators, base models, db connectors) — accumulating them first creates early intermediate states covering exactly those shared files, which many future tasks can hit. Large files tend to be feature-specific and appear in fewer task contexts, so caching them first produces intermediate states that rarely match future task prefixes.

**`frequency` + `size_desc` fallback is the worst performer** (2,061s, 7% slower than baseline). With only 50 tasks across 3 workers, the frequency table has very few observations early in the run — most tasks use the cold-start fallback, which is `size_desc`. The result is essentially `size_desc` with the overhead of Redis frequency tracking. The frequency signal doesn't have time to dominate.

**`frequency` + `size_asc` fallback is the best performer** (1,334s, 30% faster than `size_desc` baseline, 11% faster than pure `size_asc`). Two effects compound: the cold-start fallback is already the strongest ordering, and as the run progresses, the frequency table refines the order to further prioritize files that genuinely co-appear most often. Notably, this run processes more input tokens than `size_asc` (26,800 vs. 25,092) while being faster — the hits it gets are on larger, multi-file states that save more prefill time per hit, even though the raw hit rate is the same.

**The fallback dominates over frequency in short runs.** With 50 tasks across 3 workers, each worker sees only ~17 tasks. The frequency table only becomes meaningful after several tasks have been processed, leaving most of the run on fallback order. In longer runs or with more repeated context patterns, the frequency signal would compound and could diverge meaningfully from the fallback.

**Conclusion:** `size_asc` is a better default than `size_desc` for this workload, where shared context files tend to be small utilities. The `frequency` strategy with a `size_asc` fallback produces the best results by combining the strong cold-start ordering with adaptive refinement, and would likely show larger gains over pure `size_asc` in longer runs where the frequency table has more time to mature.

---
