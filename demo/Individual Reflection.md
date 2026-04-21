<!-- each team member should reflect on what they personally learned in this project.  
This is from your own individual perspective---maybe about the most challenging bug you fixed, or experiment you ran. 
It is important to share what went wrong, why it went wrong, and how you would handle it next time!  Where possible, reflect back on some of the specific concepts of the course, and refer back to how the concepts apply to the final project you did.
The big idea here is that you show your growth from Day 1 to Final project, and capture some of the highs and lows for you to remind yourself (and your future employer?!) one day :) -->

# Shared Prefill Over Distributed AI Agents Project – Individual Reflection
Kenton Romero

Starting the class, I had already been familiar with many of the distributed systems topics, but I had no formal training. I think this class helped me get a well rounded background of the field. It also solidified my interest in the field. This project seemed like the first project where I was actually unsure of my hypothesis and found nearly all my results suprising. For one, I though caching the key value logits of my AI agents would always be better than just recomputing everything, but the overhead of storing and loading all those states (even without compression) did not make up for the saved prefil time until you increased the number of AI agents that can take advantage of the cached states concurrently. Another was how drastically the ordering of context in my cache would change the performance of my caching. 





For example, a mistake I made was assuming my test runner had a default seed, but after realizing all my input token counts for the 1 worker case being different, I knew something was up. I checked and realized every benchmark had a unique set of tasks, making direct comparisons dubious. Luckily this also prompted me to properly seed my LLM responses so the real 1 worker tests are deterministic and can be compared directly.


This class also got me aquainted with coding tools like claude code. Prior to this class, claude code had not been released for long, and I had never used it. As this class's projects grew, I was leveraging claude code much more. With this project especially, claude code really helped me flush out my vision. I learned how to plan my project architectures with critical interfaces for future project growth and how to communicate new changes to claude code. Interfaces helped prevent the AI coder from trying to impliment too many things at once, and reduced complicated refactors. Providing detailed instructions is paramount, and don't be afraid to define critical components in rough pseudo code. Also creating an entire project plan first and then iterating through each component helps me keep track of what is being implimented and ensure each part is made correctly before moving onto the next one. 

However, AI coding is not a silver bullet. I learned the hard way that I need to always, 100% of the time, carefully review the code these AI models generate. For simple projects, its easy to relax and just plug bugs when you notice them, but for research projects requiring benchmarks, it may take hours of benchmarks before you realize soemthing was implimented incorrectly. Even when carefully reviewing 50-80% of all AI generated code, all it takes is one missed bug to need to rerun over all your benchmarks. 

One such case was when the KV Cache was incorrectly implimented for the llama model. All the documentation and AI conversation suggested the actual model's key-value logits would be cached, but the prior implimentations of the Anthropic and Mock LLM backends just used the context text. That still ran for the llama.cpp implimentation, but it was not avoiding any prefill. If anything, it accumulated a ton of prefills on the model, but those key value logits were never used, resulting in horrendous results.


