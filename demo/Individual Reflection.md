<!-- each team member should reflect on what they personally learned in this project.  
This is from your own individual perspective---maybe about the most challenging bug you fixed, or experiment you ran. 
It is important to share what went wrong, why it went wrong, and how you would handle it next time!  Where possible, reflect back on some of the specific concepts of the course, and refer back to how the concepts apply to the final project you did.
The big idea here is that you show your growth from Day 1 to Final project, and capture some of the highs and lows for you to remind yourself (and your future employer?!) one day :) -->

# Shared Prefill Over Distributed AI Agents – Individual Reflection
Kenton Romero

Starting the class, I had already been familiar with many distributed systems topics, but I had no formal training. This class gave me a well-rounded background in the field and solidified my interest in it. The final project was the first time I came in genuinely unsure of my hypothesis and walked out surprised by almost every result.

## Surprises and Lows

My initial hypothesis was simple: caching the key-value attention state of my AI agents would always be faster than recomputing it from scratch. What I found was more nuanced. The overhead of serializing, compressing, writing, and reloading hundreds of megabytes of KV tensor state did not pay off until enough workers could share those cached states concurrently. At one worker, the Redis round-trips made caching 39% *slower* than naive. At five workers, caching was 14% faster and computed 63% fewer input tokens. The crossover happened around three workers. I learned about the costs for saving that prefill time, and the cost only became worth paying at scale.

The second surprise was how dramatically the *ordering* of context file accumulation changed performance. Because each intermediate state is saved as a separate cache entry, the order files are accumulated determines which subsets future tasks can hit. I found that accumulating smallest files first (`size_asc`) outperformed my original largest-first default (`size_desc`) by 22% in wall time, with identical hit rates. The reason is that small files in the test repo tend to be broadly shared utilities — validators, base models, database connectors — that appear across many task contexts. Large files tend to be feature-specific and rarely create reusable intermediate states. Same hit rate, very different savings per hit.

## The Reproducibility Bug

A painful mistake I made was assuming my test runner had a default seed, but after realizing all my input token counts for the 1 worker case being different, I knew something was up. I checked and realized every benchmark had a unique set of tasks, making direct comparisons dubious. Luckily this also prompted me to properly seed my LLM responses so the real 1 worker tests are deterministic and can be compared directly. The lesson: in any benchmark-driven research project, check reproducibility on the very first run, not after you have already collected all your data.

## The KV Cache Bug

Another bug is when the KV Cache was incorrectly implimented for the llama model. All the documentation and my AI coding assistant both suggested the actual model key-value tensors were being cached. But the prior implementations for the Anthropic and mock backends had used plain context text as their "state," and when I first wired up the llama.cpp backend, claude code followed the same pattern. The code ran without errors — it just accumulated prefills without ever saving or reusing the KV tensors. The result was worse than naive: each task ran full prefill on an ever-growing context and the saved state was never used. I did not catch it until the benchmark numbers came back far worse than expected, which meant I had to rerun everything once the real `save_state()` / `load_state()` implementation was in place. Next time, I will write an explicit test that verifies KV state size changes between `accumulate()` calls before running any timed experiments.

## Course Concepts in Practice

Several concepts from the course showed up directly in the design. SQS FIFO message groups gave me implicit per-file locking at no extra complexity — messages sharing the same `MessageGroupId` (the target file path) are delivered one at a time, so no two workers ever edit the same file concurrently. Fault tolerance came for free from the same mechanism: if a worker crashes before committing its SQS acknowledgement, the message becomes visible again and another worker picks it up. Because each task is fully self-contained and all code changes were designed to be backward-compatible, completing the same task twice is idempotent — no state corruption, no manual recovery. This is exactly the kind of design where distributed systems concepts pay off in practice: simple guarantees at the infrastructure layer eliminate entire categories of coordination bugs at the application layer.

## Growth

The biggest thing I take away from this project is intuition about overhead. Before, I would have assumed that saving compute is always a net win. Now I see that network latency, compression CPU cost, and blob serialization can outweigh the compute you aimed to save. That mental model will shape how I think about caching, replication, and state sharing in any distributed system I work on.

## The High

The most satisfying outcome was proving my original hypothesis correct, just with more nuance than I expected. I came in thinking "caching saves prefill compute, therefore it's faster." I came out knowing: yes, I successfully eliminated 54% of prefill tokens, but I paid real costs in network I/O, compression, and serialization. Watching those costs shrink in relative terms as worker count grew — and seeing cached finally pull ahead of naive at five workers — made the whole project feel so much more satisfying.

## On AI-Assisted Development

This class also got me aquainted with coding tools like claude code. Prior to this class, claude code had not been released for long, and I had never used it. As this class's projects grew, I was leveraging claude code much more. With this project especially, claude code really helped me flush out my vision. I learned how to plan my project architectures with critical interfaces for future project growth and how to communicate new changes to claude code. Interfaces helped prevent the AI coder from trying to impliment too many things at once, and reduced complicated refactors. Providing detailed instructions is paramount, and don't be afraid to define critical components in rough pseudo code. Also creating an entire project plan first and then iterating through each component helps me keep track of what is being implimented and ensure each part is made correctly before moving onto the next one. 

That said, AI coding is not a silver bullet. The KV cache bug above is proof. For a simple project you can catch mistakes quickly. For a research project where a bug might not surface until after hours of benchmark runs, the cost of a single missed review is a full re-run. I will more carefully review AI-generated code on future research projects.
