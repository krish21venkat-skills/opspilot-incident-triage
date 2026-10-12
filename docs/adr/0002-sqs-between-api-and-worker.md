# 2. Put SQS between intake and triage
Status: accepted

## Context
Triage calls logs and an LLM and can take tens of seconds. Alarms can burst or flap.
## Decision
API and ingest Lambdas only record the incident and enqueue. A worker consumes with batch size 1, partial batch responses, maxReceiveCount 3 and a DLQ with an alarm.
## Consequences
Fast API responses, retries and back-pressure for free. Delivery is at-least-once, so every state change is a conditional write and dedup markers collapse repeats.
