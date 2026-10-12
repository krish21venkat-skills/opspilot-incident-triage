---
id: RB-LAMBDA-TIMEOUT
title: Lambda function times out
keywords: task timed out, timeout, timed out after, duration, slow, hanging, downstream latency
---

# Lambda function times out

## Symptoms
- Log line `Task timed out after N seconds`.
- REPORT line `Duration` equals the configured timeout.

## Likely causes
1. A downstream service or database is slow or unreachable.
2. The work genuinely takes longer than the timeout.
3. A missing network path (for example no route to the internet from a VPC function).

## Safe remediation
1. Check the downstream dependency health first.
2. Do not simply raise the timeout; it hides the cause and raises cost.
3. Open a ticket that records the duration pattern and the dependency checked.
