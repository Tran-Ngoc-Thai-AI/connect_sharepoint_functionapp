# Phase 3 — Embedding + Azure AI Search Indexer

## Mục tiêu

Generate embeddings và upsert Azure AI Search:

```text
Embedding Queue
    ↓
Generate Embeddings
    ↓
Upsert Azure AI Search
    ↓
Delete Sync
```

---

# Function

```text
embedding_indexer
```

---

# Queue Input

```text
embedding-queue
```

---

# Azure AI Search Index Schema

## Fields

```text
id
file_id
chunk_id
document_code
document_name
section
subsection
content
content_vector
last_modified
status
```

---

# Embedding Strategy

```text
1 chunk = 1 embedding
```

---

# Update Strategy

Khi file updated:

```text
delete old chunks
insert new chunks
```

---

# Delete Strategy

Khi file deleted:

```text
delete all chunks where file_id = X
```

---

# Retry Strategy

## Azure OpenAI

Retry khi:

- 429
- timeout
- rate limit

Strategy:

```text
1s → 2s → 4s → 8s
```

---

## Azure AI Search

Retry:

```text
3-5 lần
```

---

# Logging Standard

```json
{
  "run_id": "uuid",
  "file_id": "abc123",
  "chunk_id": "abc123_3_1",
  "embedding_model": "text-embedding-3-large",
  "phase": "phase3",
  "function_name": "embedding_indexer"
}
```

---

# Metrics

## Embedding Metrics

```text
embedding_requests
embedding_latency_ms
tokens_processed
```

---

## AI Search Metrics

```text
search_documents_uploaded
search_documents_deleted
search_upsert_latency_ms
```

---

## Reliability Metrics

```text
embedding_failures
search_failures
retry_count
```

---

# Done Criteria

- Vector search hoạt động
- Không duplicate chunks
- Delete sync chính xác
- Retry hoạt động ổn định