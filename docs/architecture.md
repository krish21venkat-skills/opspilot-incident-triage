# Architecture

## Components

| Component | Responsibility |
|---|---|
| Authorizer Lambda | Compares the bearer token with an SSM SecureString (cached 5 min, constant-time compare). Simple response. |
| API Lambda | Create, list, read, approve, reject. Approve flips status with a conditional write, then enqueues an execute message. |
| Alarm ingest Lambda | Maps an EventBridge alarm event to an incident. Only alarms with the managed name prefix are accepted. |
| Triage worker Lambda | SQS consumer (batch size 1). Runs the LangGraph workflow in `analyze` or `execute` mode. |
| DynamoDB | `INC#<id>` incident items and `DEDUP#<fingerprint>` markers. GSI `recent-index` on (entity, created_at). |
| Demo workload Lambda | Deliberately fails (oom, slow, error) so the system has real alarms to react to. |

## Workflow (LangGraph)

Analyze mode: `start_triage → prepare_context → gather_evidence → retrieve_runbooks → diagnose → policy_gate`,
then either `request_approval` (stop, status `AWAITING_APPROVAL`) or straight to execution.

Execute mode (after approval): `load_for_execution → begin_execution → run_action → notify_outcome`.
The approved action is re-read from the database and re-checked by policy, so a stale or tampered message cannot widen it.

## Data flow guarantees

- SQS is at-least-once. Each node is safe to run twice because status changes use `ConditionExpression` on the current status.
- The worker returns partial batch failures. After 3 failed receives a message goes to the DLQ, which has its own alarm.
- Visibility timeout (540 s) is six times the function timeout (90 s).
- If a crash happens after `EXECUTING` is set, the claim is released so a retry can continue instead of stalling.

## Security model

- API: bearer token, throttled, no public data beyond what the token grants.
- IAM: separate role per function, resource-scoped. Only the worker holds `lambda:UpdateFunctionConfiguration`, and only on functions with the managed name prefix.
- Secrets: token in SSM SecureString. Nothing secret in the repo or the template.
- LLM: input is redacted (AWS keys, bearer tokens, JWTs, password-style values, emails, IPs). Output is validated with Pydantic.
- Mutation is off by default and needs a human approval even when on.

## Cost and free-tier notes

Everything except Bedrock is inside always-free or 12-month free allowances at demo volume. DynamoDB uses provisioned capacity
(5/5 table, 2/2 index) because on-demand is not free-tier. Seven custom metrics are emitted of the ten free.

## Scaling and next steps

Cognito or IAM auth, per-team tenancy, X-Ray, a dashboard, evaluation set for the LLM, and moving dependencies to a Lambda layer or container image if the package grows.
