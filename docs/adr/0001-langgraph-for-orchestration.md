# 1. Use LangGraph for orchestration
Status: accepted

## Context
Triage is a multi-step flow with branches (approval needed or not) and a pause for a human.
## Decision
Model it as a LangGraph state machine with explicit nodes and conditional edges. Pausing is done by persisting status in DynamoDB and starting a second run after approval, not by in-memory checkpoints, because Lambda is stateless.
## Consequences
Clear, testable steps and a visible graph. Adds a dependency and package size. A plain function chain would also work; the graph earns its place through the branching and the two run modes.
