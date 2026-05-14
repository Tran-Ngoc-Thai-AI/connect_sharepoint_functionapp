# Phase 2 — Document Extract + Smart Chunking Worker

## Mục tiêu

Xử lý document:

```text
Queue Trigger
    ↓
Download raw document
    ↓
Extract text
    ↓
Detect structure
    ↓
Chunk theo template
    ↓
Lưu chunk JSON
    ↓
Push embedding queue
```

---

# Function

```text
document_chunk_processor
```

---

# Queue Input

```text
document-processing-queue
```

---

# Blob Input

```text
raw-documents/{file_id}/{filename}
```

---

# Blob Output

```text
processed-chunks/{file_id}.json
```

---

# Queue Output

```text
embedding-queue
```

---

# Template Structure

```text
XXXX.ABC.ZZZZ(n) - TÊN VĂN BẢN
├── 1. Phạm vi điều chỉnh
├── 2. Nội dung lớn
├── 3.1
├── 3.2
├── PL01
├── PL02
└── PL03
```

---

# Chunking Rules

## Section

```text
1 section = 1 chunk
```

Ví dụ:

```text
1. Phạm vi điều chỉnh
```

---

## Subsection

```text
3.1 = 1 chunk
```

Ví dụ:

```text
3.1 Quy trình phê duyệt
```

---

## Glossary

```text
1 glossary = 1 chunk
```

Ví dụ:

```text
PL01 - Giải thích từ ngữ
```

---

## Quy trình

```text
1 step = 1 chunk
```

Ví dụ:

```text
Bước 1
Bước 2
```

---

# Chunk Metadata Schema

```json
{
  "chunk_id": "abc123_3_1",
  "file_id": "abc123",
  "document_code": "XXXX.ABC.ZZZZ(1)",
  "document_name": "Tên văn bản",
  "section": "3",
  "subsection": "3.1",
  "title": "Quy trình phê duyệt",
  "content": "...",
  "chunk_type": "subsection",
  "order": 15,
  "token_estimate": 1200
}
```

---

# Queue Message Sau Chunking

```json
{
  "file_id": "abc123",
  "chunk_blob_path": "processed-chunks/abc123.json",
  "event_type": "updated",
  "run_id": "uuid"
}
```

---

# Retry Strategy

## Text Extraction

Retry:

```text
3 lần
```

---

## Chunk Parsing

Nếu regex fail:

```text
fallback semantic chunking
```

Không được crash pipeline.

---

## Queue Push

Retry:

```text
3 lần
```

---

# Logging Standard

```json
{
  "run_id": "uuid",
  "file_id": "abc123",
  "chunk_count": 25,
  "phase": "phase2",
  "function_name": "document_chunk_processor"
}
```

---

# Metrics

## Chunk Metrics

```text
documents_processed
chunk_count
avg_chunks_per_document
avg_chunk_size
```

---

## Performance Metrics

```text
extract_latency_ms
chunking_latency_ms
```

---

## Reliability Metrics

```text
failed_documents
fallback_chunking_count
```

---

# Done Criteria

- Chunk semantic đúng
- Không crash khi format lệch
- Metadata đầy đủ
- Queue downstream hoạt động