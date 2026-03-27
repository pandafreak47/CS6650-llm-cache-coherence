# Project Plan & Timeline

## Phase 0 — Infrastructure (Days 1–2)

Get your environment running and your measurement harness in place before writing any interesting code. This means EC2 setup, installing Ollama with a small model, and building the `LLMBackend` wrapper we sketched earlier. Critically, instrument every agent call to log tokens computed, tokens cached, and wall-clock latency to a file or SQLite DB. All your results graphs come from this log.

## Phase 1 — Naive Baseline (Days 2–3)

Implement the simplest possible thing: 3–4 agents, each one independently receiving the full shared context plus its task and calling the model from scratch. No coordination, no caching. Run a fixed set of tasks and record your metrics. This is your control group — every subsequent phase is measured against it. The point isn't that this is bad, it's that you can *show* concretely why it's bad.

## Phase 2 — Centralized Cache Coordinator (Days 3–5)

A single coordinator process builds the shared KV snapshot and writes it to a file or Redis key. Agents read from it with `load_state()` and only process their task tokens. Measure the token savings. Then simulate a coordinator crash mid-run (just kill the process) and show that agents stall — this demonstrates the SPOF problem concretely and motivates the next phase. This is the meatiest implementation phase.

## Phase 3 — Consistency Under Context Updates (Days 5–7)

While agents are running, inject a simulated file change — overwrite the shared context snapshot with a newer version. Implement two strategies and compare them: strong consistency (broadcast an invalidation signal, block all agents until they reload) versus eventual consistency (let agents finish on the stale snapshot, reconcile after). The key demo moment here is showing that eventual consistency produces agents with conflicting assumptions about the codebase — a real, observable consistency violation. That's a compelling result to present.

## Phase 4 — Cache-Aware Scheduling (Days 7–9)

Add a lightweight gossip layer where agents periodically broadcast which files they have warm. The scheduler uses this to route incoming tasks to agents most likely to have a cache hit. Compare against random routing on the same task set. Use Lamport timestamps or a simple version counter so the scheduler can tell if an agent's advertised state is stale after a context update.

## Stretch Goal — Prefix Trie Cache Manager

Rather than maintaining a single shared snapshot, the coordinator builds a trie of snapshots at different prefix depths: system prompt only, system + file A, system + file A + file B, etc. Incoming agent tasks are matched to the deepest applicable trie node, maximizing reuse. This directly addresses the "can't combine files in arbitrary order" constraint by pre-building the most common combinations. The interesting research question becomes the pre-warming policy: which combinations are worth building in advance, given limited memory? That's a cache eviction and prediction problem — territory of Belady's algorithm and LFU/LRU policy comparison.

---

## What We're Measuring Throughout

Every phase produces the same four numbers, making comparison clean: total tokens computed across all agents, cache hit rate, mean task latency, and (in Phase 3) consistency violation count. Four phases, four data points per metric — that's a presentable graph and a clear narrative arc from naive to novel.