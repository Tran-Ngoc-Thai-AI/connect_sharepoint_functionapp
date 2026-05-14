# Phase 1 — SharePoint REST API Change Detection + Queue Producer

## Mục tiêu

Xây dựng ingestion layer đầu tiên:

```text
Timer Trigger
    ↓
SharePoint REST API
    ↓
Get current document library snapshot
    ↓
Compare with saved snapshot state
    ↓
Detect created / updated / deleted
    ↓
Download changed files
    ↓
Blob Storage
    ↓
Push Queue Message
```

Phase này tập trung validate:

- Timer Trigger
- Managed Identity
- SharePoint REST API polling
- Snapshot state management
- Change detection
- Blob state
- Queue message
- Retry strategy
- Logging + metrics

Chưa xử lý:

- chunking
- embedding
- Azure AI Search

---

# Runtime

- Azure Functions Python v2
- Python 3.12
- SharePoint Online REST API
- Managed Identity

---

# Schedule

```cron
0 */5 * * * *
```

---

# Function

```text
sharepoint_change_detection_timer
```

---

# Blob Containers

```text
raw-documents
metadata
state
deadletter
```

---

# Queue

```text
document-processing-queue
```

---

# Input

SharePoint Document Library

---

# Flow Processing

```text
Timer Trigger
    ↓
Call SharePoint REST API
    ↓
Get current document library snapshot
    ↓
Load saved snapshot state
    ↓
Compare snapshots
    ↓
Detect created / updated / deleted
    ↓
Download changed files
    ↓
Upload raw files to Blob
    ↓
Save metadata
    ↓
Push queue messages
    ↓
Save new snapshot state
```

---

# Pagination Strategy

## SharePoint REST API Pagination

Document library có thể lớn hơn:

```text
5000+ files
```

Function phải hỗ trợ:

```text
$top
odata.nextLink
__next
```

Yêu cầu:

- loop toàn bộ pages
- collect full snapshot trước khi compare state

---

# File Filtering

## Chỉ xử lý

```text
.docx
.pdf
.xlsx
.pptx
```

---

## Bỏ qua

```text
.tmp
.forms
.aspx
```

---

# Output

## Raw Documents

```text
raw-documents/{file_id}/{filename}
```

Ví dụ:

```text
raw-documents/abc123/QD001.docx
```

---

## Metadata

```text
metadata/{file_id}.json
```

Ví dụ:

```json
{
  "file_id": "abc123",
  "file_name": "QD001.docx",
  "etag": "123456",
  "sharepoint_url": "...",
  "last_modified": "2026-05-13T08:00:00Z",
  "status": "updated"
}
```

---

## Snapshot State

```text
state/document_library_state.json
```

Ví dụ:

```json
{
  "abc123": {
    "etag": "\"{GUID},1\"",
    "last_modified": "2026-05-13T08:00:00Z"
  },
  "xyz999": {
    "etag": "\"{GUID},2\"",
    "last_modified": "2026-05-13T09:00:00Z"
  }
}
```

---

# Queue Message Schema

```json
{
  "event_type": "created",
  "file_id": "abc123",
  "file_name": "QD001.docx",
  "blob_path": "raw-documents/abc123/QD001.docx",
  "etag": "123456",
  "sharepoint_url": "...",
  "last_modified": "2026-05-13T08:00:00Z",
  "run_id": "uuid",
  "timestamp": "2026-05-13T08:05:00Z"
}
```

---

# Change Detection Logic

## Created

```text
File exists in current snapshot
but NOT exists in saved snapshot
```

---

## Updated

```text
Same file_id
but ETag changed
```

hoặc:

```text
LastModified changed
```

---

## Deleted

```text
File exists in saved snapshot
but NOT exists in current snapshot
```

---

# Idempotent Strategy

Sử dụng:

```text
(file_id + etag)
```

Nếu metadata hiện tại đã có:

```text
same etag
```

thì:

```text
skip processing
```

---

# Retry Strategy

## SharePoint REST API

Retry khi:

- 429
- timeout
- transient network error

Strategy:

```text
1s → 2s → 4s → 8s
```

Max retry:

```text
5 lần
```

---

## Blob Upload

Retry:

```text
3 lần
```

---

## Queue Push

Retry:

```text
3 lần
```

Nếu fail:

```text
deadletter container
```

---

# Logging Standard

Mỗi log phải có:

```json
{
  "run_id": "uuid",
  "file_id": "abc123",
  "event_type": "created",
  "phase": "phase1",
  "function_name": "sharepoint_change_detection_timer"
}
```

---

# Metrics

## Change Detection Metrics

```text
files_scanned
created_count
updated_count
deleted_count
```

---

## Performance Metrics

```text
sharepoint_api_latency_ms
download_latency_ms
blob_upload_latency_ms
queue_publish_latency_ms
```

---

## Reliability Metrics

```text
retry_count
failed_downloads
deadletter_count
```

---

# Security

## Authentication

Sử dụng:

```text
Managed Identity
```

---

## SharePoint Permission

Application permission:

```text
Sites.Selected
```

---

## Storage RBAC

Managed Identity cần:

```text
Storage Blob Data Contributor
Storage Queue Data Contributor
```

---

# Done Criteria

- SharePoint polling ổn định
- Snapshot state hoạt động đúng
- Change detection chính xác
- Blob upload thành công
- Queue message đúng schema
- Restart function không mất state
- Không duplicate processing