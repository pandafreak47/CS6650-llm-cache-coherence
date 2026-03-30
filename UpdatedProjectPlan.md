# Updated project plan

## New Architecture

python test script -> AWS SQS -> AI Agent Workers <-> Git Server
                                                  <-> KV Cache

### AWS SQS
This queue will hold all the tasks for the agents to work on. The messages will have the following format:
```
{ 
    git repo header:str,
    list of context files: [str],
    target file: str,
    task prompt: str
}
```

The queue will have message groups based on the target file. Each message group may only have one active message at a time. This should work as an implicit file lock, so multiple agents do not work on the same file concurrently. We assume all code changes are fully backward compatible, so there is no need for further file locking in this project.

the list of context and the target file are NOT the full files, they should be a file path or something similar, which can be used with the git cli to view and commit to files. 

### Python Test Script
This script will send a seeded number of tasks to fill the SQS queue. If possible, it should completely empty the queue upon start and then time how long it takes for the queue to empty. Before starting the test, the script needs to make a new branch with a random id from the main of the given repo, that way all tests start from the same base repo. 

### Git Server
This will be the repo with all the files, hosted on github. We will use a token defined in a terraform env variable to enable all ai agents to look at files and make commits.

### AI Agent Workers
Worker Logic:
```
    Loop:
        Grab Msg from queue

        Build Msg

        Call LLM

        Parse rewritten Code

        Commit & Push to Git

        Send Ack to SQS 

        Repeat
```

Worker helper endpoints:
```
    /health # states if healthy
    /status # are you waiting for a message (on standby)? or are you processing a message? (merge with health if wanted)
    /metrics # return num tokens input, num tokens output, total LLM latency, total num oof requests
    /metriccs/clear # clears all metrics
```


#### Naive Build Msg Implimentation:
```
    Get context Files from Git

    Merge all Context Files with standardized file header and ender notations for LLM interpretation

    Append target file with standardized notation

    Append task

    Append Seed for writing updated file

    return empty_KV, full_context_string
```

#### Cached Build Msg Implimentation:
```
    evaluate context files as a set & check KV cache for hit with most context files

    evaluate remaining context files & stratigecally order (order by size of file by pinging git cli for now)

    for remaining context files:
        get file from git server
        add standardized file header and ender notation
        Call LLM with previous KV context + added context str
        save new KV context to cache
        repeat

    prompt_string:
        get target file from git server
        add standard file header and ender notation
        add task & seed LLM response

    return last_KV_context, prompt_string
```

#### Call LLM should be an object with the following interface:
```
class InterfaceLLM():
    """
    Common interface for LLM backends.

    The kv_state parameter carries prefix-cache data between calls.
    Some implementations may ignore it.
    llama.cpp implementations will use it to avoid reprocessing shared context.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        kv_state: KVState,
        max_tokens: int = 1024,
        system: Optional[str] = None, # maybe use this for task if llama.cpp permits?
    ) -> kv_state:KVState, output:string # note the KVState returned should NOT include the output

    """
    The LLM object shall keep track of all these metrics. if reset = true, set all metrics to zero
    """
    def metrics(
        self,
        reset:bool = false
    ) -> total_input_tokens: int,
        total_output_tokens: int,
        total_latency_ms: float
```
##### Implimentations:
Make sure the constructors allow defining an end sequence token. This may be aligned with the standardized header and ender.
###### DummyLLM
This implimentation will just track the metrics and return the last file given as is. The prompt will always end with the target file and the task, so make sure the header and ender notation are standardized, and include the name of the file in them for redundancy. The total lanency and output tokens shall be zero. and just return the give KVState

###### AnthropicAPI
This implimentaion will use the anthropic API. have it ignore the KVState and any metrics not readily available. and just return the given KVState.

###### llama.cpp
This implimentation is the end goal and should have full support for metrics and KVState usage

## Experiments
The end goal is to evaluate metrics over the following matrix:

Implimentation \ Number of workers | 1 | 3 | 5+
Naive (no cacheing) | | |
Centralized KV Cache | | |
"Smart" caching order | | |
Distributed cache? | | |

### "Smart" caching
Currently we build the KV State Context by puting the largest file on the top of the heirarchy, we will later replace that with more advanced logic, considering path structure, git commits, and more

### Distributed cache
We may test using a distributed KV cache