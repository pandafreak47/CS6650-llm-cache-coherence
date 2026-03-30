# CS6650 Project Report — LLM Cache Coherence

**Course:** CS6650 Distributed Computing Systems
**Student:** Kenton Romero (romero.ke@northeastern.edu)

---

## Problem, Team, and Overview of Experiments

Modern AI coding agents are computationally expensive. Every time an agent processes a task, it re-reads the same context files — utility modules, shared models, database layers — and feeds them into an LLM from scratch. This means the same tokens are processed over and over, even when nothing has changed. At scale, across a swarm of agents working on the same codebase, this redundancy compounds quickly.

This project investigates whether distributed AI coding agents can share and reuse LLM prefix-cache state to reduce redundant token computation, and what coherence guarantees are needed to do so correctly. The core question: when multiple agents share the same context files, can we avoid re-running the attention prefill computation for those files on every agent — and what breaks when we try?

This is a solo project. I am the sole designer, implementer, and experimenter. AI (Claude Code) is used as a pair programming tool throughout — for code generation, refactoring, debugging, and brainstorming architectural tradeoffs. The project is a direct application of distributed systems concepts from the course: message queues, consistency models, at-least-once delivery, and horizontal scaling.

**Experiments being evaluated:**
- Token reduction from prefix-cache reuse (naive vs. cached build mode)
- Cache hit rate as a function of task overlap and worker count
- System behavior under worker crashes (at-least-once delivery semantics)
- Scaling behavior: how cache hit rate and total token savings change and what lock ups may occur from 1 → 3 → 5+ workers

**Observability:** Workers expose `/health`, `/status`, `/metrics`, and `/metrics/clear` HTTP endpoints. Logs are shipped to AWS CloudWatch. A future dashboard will aggregate metrics across pods.

---

## Project Plan and Recent Progress

The project is organized into six phases. Phase 0 (infrastructure) and Phase 1 (preliminary token baseline) are complete. Phases 2–5 are planned.

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Infrastructure: ECS, SQS, ECR, Terraform, end-to-end pipeline | Complete |
| 1 | Token baseline: DummyLLM, naive vs. cached, 1 pod | Complete |
| 2 | Centralized KV cache (Redis-backed), multi-pod validation | Planned |
| 3 | Crash recovery characterization | Planned |
| 4 | llama.cpp integration, full experiment matrix | Planned |
| 5 | Smart caching order (directory grouping, git recency) | Planned |

See [ProjectTimeline.md](ProjectTimeline.md) for the full breakdown of tasks and exit criteria per phase.

**AI's role in development:** Claude Code accelerated every phase — scaffolding Terraform modules, debugging AWS IAM and Docker build issues, writing the worker loop, and helping reason about distributed systems edge cases (e.g. at-least-once delivery and cache invalidation semantics). Because the tool is heavily subsidized, the cost has been negligible. The primary benefit is the speed of iteration: what would take a day of reading docs and debugging takes an hour. The tradeoff is that AI-generated code requires careful review — subtle bugs like undefined variable references and incorrect build context paths were introduced and required manual identification.

---

## Objectives

**Short-term (within the course):**
- Quantify the token savings from prefix-cache reuse across a realistic multi-file coding workload
- Characterize how cache hit rate scales with the number of workers sharing the same cache
- Extend the system to llama.cpp with real KV tensor sharing, not just a structural simulation
- Implement a distributed gossip-based cache where workers share KV states peer-to-peer rather than through a central Redis instance
- Build a live observability dashboard that tracks cache hit rate, token savings, and per-pod utilization across the full worker fleet

**Long-term (beyond the course):**
- Extend system to open coding agents with more advanced dependencies and code bases.


**Ensuring performance, reliability, and cost control:**
- Performance: cache hit rate and total tokens computed are the primary metrics; experiments are designed to isolate each variable
- Reliability: SQS FIFO with per-file message groups gives at-least-once delivery with implicit single-writer semantics; crash recovery is an explicit experiment phase
- Cost control: the LRU eviction policy bounds memory per pod; Redis centralizes state to prevent per-pod memory explosion at scale; DummyLLM validates the pipeline before incurring real LLM API costs

---

## Related Work

**Distributed systems foundations:**
The design of this system directly applies concepts from the course. SQS FIFO queues provide the message-passing backbone, with per-file message groups giving implicit per-file locking — a practical application of the single-writer principle discussed in the consistency lectures. The at-least-once delivery model and its interaction with idempotent operations (committing the same file twice is harmless) mirrors patterns from the distributed database readings. The LRU cache with set-based keys is a direct application of cache coherence principles.

**LLM prefix caching:**
Anthropic, OpenAI, and others have implemented server-side prefix caching that transparently reuses KV state for repeated prompt prefixes. This project extends that idea to the multi-agent, distributed setting, where the challenge is not just reuse within a single model server but coherence across agents sharing a mutable codebase. Prior work on speculative decoding and continuous batching (e.g. vLLM's PagedAttention) is relevant context for how KV state is managed at the inference engine level.

**Related Piazza projects:**

1. **SportsPulse (Prannov Jamadagni & Eroniction)** — Investigates Kafka vs. direct DB writes for live game event ingestion, and Redis cache vs. DB read replicas for fan query load. *Similar:* both projects use ECS horizontal scaling and measure the point at which a caching layer becomes worth its complexity. *Different:* SportsPulse is a read/write throughput problem with human users; this project is a compute-cost problem with AI agents. The bottleneck there is DB connection exhaustion; here it is redundant LLM prefill computation.

2. **Terraform MCP for AI Providers (Team 42: Vinal, Kalhar, Parin)** — Builds a protocol layer that gives LLM agents structured access to Terraform infrastructure context. *Similar:* both projects are infrastructure for AI agents, and both measure token efficiency (their baseline improvement comparison tracks token usage). *Different:* their project is about giving agents better *input* (richer context); this project is about making agents more efficient at *processing* that input by reusing cached computation.

3. **Self-Optimizing Infrastructure Agent (Dylan Pan, Zongwang Wang, Lucas Chen, Ellis Guo)** — Builds a closed-loop AI agent that reads Kafka CPU metrics and applies Terraform configurations to auto-scale the live environment. *Similar:* both projects involve AI agents operating on shared infrastructure, and both care about agent reliability and observability (their infra-events-topic audit log is analogous to this project's CloudWatch logging). *Different:* their agent makes infrastructure decisions; this project's agents make code edits. Their consistency concern is Terraform state; this project's is KV cache coherence.

---

## Methodology

**System architecture:**

```
Test Script → AWS SQS FIFO Queue → ECS Fargate Workers ↔ GitHub (Git Server)
                                           ↕
                                    In-Memory KV Cache (LRU)
                                    (Redis in Phase 2+)
```

Workers pull coding tasks from a shared SQS FIFO queue. Each message specifies a target file to rewrite, a set of context files to read, and a task prompt. Workers fetch file contents from a shared GitHub repository, build a prompt (naive or cached), call an LLM, parse the rewritten file from the output, commit it back to GitHub, and acknowledge the SQS message. SQS FIFO message groups are keyed by target file, giving implicit single-writer semantics without explicit coordination.

**Prompt-build strategies:**

*Naive:* All context files and the target file are fetched and concatenated into a single prompt string. No caching. Simple, correct for all backends.

*Cached:* The worker maintains a local LRU cache mapping sets of context file paths to the LLM's KV state after processing those files. On each task, it finds the largest cached subset of the required context files, processes only the uncached remainder (ordered by descending file size, to maximize prefix sharing), and passes only the target file and task prompt as the final prompt. After a successful commit, all cache entries containing the modified file are invalidated.

**LLM backends:**

| Backend | KV State | Purpose |
|---------|----------|---------|
| DummyLLM | passthrough (empty dict) | Pipeline validation, token-count baseline |
| AnthropicLLM | ignored (server-side) | API-based baseline |
| LlamaLLM | full tensor support (planned) | Primary experiment target |

**Observability:**
Each worker exposes `/health`, `/status`, `/metrics`, and `/metrics/clear` over HTTP. Metrics track total input tokens, output tokens, LLM latency, and request count. Logs are shipped to CloudWatch. The test runner times end-to-end queue drain and reports tasks/second.

**Tradeoffs being evaluated:**
- Token savings vs. cache management overhead (invalidation cost, LRU eviction)
- Local per-pod cache (no coordination, low hit rate at scale) vs. centralized Redis cache (higher hit rate, network overhead)
- Naive simplicity vs. cached complexity (more code paths, more failure modes)

---

## Preliminary Results

### Phase 1: DummyLLM Token Count Baseline (1 pod, 50 tasks)

The DummyLLM approximates input token count as `len(prompt) // 4`. This gives a structural baseline for how many tokens each build mode sends to the LLM, independent of any real model behavior.

Tasks were sampled from the [CS6650-test-repo](https://github.com/pandafreak47/CS6650-test-repo) dependency graph — a 14-file Python order management service with realistic import chains. The same 50-task set (fixed random seed) was run under both modes.

| Mode | Tasks | Approx. Input Tokens | Cache Hit Rate |
|------|-------|----------------------|----------------|
| Naive | 50 | 34,364 | N/A |
| Cached | 50 | 27,508 | ~38% |

**Cached mode processed ~20% fewer tokens than naive.** This is consistent with the expected behavior: context files that appear in multiple tasks (e.g. `utils/validators.py`, `db/connection.py`, `models/user.py`) are processed once and reused from the KV cache on subsequent tasks, avoiding re-sending their token content to the model.

**What remains to collect:**
- Phase 2: multi-pod centralized cache — does cache hit rate increase when 3+ pods share a Redis-backed store?
- Phase 3: crash recovery timing — redelivery delay and behavior under each failure scenario
- Phase 4: real token computation with llama.cpp — the DummyLLM savings are structural; real savings depend on actual KV reuse at the attention layer
- Phase 5: smart ordering strategies — can directory-based or git-recency ordering improve hit rate beyond the size-descending baseline?

**Pathological worst-case workload:** A task set where every task targets a different file with a completely unique set of context files — zero overlap. The cache never warms up, every task is a full cache miss, and cached mode performs *worse* than naive due to the overhead of cache lookups and incremental LLM calls that each return an empty KV state. The best-case workload is the inverse: many tasks sharing the same large context files, with only the target file changing between tasks.

---

## Impact

This project demonstrates a concrete approach to reducing compute cost for swarms of AI coding agents — a problem that becomes economically significant at scale. As AI agents are deployed in larger numbers to automate software engineering tasks, the cost of repeated LLM prefill computation on shared context files is a real and growing expense. A 20% token reduction on a structural baseline suggests meaningful savings in production with a real LLM backend.

Beyond cost, this project explores the distributed systems challenges that arise when AI agents share mutable state: cache coherence, at-least-once delivery, and the interaction between file modification and cached computation. These are not AI-specific problems — they are classical distributed systems problems applied to a new domain.

Other students can directly use this system against their own repositories by swapping the `GITHUB_TOKEN` and `--repo-url` in the test runner, and adding a `deps.json` to their repo. This makes it straightforward to measure token savings on any codebase, not just the example test repo used here.
