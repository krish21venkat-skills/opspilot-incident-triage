# 5. TF-IDF retrieval instead of a vector database
Status: accepted

## Context
The corpus is a handful of short runbooks. Managed vector stores are not free-tier.
## Decision
Index runbooks in memory with TF-IDF and cosine similarity at cold start.
## Consequences
No cost, no network call, easy to test. It will not scale to thousands of documents; the `RunbookIndex` interface lets embeddings replace it later.
