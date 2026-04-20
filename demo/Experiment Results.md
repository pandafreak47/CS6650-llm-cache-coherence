<!-- [10 marks] Experiments!  This is a detailed representation of experiments and results in a report that can be shared as a .pdf.  This should be no more than 5 pages, and should show off your technical abilities without drowning the reader in data and details!  For each experiment, please provide:  
    The purpose of this experiment, the tradeoff you were exploring, the limitations of what you did
    The results, in detail, with appropriate charts and graphs
    An analysis of the results, what evidence they revealed in terms of the tradeoffs they were designed to address, what your conclusions are based on this evidence, and what limitations there are to this analysis -->
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