---
id: RB-LAMBDA-OOM
title: Lambda function runs out of memory
keywords: out of memory, signal: killed, Runtime.OutOfMemory, Max Memory Used, memory size, killed
---

# Lambda function runs out of memory

## Symptoms
- Log line `Runtime exited with error: signal: killed` or `Runtime.OutOfMemory`.
- The REPORT line shows `Max Memory Used` equal to `Memory Size`.
- Invocations end with status `error` and the Errors metric rises.

## Likely causes
1. The memory setting is too small for the payload being processed.
2. A recent change loads a whole file or result set into memory.
3. A memory leak across warm invocations (rare; usually a global list that keeps growing).

## Safe remediation
1. Raise the function memory by one step, to at most double the current size.
2. Watch `Max Memory Used` over the next ten invocations.
3. If usage still hits the limit, open a ticket for a code fix instead of raising memory again.

## Do not
- Raise memory above 1024 MB without a human review of cost and cause.
- Change any function that is not a managed workload.
