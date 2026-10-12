---
id: RB-LAMBDA-ERRORS
title: Lambda function raises unhandled exceptions
keywords: exception, traceback, error, ValueError, KeyError, TypeError, unhandled, stack trace, bug
---

# Lambda function raises unhandled exceptions

## Symptoms
- Log lines with `Traceback`, `Exception` or `ERROR` and a stack trace.
- Errors metric above zero while Duration looks normal.

## Likely causes
1. A code defect introduced by a recent deployment.
2. Unexpected input shape from an upstream producer.
3. A dependency returned an error that the code does not handle.

## Safe remediation
1. Identify the first deployment or input change that matches the start of the errors.
2. Open a ticket with the exception type, the failing line and the affected input.
3. If the errors began right after a release, recommend a rollback to a human.

## Do not
- Change memory or timeout to fix a code error.
