# OpsPilot: Step-by-Step Practice Guide

Follow this next to your **OpsPilot Hands-On Handbook**. The handbook has the commands. This guide adds plain click-by-click steps, what you should see after each step, and a few fixes for your AWS account type. Allow 2 to 3 hours the first time (the Docker build is the slow part).

I have only seen the handbook, not the project zip. Names such as `BedrockModelId`, `NotifyEmail` and the stack outputs come from the handbook. If the code differs, trust the code and tell me.

## Read this first: 6 things about your account

Your console is the AWS "new experience" Free Tier (the home page with Manage billing, Manage team and Manage projects). I checked the AWS documentation for this account type. It changes a few handbook steps.

1. **Your Region is fixed to Sydney (`ap-southeast-2`).** This matches the handbook. AWS only lets you create resources in your project's Region, and cross-Region features are not supported. Never switch Region. If a page ever says "not found", look at the address bar: it must contain `region=ap-southeast-2`.
2. **Bedrock has no "Model access" request step any more.** Serverless models such as Amazon Nova Lite are switched on automatically. Skip the handbook line about requesting access.
3. **Use the plain model ID `amazon.nova-lite-v1:0`**, not a name starting with `apac.` or `us.`. Those are cross-Region profiles, which your account type does not support. Phase 4 shows how to check and pass it.
4. **You can create an IAM user for the CLI, but not one with a console password.** The handbook's `opspilot-deployer` works if you do **not** tick "Provide user access to the AWS Management Console". Phase 2 has the exact clicks.
5. **If you create a spend limit, AWS can block you as you approach it.** Near the limit it blocks new CloudFormation stacks, queues, tables, topics and APIs, and also Lambda and Bedrock calls. For this practice, rely on the budget email alert, or set a spend limit well above what you expect to use.
6. **The plan is credit based, not the old 12-month free tier.** The handbook's free-tier table is a good guide to what is cheap, but check your balance and end date on the Free Tier page (search `Free Tier`). Use only made-up data. This account type does not offer the HIPAA agreement.

Every service OpsPilot uses (Lambda, API Gateway, DynamoDB, SQS, SNS, EventBridge, CloudWatch, S3, Systems Manager, CloudFormation, IAM, Bedrock) is on your account's Free Tier list.

## The project map

```
 CloudWatch alarm (demo function has errors)
    |
    v
 EventBridge rule --> Lambda "ingest": makes the incident in DynamoDB, ignores repeats, puts a job on SQS
                                   |
                                   v
                    SQS queue  (3 failures --> dead-letter queue --> alarm)
                                   |
                                   v
                    Lambda "triage worker" runs the LangGraph workflow:
                    redact secrets > read recent logs > match runbook > LLM diagnosis > policy check
                        |                                         |
              safe action: auto-resolve              changes something: AWAITING_APPROVAL + email (SNS)
                                                                  |
 You (PowerShell) --> API Gateway --> Lambda "authorizer" (checks your token stored in SSM)
                      GET /incidents, POST /incidents, POST /incidents/{id}/approve or /reject
                                                                  |
                      approve --> job goes back on the queue --> action runs (a dry run unless AllowMutatingActions=true)
```

**What gets created (you will see about 18 resources)**

| AWS service | OpsPilot resource | What it does | How to find it |
| --- | --- | --- | --- |
| CloudFormation | stack `opspilot-dev` | Holds everything below, so one command can delete it all | search `CloudFormation` |
| Lambda | five functions named `opspilot-...` | api, authorizer, ingest, triage worker, demo workload | search `Lambda` > Functions |
| API Gateway | HTTP API | The web address you call | search `API Gateway` |
| DynamoDB | table `opspilot-dev-...` | Stores incidents and their timeline | search `DynamoDB` > Tables |
| SQS | triage queue and dead-letter queue | Work list for the worker, plus a parking place for failures | search `SQS` |
| EventBridge | one rule | Turns the alarm into an incident | search `EventBridge` > Rules |
| CloudWatch | alarms `opspilot-demo-workload-errors-dev` and `opspilot-dev-dead-letter-queue-not-empty` | Detect the failure, and detect stuck jobs | search `CloudWatch` > Alarms |
| SNS | one topic with your email | Sends the approval email | search `SNS` > Topics |
| S3 | ticket bucket | Holds drafted tickets | search `S3` |
| Systems Manager | parameter `/opspilot/dev/api-token` | Keeps your API token encrypted | search `Parameter Store` |
| IAM | roles created by SAM | Give each function only the access it needs | search `IAM` > Roles |
| Bedrock | optional | The real language model | search `Bedrock` |

## How to move around the console (10 minutes)

Use your own home page as the guide:

- **Search box** at the top (shortcut Alt+S). Type a service name and click the result. This is the fastest way.
- **All services** button on the right of the Recently visited box lists everything.
- **CloudShell** is at the bottom left. It is a terminal inside the browser, already signed in as you.
- **Recently visited** shows the services you opened lately. Amazon Bedrock is already there.
- **Manage billing** at the top right is where budgets and the free-tier balance live.

Try it now: search `Lambda`, `DynamoDB`, `SQS` and `CloudFormation` once each so they are familiar. They will be empty. After the deploy in Phase 3 they fill up.

## Phase 0: Install the tools on your computer

The handbook uses Windows PowerShell. In PowerShell run each check. If one says "not recognized", install it.

| Tool | Check | Install (or use the official installer) |
| --- | --- | --- |
| Python 3.12 | `python --version` | `winget install Python.Python.3.12` |
| Git | `git --version` | `winget install Git.Git` |
| AWS CLI v2 | `aws --version` | `winget install Amazon.AWSCLI` |
| AWS SAM CLI | `sam --version` | `winget install Amazon.SAM-CLI` |
| Docker Desktop | `docker --version` | `winget install Docker.DockerDesktop` |

Close and reopen PowerShell after installing so it finds the new tools. Docker Desktop needs a restart and may ask you to enable WSL 2. **Start Docker Desktop before Phase 3** and wait until it says it is running.

If Docker will not install or start on your computer, use **Option B (CloudShell)** at the end of this guide for Phases 2 to 3.

- [ ] All five checks print a version number

## Phase 1: Run it on your laptop (no AWS needed)

Goal: watch the whole triage workflow work before touching AWS. About 15 minutes.

1. Unzip the project. Open PowerShell inside the folder (for example `D:\Hands_On_Projects\AWS\opspilot`). Tip: in File Explorer, click the address bar, type `powershell`, press Enter.
2. Create a private Python workspace and switch it on:

   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   You should see `(.venv)` at the start of the prompt. If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process RemoteSigned` and try again.
3. Install the libraries: `pip install -r src/requirements.txt -r requirements-dev.txt`
4. Tell Python where the code is: `$env:PYTHONPATH="src"`
5. Run the tests: `python -m unittest discover -s tests -t .`  You should see `OK` and 64 tests.
6. Triage a pretend out-of-memory incident and approve it:

   ```
   python -m opspilot.cli triage samples/incidents/lambda_oom.json --approve
   ```
7. Read the output top to bottom. In order you should see: evidence kept, runbooks matched (`RB-LAMBDA-OOM` first), a diagnosis with confidence 0.82, `approval required`, `waiting for human approval`, then `RESOLVED: Raised ... from 128 MB to 256 MB`. The memory change happens on a stub, not on AWS.
8. Run the other two samples: `lambda_timeout.json` ends by drafting a ticket (raising a timeout would hide the cause), and `lambda_exception.json` also drafts a ticket and reports redactions (it contains fake secrets and a line that tries to give the assistant orders).
9. Try saying no: `... lambda_oom.json --reject` ends as `REJECTED` and nothing changes.

**What to notice:** secrets were removed before any model saw the text, and the "ignore your instructions" line in the sample log was treated as data, not as a command.

- [ ] 64 tests OK, and you saw RESOLVED, a drafted ticket and REJECTED

## Phase 2: Account safety and the command-line user

Goal: protect the account and let your computer deploy as a limited user, never as the owner.

**Step 1. Turn on two-step sign-in.** In the console click your name at the top right, then **Security credentials** (or search `IAM` and open **My security credentials**). Choose **Assign MFA device** and follow the authenticator-app steps. If you sign in with Google, GitHub or Amazon, also turn on 2-step verification in that account.

**Step 2. Create the budget alert.** Search `Budgets` > **Create budget** > choose **Use a template (simplified)** > **Zero spend budget** > enter your email > **Create budget**. You will get an email on the first charge.

**Step 3. (Optional) spend limit.** Click **Manage billing** on the home page and look for the spend limit option. Read item 5 in "Read this first" before you create one.

**Step 4. Create the CLI user.**

1. Search `IAM` > **Users** > **Create user**.
2. User name: `opspilot-deployer`.
3. **Leave "Provide user access to the AWS Management Console" unticked.** Your account type blocks console passwords, and this user only needs the command line.
4. Click **Next** > **Attach policies directly** > tick **AdministratorAccess** > **Next** > **Create user**. (Fine for a personal sandbox. In a shared account you would write a narrower policy. Mention that trade-off in interviews.)

**Step 5. Create its access key.**

1. IAM > Users > click `opspilot-deployer` > **Security credentials** tab > **Create access key**.
2. Choose **Command Line Interface (CLI)**, tick the confirmation box, **Next** > **Create access key**.
3. **Copy the Access key and Secret access key now.** The secret is shown only once. Do not put them in a file, email or GitHub.

**Step 6. Connect your computer to AWS.** In PowerShell:

```
aws configure --profile opspilot
```

Paste the key, the secret, region `ap-southeast-2` and output `json`. Then:

```
$env:AWS_PROFILE="opspilot"
aws sts get-caller-identity
aws configure get region --profile opspilot
```

The ARN must end in `user/opspilot-deployer`, and the region must print `ap-southeast-2`.

Each time you open a new PowerShell window, run the `$env:AWS_PROFILE="opspilot"` line again.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Unable to locate credentials` | Profile not set in this window | Run the `AWS_PROFILE` line again |
| `InvalidClientTokenId` | Key typed wrongly | Create a new key and run `aws configure --profile opspilot` again |
| `not authorized to perform: iam:CreateLoginProfile` | You ticked console access | Delete the user and recreate it with that box unticked |

- [ ] MFA on, budget created, `get-caller-identity` shows `opspilot-deployer`, region is `ap-southeast-2`

## Phase 3: Build and deploy with SAM

Goal: create all the AWS resources from one template. About 10 minutes.

Start **Docker Desktop** first. In PowerShell, inside the project folder, with the virtual environment off or on (either is fine) and `AWS_PROFILE` set:

1. Check the template: `sam validate --lint`. Expect `is a valid SAM Template`.
2. Build: `sam build`. The first run downloads a Linux image, so wait a few minutes. Expect `Build Succeeded`.
3. Deploy, with your real email (it is only typed here, never saved in a file):

   ```
   sam deploy --parameter-overrides NotifyEmail=you@example.com
   ```
4. SAM lists the changes and asks to confirm. Type `y`. Wait for it to finish.
5. Near the end you get an **Outputs** list: `ApiUrl`, `DemoFunctionName`, `DemoAlarmName`, `TableName`, `TicketBucketName`, `TriageQueueUrl`, `DeadLetterQueueUrl`. **Copy the whole list into a notepad.**

**Look at what you built (click through each):**

- CloudFormation > Stacks > `opspilot-dev` shows `CREATE_COMPLETE`. Open the **Resources** tab to see all items, and the **Outputs** tab for the list above.
- Lambda > Functions: five functions starting with `opspilot-`. Click one and open the **Monitor** and **Configuration** tabs.
- DynamoDB > Tables > the `opspilot-dev-...` table > **Additional settings** or **Capacity** shows provisioned 5 read and 5 write.
- SQS > Queues: the triage queue and the dead-letter queue.
- EventBridge > Rules: one rule that listens for alarm changes.
- CloudWatch > Alarms: two alarms, both `OK` or `Insufficient data` for now.
- SNS > Topics: one topic. AWS has already emailed you a confirmation link (you click it in Phase 4).

| Message | Meaning | Fix |
| --- | --- | --- |
| `Cannot connect to the Docker daemon` | Docker is not running | Start Docker Desktop, retry |
| `Unzipped size must be smaller than 262144000 bytes` | Libraries too big for one function | Send me the message. The handbook suggests a Lambda layer or a container image |
| `ROLLBACK_COMPLETE` | One resource failed | CloudFormation > Events tab, find the first `CREATE_FAILED`, send me that line, then run `sam delete` and redeploy |
| `explicit deny in a service control policy` | Your account type blocks that action | Send me the full line. Common causes: wrong Region, a spend limit near its cap, or a service outside the Free Tier |
| `ParameterNotFound` in logs, or a 500 from the API | Token not created yet | Do Phase 4 step 1 |

- [ ] Stack is `CREATE_COMPLETE` and you saved the Outputs

## Phase 4: Token, email and the model switch

Goal: protect the API, receive email, and know how to change the model.

**Step 1. Create the API token.** In PowerShell:

```
$token = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
aws ssm put-parameter --name /opspilot/dev/api-token --type SecureString --value $token
$env:OPSPILOT_TOKEN = $token
```

Check it exists: search `Parameter Store` and you should see `/opspilot/dev/api-token` as type SecureString. The token lives only in this window and in AWS. Do not paste it into any file.

**Step 2. Confirm the email.** Open the message from AWS Notifications and click **Confirm subscription**. Check: SNS > Subscriptions shows status **Confirmed**. Look in spam if it has not arrived.

**Step 3. Save the API address** (the `ApiUrl` from Phase 3, with no slash at the end):

```
$env:API="<ApiUrl>"
```

**Step 4. Test the lock.** Without the token it must fail, and with it must work:

```
curl.exe -s -o NUL -w "%{http_code}" "$env:API/incidents"
curl.exe -s -H "Authorization: Bearer $env:OPSPILOT_TOKEN" "$env:API/incidents"
```

Expect `401`, then an empty list in JSON.

**Using a real model (optional, costs a little).** The default is the free offline `mock` model, which is enough for every test. To try Bedrock:

1. Open Bedrock: search `Bedrock` and click **Amazon Bedrock** (also under Recently visited).
2. In the left menu open **Playground** > **Chat**, click **Select model**, choose **Amazon** > **Nova Lite**, type `hello` and send. A reply means the model works in Sydney. There is nothing to request or approve.
3. Check the exact ID your account can call:

   ```
   aws bedrock list-foundation-models --by-provider amazon --region ap-southeast-2 --query "modelSummaries[?contains(modelId,'nova-lite')].[modelId,inferenceTypesSupported]" --output json
   ```

   You want `amazon.nova-lite-v1:0` with `ON_DEMAND` in the list.
4. Redeploy with the model switched on. **Repeat every parameter each time**, because ones you leave out return to their defaults:

   ```
   sam deploy --parameter-overrides NotifyEmail=you@example.com LlmProvider=bedrock BedrockModelId=amazon.nova-lite-v1:0
   ```
5. If you see `AccessDeniedException`, open `template.yaml`, find the Bedrock permission block and check it allows `arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-lite-v1:0`. Send me the error and that block.
6. Bedrock charges per use from your credits. When you finish, redeploy without `LlmProvider=bedrock` to go back to `mock`.

**Real changes are a second switch.** `AllowMutatingActions=true` lets an approved memory change really happen. Without it you only get a dry run.

- [ ] 401 without a token, an empty list with it, and the email subscription is Confirmed

## Phase 5: Break something on purpose and approve the fix

Use the **same PowerShell window** as Phase 4 so `$env:API` and `$env:OPSPILOT_TOKEN` are still set. About 10 minutes.

### Test A: make an incident by hand (fastest)

```
$h = @{ Authorization = "Bearer $env:OPSPILOT_TOKEN" }
$body = '{"title":"Manual test","resource":{"function_name":"<DemoFunctionName>"}}'
Invoke-RestMethod -Method Post -Uri "$env:API/incidents" -Headers $h -ContentType "application/json" -Body $body
```

Expect `duplicate: False` and status `RECEIVED`. Wait 20 seconds and read the list:

```
Invoke-RestMethod -Uri "$env:API/incidents" -Headers $h
```

The status moves on from RECEIVED. A function with no failures usually ends as RESOLVED with a notify-only result, because OpsPilot refuses to act without evidence. Send the same `$body` again: you should see `duplicate: True` and `occurrences` going up.

**Watch it in the console:** DynamoDB > Tables > your table > **Explore table items** shows the incident and its timeline.

### Test B: the real alarm path

1. Crash the demo function on purpose:

   ```
   Set-Content -Path oom.json -Value '{"mode":"oom"}' -NoNewline
   aws lambda invoke --function-name <DemoFunctionName> --cli-binary-format raw-in-base64-out --payload file://oom.json out.json
   ```

   It returns fast with a `FunctionError`. That is the plan.
2. Wait 1 to 3 minutes. CloudWatch > Alarms > `opspilot-demo-workload-errors-dev` changes to **In alarm**.
3. EventBridge starts the ingest function, which queues a job. Find the worker's name: Lambda > Functions, search `opspilot`, pick the one with `triage` or `worker` in its name. Then read its log:

   ```
   aws logs tail /aws/lambda/<TriageWorkerFunctionName> --since 10m
   ```

   Look for JSON lines with `incident_id`, then `diagnosis` and `policy`.
4. Check your inbox for `[OpsPilot][P2] Approval needed: INC-...`. It shows the diagnosis (out of memory), the proposed action and the approve and reject addresses.
5. Approve it (use the `INC-` number from the email or from the incident list):

   ```
   Invoke-RestMethod -Method Post -Uri "$env:API/incidents/<INC-id>/approve" -Headers $h -ContentType "application/json" -Body '{"by":"your-name"}'
   ```

   Expect `APPROVED`, then `RESOLVED` within seconds.
6. Read the result and timeline:

   ```
   Invoke-RestMethod -Uri "$env:API/incidents/<INC-id>" -Headers $h
   ```

   By default it starts with `DRY-RUN: would raise ... from 128 MB to 256 MB`.
7. To see a real change: redeploy with `AllowMutatingActions=true` added (plus all your other parameters), repeat Test B with a fresh failure (wait 30 minutes for the repeat-alert window, or use a different message), approve, then open Lambda > the demo function > Configuration > General configuration and confirm memory is now 256 MB.

### Test C: safety checks worth showing in an interview

| Check | How | Expected |
| --- | --- | --- |
| Approve twice | Run the approve call two times | The second returns 409, `not AWAITING_APPROVAL` |
| Wrong token | Use `Bearer nope` | 403 |
| No token | Leave the header out | 401 |
| Say no | Call reject instead of approve | `REJECTED`, nothing changes |
| Dead-letter alarm | Nothing to do | `opspilot-dev-dead-letter-queue-not-empty` stays `OK` |

- [ ] Test A shows a duplicate, Test B reaches RESOLVED, and Test C behaves as listed

## Phase 6: Where to look when something is wrong

Every function writes JSON logs to `/aws/lambda/<function name>` with an `incident_id`, so one search follows an incident everywhere.

1. Search `CloudWatch` > **Logs Insights**.
2. Pick the `opspilot` log groups in the selector at the top, set the time range, and run:

   ```
   fields @timestamp, message, incident_id
   | filter incident_id = "INC-..."
   | sort @timestamp
   ```
3. Custom counters such as IncidentCreated, IncidentDeduplicated and ApprovalGranted appear under CloudWatch > **All metrics**.

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| API gives 401 | No `Authorization` header | Add `Bearer <token>` |
| API gives 403 | Token differs from the saved one | `aws ssm get-parameter --name /opspilot/dev/api-token --with-decryption`, then reset `$env:OPSPILOT_TOKEN` |
| API gives 500 | Error in the API function | Tail `/aws/lambda/<api function>` and read the error |
| Alarm fires, no incident | Alarm name does not start with `opspilot-demo-` | The ingest function ignores other alarms on purpose |
| Stuck at `RECEIVED` | Worker not running or failing | SQS > the queue > **Monitoring** tab, and the worker log |
| `AWAITING_APPROVAL`, no email | Subscription not confirmed | SNS > Subscriptions, click the email link, check spam |
| Message in the dead-letter queue | Worker failed 3 times | Read the worker log, fix, then in SQS choose **Start DLQ redrive** |
| `AccessDeniedException` on Bedrock | Wrong model ID or policy | See Phase 4, steps 3 and 5 |
| Result says `DRY-RUN` | Mutation is off | Intended. Add `AllowMutatingActions=true` if you want a real change |
| Approve gives 409 | Already approved, rejected, or not triaged yet | Read the incident status first |

A good habit: reproduce, find the log line, say the cause in one sentence, then fix.

## Phase 7: Clean up and check nothing is still billing

Do this at the end of every session. Idle resources here cost nothing, but build the habit.

1. If you used Bedrock, redeploy without `LlmProvider=bedrock` if you keep the stack.
2. Empty the ticket bucket (a full bucket blocks deletion): `aws s3 rm s3://<TicketBucketName> --recursive`
3. Delete the stack: `sam delete`. Answer `y` to every question, including deleting SAM's own upload bucket.
4. Delete the token: `aws ssm delete-parameter --name /opspilot/dev/api-token`
5. Remove leftover logs: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/opspilot`, then `aws logs delete-log-group --log-group-name <name>` for each one listed.
6. Confirm in the console: CloudFormation has no `opspilot-dev`, and the SQS, DynamoDB and Lambda pages show no `opspilot` items.
7. Switch off the access key if you will not use the account for a while: IAM > Users > `opspilot-deployer` > Security credentials > **Make inactive** (or delete it).
8. Next morning, open Billing > Cost Explorer for charges. The zero-spend budget should stay quiet.

The code in your repository is the portfolio piece. The running stack is not.

## Option B: use CloudShell instead of installing tools

Use this if Docker or the AWS CLI will not work on your computer. It replaces Phases 2 and 3, and it needs **no IAM user and no access key**, which is safer. I have not tested this path in your account, so tell me what you see.

1. Click **CloudShell** at the bottom left of the console. Wait for the prompt. You are already signed in, in Sydney.
2. Click **Actions** > **Upload file** and pick your project zip. Then run `unzip <zip name>` and `cd` into the folder.
3. CloudShell already has the AWS CLI and SAM CLI. Run `sam validate --lint`, then `sam build --use-container` (Docker is available in CloudShell but has limited space), then `sam deploy --parameter-overrides NotifyEmail=you@example.com`.
4. If the build says there is no space left, the 1 GB home folder is full. Delete old files, or go back to the computer route.
5. CloudShell is Linux, so the handbook's PowerShell lines change shape: `$env:API=...` becomes `export API=...`, `Invoke-RestMethod` becomes `curl`, and the token is made with `openssl rand -base64 32`. Ask me and I will translate any block.

## Words you will meet

- **Lambda:** a small program AWS runs only when something calls it.
- **API Gateway:** the front door that gives your Lambda functions a web address.
- **SQS queue:** a to-do list between two programs. The **dead-letter queue** holds jobs that kept failing.
- **EventBridge:** passes events (like "the alarm fired") to the right program.
- **DynamoDB:** a simple database table.
- **SNS:** sends the email.
- **SSM Parameter Store:** a safe place for secrets such as your token.
- **CloudFormation and SAM:** a recipe file that builds every resource in one go and deletes it in one go.
- **IAM user and role:** a user is you (or your computer). A role is a set of permissions a function borrows.

## When something fails, send me this

1. The phase and step number.
2. The exact command you ran (hide keys, tokens and your email).
3. The full error text, copied, not paraphrased.
4. For deploy errors: the first `CREATE_FAILED` line from CloudFormation > Events.
5. Whether the address bar shows `region=ap-southeast-2`.
