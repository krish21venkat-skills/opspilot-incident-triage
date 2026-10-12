# 4. DynamoDB single table with provisioned capacity
Status: accepted

## Context
Access patterns are: get by id, list latest, de-duplicate by fingerprint. Free tier covers only provisioned capacity.
## Decision
One table with `pk`, a TTL attribute for dedup markers, and a GSI `recent-index` (entity, created_at). Provisioned 5/5 and 2/2.
## Consequences
Free and sufficient for the demo. The list query filters by status after the read, so it pages with a bounded loop. Expiry is checked in the condition because TTL deletion can lag.
