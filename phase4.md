# Phase 4 — Full End-to-End Pipeline + Production Hardening

## Mục tiêu

Hoàn thiện pipeline production-ready:

```text
SharePoint
    ↓
Delta Sync
    ↓
Blob Raw
    ↓
Chunk Worker
    ↓
Embedding Worker
    ↓
Azure AI Search
```

---

# Production Architecture

```text
Timer Function
    ↓
Queue Producer
    ↓
Chunk Worker
    ↓
Embedding Worker
    ↓
Azure AI Search
```

---

# Queue Structure

```text
document-processing-queue
embedding-queue

document-processing-queue-poison
embedding-queue-poison
```

---

# Reliability Design

## Poison Queue

Nếu retry vượt ngưỡng:

```text
move message to poison queue
```

---

# Idempotent Processing

Sử dụng:

```text
(file_id + etag)
```

Nếu document đã xử lý:

```text
skip processing
```

---

# Fault Tolerance

Mỗi step phải:

- retry độc lập
- không crash toàn pipeline
- có deadletter handling

---

# Distributed Logging

## App Insights

Mỗi log trace theo:

```text
run_id
file_id
chunk_id
queue_message_id
```

---

# Metrics Dashboard

## Business Metrics

```text
documents_per_day
chunks_per_day
active_documents
deleted_documents
```

---

## Technical Metrics

```text
queue_length
queue_processing_time
function_execution_time
memory_usage
```

---

## AI Metrics

```text
embedding_cost_estimate
token_usage
avg_vector_latency
```

---

# Alerting

## Queue backlog

```text
queue > threshold
```

---

## Error rate

```text
failed_documents > threshold
```

---

## Function timeout

```text
execution_time > threshold
```

---

## AI Search failure

```text
upsert_failure_rate > threshold
```

---

# Final Done Criteria

Pipeline production-ready khi:

- restart không mất state
- retry ổn định
- poison queue hoạt động
- chunk deterministic
- delete sync chính xác
- vector search đúng
- scaling ổn định
- observability đầy đủ
- trace end-to-end theo run_id