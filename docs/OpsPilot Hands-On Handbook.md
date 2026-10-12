# OpsPilot: Hands-On Handbook

Oct 12, 2026 · @Krishna

## What you are building

OpsPilot is a cloud incident triage copilot: when a CloudWatch alarm fires, an AI workflow investigates it, proposes a fix from your runbooks, and waits for a human to approve before anything changes.

**The flow in one paragraph**

A CloudWatch alarm raises an event. EventBridge hands it to a Lambda that records an incident in DynamoDB and puts a job on an SQS queue. A worker Lambda runs a LangGraph workflow: it redacts sensitive text, reads the recent logs, finds the matching runbook, asks a language model for a structured diagnosis, and applies a policy check. Safe steps run automatically. Anything that changes infrastructure stops at an approval gate until you call an API endpoint. Every decision is logged, and an email goes out through SNS.

**Why this is portfolio-grade**

- **Real AWS breadth:** API Gateway (HTTP API with a Lambda authorizer), Lambda, SQS with a dead-letter queue, DynamoDB with TTL and a secondary index, EventBridge, SNS, CloudWatch alarms and custom metrics, S3, SSM Parameter Store, IAM least privilege and Bedrock.
- **Infrastructure as code:** the whole stack is one AWS SAM template, deployed and deleted with a single command each.
- **Agentic AI done responsibly:** LangGraph state machine, structured output validated by Pydantic, an allow-list of actions, a human approval gate and redaction before any text reaches a model.
- **Production habits:** idempotent queue processing, deduplication of repeat alerts, structured JSON logs, custom metrics, tests that run without AWS, a CI workflow and written design decisions.
- **A switchable model layer:** one config value picks Amazon Bedrock, a free local Ollama model, or a deterministic mock used in tests.

**What it costs**

Every service used sits inside the AWS free tier at this volume. The one exception is Amazon Bedrock, which bills per token with no free allowance. With the caps in this project it costs cents, and the local Ollama and mock providers cost nothing. The cost table in the prerequisites section shows each service.

**What you will practise**

1. Run the full workflow locally with no AWS account.
2. Set up a safe AWS account with budgets and a least-privilege CLI user.
3. Deploy the stack with SAM and read the CloudFormation output.
4. Fire a test alarm and watch an incident move from received to resolved.
5. Tear everything down and confirm nothing is still billing.

## 1. Prerequisites and free-tier guardrails

Everything here is free except one optional item: Amazon Bedrock tokens. The default deployment uses the offline mock model, so you pay nothing unless you choose otherwise.

**Install on your computer (Windows PowerShell commands shown)**

| Tool | Why | Check it |
| --- | --- | --- |
| Python 3.12 | Run code and tests | `python --version` |
| Git | Version control, GitHub | `git --version` |
| AWS CLI v2 | Talk to AWS | `aws --version` |
| AWS SAM CLI | Build and deploy the stack | `sam --version` |
| Docker Desktop | SAM builds Linux packages in a container | `docker --version` |
| (optional) Ollama | Free local LLM for experiments | `ollama --version` |

Start Docker Desktop before `sam build`. Build errors that mention `docker` almost always mean it is not running.

**Free-tier map**

| Service | Free allowance | Our usage |
| --- | --- | --- |
| Lambda | 1M requests, 400k GB-seconds per month | A few dozen invocations |
| API Gateway HTTP API | 1M calls (first 12 months) | Tens of calls |
| DynamoDB | 25 RCU/WCU always free, provisioned only | Table 5/5 plus index 2/2 |
| SQS | 1M requests per month | Tens |
| SNS | 1,000 email notifications per month | A handful |
| CloudWatch | 10 alarms, 10 custom metrics, 5 GB logs | 2 alarms, 7 metrics |
| S3 | 5 GB (first 12 months) | Kilobytes |
| SSM Parameter Store | Standard parameters free | 1 parameter |
| Bedrock | **No free tier** | Off by default |

**Guardrails to set first**

1. Create a zero-spend budget in Billing, Budgets, so AWS emails you on the first charge.
2. Keep `LlmProvider=mock` until Phase 4. If you switch to Bedrock, one triage uses roughly a thousand tokens, which costs a fraction of a cent with a small model. Still, check Bedrock pricing for your region before you enable it.
3. Always finish the session with the teardown section. Idle resources here cost nothing, but habit matters.
4. Never paste access keys, the API token or your email into files that go to GitHub.

## 2. Architecture

&#91;embedded content: OpsPilot architecture · intake, worker and approval lanes\]

Top row is intake: an alarm becomes a de-duplicated incident and a queued job. The middle row holds the human lane (engineer to API) and the worker that runs the LangGraph steps. The bottom row is what the worker talks to.

**Key design decisions**

- **Rules decide, the model advises.** The action list is fixed in code and the target comes from the incident record, never from the model.
- **Human approval for any change.** Raising Lambda memory waits for an approve call and is a dry run unless mutation is switched on.
- **Safe to retry.** Status changes are conditional writes, repeat alerts share a dedup marker, and failed jobs end in the dead-letter queue with an alarm.
- **Untrusted logs.** Secrets are redacted and log text is treated as data before the model sees it.
- **Swappable model.** One setting selects mock, Ollama or Bedrock, and a rule-based fallback covers model failure.

## 3. Phase 1: run it on your laptop (no AWS account)

Goal: see the full triage workflow work before touching AWS. About 15 minutes.

1. Unzip the project and open PowerShell in its folder, for example `D:\Hands_On_Projects\AWS\opspilot`.
2. Create and activate a virtual environment:

   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process RemoteSigned` and retry.
3. Install dependencies: `pip install -r src/requirements.txt -r requirements-dev.txt`
4. Point Python at the source folder: `$env:PYTHONPATH="src"`
5. Run the unit tests: `python -m unittest discover -s tests -t .`. Expect `OK` and 64 tests.
6. Triage an out-of-memory incident and approve it:

   ```
   python -m opspilot.cli triage samples/incidents/lambda_oom.json --approve
   ```
7. Read the trace. You should see, in order: evidence kept, runbooks matched (`RB-LAMBDA-OOM` first), diagnosis with confidence 0.82, `approval required`, `waiting for human approval`, then `RESOLVED: Raised ... from 128 MB to 256 MB`. The memory change happens on a stub, not on AWS.
8. Run the other two samples and compare:
   - `lambda_timeout.json` ends with a drafted ticket, because raising a timeout hides the cause.
   - `lambda_exception.json` also drafts a ticket and reports redactions, because its log contains fake secrets and an injection line.
9. Try a rejection: `... lambda_oom.json --reject`. Status becomes `REJECTED` and nothing changes.

**What to notice**

- The log lines containing secrets were redacted before any model saw them.
- The sample notifier log contains the line telling the assistant to ignore its instructions. It is treated as data and the action is unchanged.
- Without `--approve`/`--reject`, the CLI asks interactively when run in a terminal.

**Optional: a real local model.** Install Ollama, run `ollama pull llama3.2`, then `pip install langchain-ollama` and add `--provider ollama`. Small models may return invalid JSON. When that happens the workflow logs it and falls back to the rule-based diagnosis, which is the intended behaviour.

## 4. Phase 2: AWS account safety setup

Goal: a safe account and a CLI that deploys as a limited user, never as root.

1. Sign in as the root user once, turn on MFA (IAM, Security credentials), then stop using root.
2. Billing, Budgets: confirm the zero-spend budget from section 1 exists and emails you.
3. IAM, Users, create `opspilot-deployer`. Attach `AdministratorAccess` for this personal sandbox. For a shared account you would instead write a scoped policy; mention this trade-off in interviews.
4. Create an access key for the CLI use case, then run `aws configure --profile opspilot` and enter the key, secret, region `ap-southeast-2` and output `json`. Do not store the key anywhere else.
5. Use the profile for the session: `$env:AWS_PROFILE="opspilot"`
6. Check identity: `aws sts get-caller-identity`. The ARN must end in `user/opspilot-deployer`, not root.
7. Check the region: `aws configure get region --profile opspilot` prints `ap-southeast-2`.

**Common problems**

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Unable to locate credentials` | Profile not set in this PowerShell window | Re-run the `AWS_PROFILE` line |
| `InvalidClientTokenId` | Key typed wrongly or deactivated | Create a new key and re-run `aws configure` |
| Wrong region in the console | Console region selector differs | Switch the console to Sydney (ap-southeast-2) |

## 5. Phase 3: build and deploy with SAM

Goal: 18 AWS resources created from one template. About 10 minutes, mostly the first container build.

1. Validate the template: `sam validate --lint`. Expect `is a valid SAM Template`. Fix anything it reports before going on.
2. Make sure Docker Desktop is running, then build: `sam build`. The first run downloads a Linux build image, so allow a few minutes. Expect `Build Succeeded`.
3. Deploy, passing your email on the command line so it stays out of git:

   ```
   sam deploy --parameter-overrides NotifyEmail=you@example.com
   ```
4. SAM shows a change set. Review it and answer `y`. You should see roles, functions, queues, a table, a bucket, an alarm rule and the HTTP API being created.
5. When it finishes, the Outputs block lists `ApiUrl`, `DemoFunctionName`, `DemoAlarmName`, `TableName`, `TicketBucketName`, `TriageQueueUrl` and `DeadLetterQueueUrl`. Copy `ApiUrl`.
6. Confirm in the console: CloudFormation shows `opspilot-dev` as `CREATE_COMPLETE`, and Lambda lists five `opspilot-...` functions.
7. Check the cost footprint: DynamoDB, table `opspilot-dev-...`, Capacity tab shows provisioned 5 read and 5 write.

**If the build or deploy fails**

| Message | Meaning | Fix |
| --- | --- | --- |
| `Cannot connect to the Docker daemon` | Docker is not running | Start Docker Desktop and retry |
| `Unzipped size must be smaller than 262144000 bytes` | Dependencies too large for one function package | Move `src/requirements.txt` packages into a Lambda layer, or deploy the worker as a container image. This is the one thing the template author could not test without AWS, so check it on your first build |
| `ROLLBACK_COMPLETE` | A resource failed to create | Read the first `CREATE_FAILED` event in CloudFormation, fix it, run `sam delete`, then redeploy |
| `ParameterNotFound in the authorizer logs, or a 500 from the API` | API token not created yet | Do section 6, step 1 |

AWS emails the address you gave with a subscription confirmation link. You confirm it in the next phase.

## 6. Phase 4: secrets, email and the LLM switch

Goal: protect the API, receive notifications, and know how to change the model provider.

1. Generate a long random token and store it as an encrypted SSM parameter:

   ```
   $token = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
   aws ssm put-parameter --name /opspilot/dev/api-token --type SecureString --value $token
   $env:OPSPILOT_TOKEN = $token
   ```

   The token exists only in this PowerShell window and in SSM. Do not echo it into files.
2. Open the confirmation email from AWS Notifications and click Confirm subscription. Without this, no email arrives.
3. Set the API address for later commands: `$env:API="<ApiUrl from the deploy outputs>"` with no trailing slash.
4. Smoke test the protection. Without a token the call must fail:

   ```
   curl.exe -s -o NUL -w "%{http_code}" "$env:API/incidents"
   ```

   Expect `401`. With the token it must succeed:

   ```
   curl.exe -s -H "Authorization: Bearer $env:OPSPILOT_TOKEN" "$env:API/incidents"
   ```

   Expect an empty list in JSON.

**Switching the LLM provider**

The default is `mock`. Parameters you do not repeat on a later deploy return to their defaults, so pass all you want each time.

| Goal | Deploy command |
| --- | --- |
| Free, offline model (default) | `sam deploy --parameter-overrides NotifyEmail=you@example.com` |
| Real model on Bedrock | add `LlmProvider=bedrock` |
| Allow real memory changes after approval | add `AllowMutatingActions=true` |

Before using Bedrock: in the Bedrock console (Sydney region), open Model access and request access to Amazon Nova Lite. The template permits only Nova models and the inference profile named in `BedrockModelId`. If the model ID is rejected in your account, list valid ones with `aws bedrock list-inference-profiles --region ap-southeast-2` and pass the correct `BedrockModelId`. Bedrock is billed, so switch back to `mock` when you finish.

**Why mutation is a separate switch.** With `AllowMutatingActions=false`, an approved memory change is only reported as a dry run. Turning it on is a deliberate second decision.

## 7. Phase 5: test the whole flow, alarm to approved action

Goal: break something on purpose, watch OpsPilot react, approve the fix. Allow 10 minutes. Use the same PowerShell window as Phase 4 so `$env:API` and `$env:OPSPILOT_TOKEN` are set.

**Test A: create an incident by hand (fastest check)**

1. Set a header variable: `$h = @{ Authorization = "Bearer $env:OPSPILOT_TOKEN" }`
2. Build the request body for the demo function (use the `DemoFunctionName` output):

   ```
   $body = '{"title":"Manual test","resource":{"function_name":"<DemoFunctionName>"}}'
   ```
3. Send it: `Invoke-RestMethod -Method Post -Uri "$env:API/incidents" -Headers $h -ContentType "application/json" -Body $body`. Expect `duplicate: False` and status `RECEIVED`.
4. Wait 20 seconds and read it: `Invoke-RestMethod -Uri "$env:API/incidents" -Headers $h`. Status should have moved on from RECEIVED. A function with no failure logs yet usually ends as RESOLVED with a notify-only result, because the evidence is inconclusive and the system refuses to act without evidence. Test B shows the approval path.
5. Send the same body again. Expect `duplicate: True` and `occurrences` rising. That is the dedup marker working.

**Test B: the real alarm path**

1. Make the demo function run out of memory. Save the payload to a file to avoid PowerShell quoting problems:

   ```
   Set-Content -Path oom.json -Value '{"mode":"oom"}' -NoNewline
   aws lambda invoke --function-name <DemoFunctionName> --cli-binary-format raw-in-base64-out --payload file://oom.json out.json
   ```

   The command returns quickly with a `FunctionError`. That is the intended failure.
2. Wait one to three minutes. CloudWatch Alarms, `opspilot-demo-workload-errors-dev`, moves to `In alarm`.
3. EventBridge triggers the ingest function, which queues a job. Check the worker logs:

   ```
   aws logs tail /aws/lambda/<TriageWorkerFunctionName> --since 10m
   ```

   Find the function name in the Lambda console. Look for JSON lines with an `incident_id`, then `diagnosis` and `policy`.
4. You should receive an email titled `[OpsPilot][P2] Approval needed: INC-...` showing the diagnosis (out of memory), the proposed action and the approve and reject endpoints.
5. Approve it: `Invoke-RestMethod -Method Post -Uri "$env:API/incidents/<INC-id>/approve" -Headers $h -ContentType "application/json" -Body '{"by":"your-name"}'`. Expect status `APPROVED`, then within seconds `RESOLVED`.
6. Read the result: `Invoke-RestMethod -Uri "$env:API/incidents/<INC-id>" -Headers $h`. With the default settings the result starts with `DRY-RUN: would raise ... from 128 MB to 256 MB`. The event timeline lists every transition.
7. To see a real change, redeploy with `AllowMutatingActions=true`, repeat Test B with a fresh failure (wait out the 30-minute dedup window or use a different message), approve, and confirm in the console that the demo function now shows 256 MB.

**Test C: safety behaviours worth demonstrating**

| Check | How | Expected |
| --- | --- | --- |
| Double approval | Run the approve call twice | Second returns 409, `not AWAITING_APPROVAL` |
| Wrong token | Use `Bearer nope` | 403 from the authorizer |
| Missing token | No header | 401 |
| Reject path | Reject instead of approve | Status `REJECTED`, nothing changes |
| Dead-letter queue | Not needed to test; the alarm `opspilot-dev-dead-letter-queue-not-empty` watches it | Stays in `OK` |

## 8. Observability and troubleshooting

**Where to look.** Every Lambda writes JSON logs to `/aws/lambda/<function name>` with an `incident_id` field, so one search follows an incident across functions. In CloudWatch Logs Insights, select the log groups and run `fields @timestamp, message, incident_id | filter incident_id = "INC-..." | sort @timestamp`. Custom metrics (IncidentCreated, IncidentDeduplicated, ApprovalGranted and others) appear in the CloudWatch namespace the code emits, under All metrics.

| Symptom | Likely cause | How to confirm and fix |
| --- | --- | --- |
| API returns 401 | No `Authorization` header | Add `Bearer <token>` |
| API returns 403 | Token differs from the SSM value | Re-read it with `aws ssm get-parameter --with-decryption`, reset `$env:OPSPILOT_TOKEN` |
| API returns 500 | Runtime error in the API function | Tail `/aws/lambda/<api function>` and read the stack trace |
| Alarm fires, no incident | Alarm name does not start with `opspilot-demo-` | The ingest function ignores unmanaged alarms by design. Rename or check its logs |
| Incident stays `RECEIVED` | Worker not triggered or failing | Check the queue's Monitoring tab and worker logs. Re-sending the same create call re-queues it |
| Incident `AWAITING_APPROVAL` but no email | SNS subscription not confirmed | Click the link in the AWS email, or check spam |
| Message in the dead-letter queue | Worker failed three times | Read worker logs for the error, fix, then redrive from the SQS console. The DLQ alarm will have fired |
| Diagnosis says evidence inconclusive | No error lines in the logs yet, or log delivery is still in progress | Wait a minute and re-trigger |
| `AccessDeniedException` on Bedrock | Model access not enabled, or wrong model ID | Enable access in the Bedrock console, check `BedrockModelId` |
| Result says `DRY-RUN` | Mutation is off | Intended. Redeploy with `AllowMutatingActions=true` if you want a real change |
| Approve returns 409 | Already approved, rejected or not yet triaged | Read the incident status first |

**Two real issues from the first deployment, both fixed in the current template**

- **API returns `Internal Server Error` and the API log group is empty.** The authorizer Lambda had no permission for API Gateway to invoke it. Check with `aws lambda get-policy --function-name opspilot-dev-authorizer`; the template now includes `AuthorizerInvokePermission`.
- **Authorizer invoke ends with `Task timed out`.** At 128 MB the authorizer got too little CPU to start boto3 and read SSM in time. It now runs at 512 MB with a 10 s timeout and no longer imports the workflow code. Decode `--log-type Tail` output to see `Init Duration` and `Duration` when diagnosing this kind of problem.

**Habit to build.** Reproduce, find the log line, state the cause in one sentence, then fix. Interviewers like this order.

## 9. Teardown and verifying nothing still bills

1. If you switched to Bedrock, you pay only per call, so there is nothing to shut down, but switch back to `mock` by redeploying if you keep the stack.
2. Empty the ticket bucket (a non-empty bucket blocks stack deletion): `aws s3 rm s3://<TicketBucketName> --recursive`
3. Delete the stack: `sam delete`. Answer `y` to the prompts, including removal of the artifact bucket SAM created.
4. Delete the token: `aws ssm delete-parameter --name /opspilot/dev/api-token`
5. Delete leftover log groups if any remain: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/opspilot`, then `aws logs delete-log-group --log-group-name <name>` for each.
6. In CloudFormation, confirm `opspilot-dev` no longer exists. Confirm the SQS, DynamoDB and Lambda consoles show no `opspilot` items.
7. In IAM, deactivate or delete the access key if you will not use the account for a while.
8. Next morning, check Billing, Bills and Cost Explorer for charges. The zero-spend budget should stay quiet.

After teardown the repository and README still show the project, which is the point: the code is the portfolio, not the running stack.

## 11. Publishing to GitHub and presenting to recruiters

1. In the project folder, confirm `.gitignore` exists and covers `.venv/`, `.aws-sam/`, `__pycache__/`, `*.zip`, `out.json`, `oom.json`.
2. Scan for secrets before the first commit:

   ```
   git init
   git add .
   git status
   Select-String -Path (git ls-files) -Pattern "AKIA","aws_secret","Bearer ","@gmail" -SimpleMatch
   ```

   Any hit must be reviewed. Sample files contain fake secrets on purpose, so those hits are expected.
3. Create an empty repository on GitHub named `opspilot-incident-triage`, public, no README (you already have one).
4. Commit and push:

   ```
   git commit -m "Initial commit: OpsPilot incident triage on AWS"
   git branch -M main
   git remote add origin https://github.com/<your-user>/opspilot-incident-triage.git
   git push -u origin main
   ```
5. On the repository page add a one-line description and topics: `aws`, `serverless`, `aws-sam`, `langgraph`, `langchain`, `bedrock`, `python`, `incident-response`.
6. Check that the Actions tab shows the CI workflow passing. Fix failures before sharing the link.
7. Optional: add a screenshot of the offline CLI output and of the CloudFormation stack to the README.

**Description to paste:** AI-assisted cloud incident triage on AWS serverless: alarms to diagnosis to human-approved fix, with LangGraph, SQS idempotency, redaction and a switchable LLM.

**How to present it**

- Lead with the problem: on-call engineers lose time reading logs for known failure patterns.
- Then the stance: rules decide, the model advises, a human approves.
- Point to proof: 64 tests, CI, least-privilege IAM, dry-run default, free-tier design.
- Be upfront about limits listed in the README. Honesty reads as seniority.
- On your CV: one line naming the services (Lambda, API Gateway, SQS, DynamoDB, EventBridge, SNS, CloudWatch, SSM, SAM) and the outcome.

## 12. Concepts and interview question bank

| Question | Short answer |
| --- | --- |
| Why SQS between the API and the worker? | Triage is slow and alarms burst. The queue gives fast responses, retries, back-pressure and a dead-letter queue. |
| SQS delivers at least once. How do you stay safe? | Every state change is a conditional write on the current status, so a repeat does nothing. Dedup markers collapse repeated alerts. |
| Why DynamoDB provisioned, not on-demand? | Only provisioned capacity (25 RCU/WCU) is always free. 5/5 is plenty here. |
| Why check expiry in the condition if TTL exists? | TTL deletion can lag by hours, so an expired marker could still block a new incident. |
| Why an HTTP API and a Lambda authorizer? | HTTP APIs are cheaper and inside the free allowance. A simple-response authorizer compares a token held in SSM. |
| Where is the AI used, and where is it not? | It writes a diagnosis and suggests an action. Targets, limits and approval come from code, never from the model. |
| How do you handle prompt injection? | Logs are redacted, wrapped as untrusted data, and the system prompt says never to obey them. The model cannot choose the target, memory changes are clamped, low confidence becomes a ticket. |
| What if the LLM is down or returns bad JSON? | Pydantic validation fails and a rule-based diagnosis takes over, so triage still completes. |
| Why LangGraph? | The flow branches (approval or not) and runs in two modes. Explicit nodes are testable. Pausing is done by saving status, because Lambda is stateless. |
| Why TF-IDF, not a vector database? | Four short runbooks. No cost, no network call. The interface allows embeddings later. |
| How is least privilege applied? | One role per function, resource-scoped. Only the worker can change Lambda config, and only on functions with the managed prefix. |
| How would you scale or harden it? | Cognito or IAM auth, multi-tenant keys, X-Ray tracing, a dashboard, an LLM evaluation set, a layer or container image for dependencies. |
| What did you not verify? | Be honest: model quality was not benchmarked, and it runs single-region, single-tenant. |

**Terms to know.** EMF: CloudWatch Embedded Metric Format, metrics emitted as structured log lines. DLQ: dead-letter queue for messages that keep failing. GSI: global secondary index, an alternate query path on a DynamoDB table. SAM: shorthand CloudFormation for serverless. Partial batch response: the worker reports only failed messages so successes are not retried.
