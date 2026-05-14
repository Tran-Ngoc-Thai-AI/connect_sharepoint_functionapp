# SharePoint Delta Query Function App

Ứng dụng chạy trên Azure Function App, Python 3.12. Function dùng Timer Trigger để gọi Microsoft Graph delta query mỗi 5 phút, xác định file SharePoint mới, file đã cập nhật, file đã xóa; sau đó xử lý nội dung, chunk theo cấu trúc văn bản nghiệp vụ và đưa vector vào Azure AI Search cho chatbot khai thác.

## Kiến trúc

Luồng xử lý:

1. Azure Function Timer Trigger chạy mỗi 5 phút.
2. Function xác thực với Microsoft Graph bằng Managed Identity.
3. Microsoft Graph `driveItem delta` trả về thay đổi của SharePoint document library.
4. Delta state được lưu trong Azure Blob Storage.
5. File `created/updated` được download từ Graph.
6. Nội dung được extract text, chunk theo template văn bản.
7. Mỗi chunk được tạo embedding bằng Azure OpenAI.
8. Chunk + vector được upsert vào Azure AI Search.
9. File `deleted` sẽ xóa các chunk tương ứng khỏi Azure AI Search.

## Runtime

- Azure Functions Python v2 programming model.
- Python 3.12.
- Schedule mặc định: `0 */5 * * * *`, tức mỗi 5 phút.

File liên quan:

- `function_app.py`: Timer Trigger entrypoint.
- `sharepoint_delta.py`: Microsoft Graph delta query, state, download file.
- `document_pipeline.py`: extract text, chunk văn bản, embedding, index Azure AI Search.
- `requirements.txt`: dependencies.
- `runtime.txt`: pin Python 3.12.
- `host.json`: Azure Functions host config.
- `local.settings.json.example`: app settings mẫu.

## SharePoint Authentication

Function dùng `DefaultAzureCredential`.

Trên Azure Function App, `DefaultAzureCredential` sẽ dùng Managed Identity của Function App. Bạn cần tự thực hiện:

- Enable system-assigned hoặc user-assigned managed identity cho Function App.
- Grant Microsoft Graph application permission `Sites.Selected`.
- Gán quyền cho SharePoint site URL cụ thể mà Function được phép đọc.

Nếu dùng user-assigned managed identity, thêm app setting:

```text
AZURE_CLIENT_ID=<client-id-cua-user-assigned-managed-identity>
```

Nếu tenant không cho resolve site từ URL bằng `Sites.Selected`, set trực tiếp:

```text
SHAREPOINT_SITE_ID=<site-id>
```

## App Settings

```text
AzureWebJobsStorage=<storage-connection-string>
FUNCTIONS_WORKER_RUNTIME=python
SHAREPOINT_DELTA_SCHEDULE=0 */5 * * * *
SHAREPOINT_SITE_URL=https://contoso.sharepoint.com/sites/demo

STATE_CONTAINER=sharepoint-delta-state
STATE_BLOB_NAME=delta-state.json

AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
AZURE_SEARCH_INDEX_NAME=sharepoint-chunks

AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-02-01
```

Tùy chọn:

```text
SHAREPOINT_DRIVE_ID=<drive-id>
STATE_STORAGE_ACCOUNT_URL=https://<storage-account>.blob.core.windows.net
AZURE_SEARCH_API_KEY=<api-key-neu-khong-dung-managed-identity>
AZURE_OPENAI_API_KEY=<api-key-neu-khong-dung-managed-identity>
```

Nếu dùng `STATE_STORAGE_ACCOUNT_URL`, Managed Identity cần quyền `Storage Blob Data Contributor`.

Nếu dùng Managed Identity cho Azure AI Search, identity cần quyền phù hợp để ghi index. Nếu dùng Managed Identity cho Azure OpenAI, identity cần quyền gọi embedding deployment.

## Delta Query

Nếu có `SHAREPOINT_DRIVE_ID`, code gọi:

```text
GET /drives/{drive-id}/root/delta
```

Nếu chỉ có `SHAREPOINT_SITE_URL` hoặc `SHAREPOINT_SITE_ID`, code gọi default drive:

```text
GET /sites/{site-id}/drive/root/delta
```

Lần chạy đầu tiên tạo baseline và không emit event. Từ lần chạy sau, Function dùng `@odata.deltaLink` để chỉ lấy thay đổi mới.

## Chunk Template

Tài liệu được chunk theo định dạng:

```text
XXXX.ABC.ZZZZ(n) - TÊN VĂN BẢN
├── 1. Phạm vi điều chỉnh        -> chunk theo section
├── 2. Nội dung lớn 1            -> chunk theo section/subsection
├── 3. Nội dung lớn 2            -> chunk theo subsection, ví dụ 3.1, 3.2
├── 4. Phụ lục và Mẫu biểu       -> chunk nhỏ theo danh sách
├── 5. Quản lý sự thay đổi       -> 1 chunk
├── PL01 - Giải thích từ ngữ     -> 1 chunk glossary
├── PL02 - Trách nhiệm đơn vị    -> chunk theo Khối/Phòng/Ban
└── PL03 - Quy trình thực hiện   -> chunk theo từng Bước
```

Hiện tại `DocumentExtractor` hỗ trợ `.docx`, `.txt`, `.md`. Nếu cần PDF scan hoặc OCR, nên bổ sung Azure Document Intelligence trước bước chunk.

## Azure AI Search Index

Code đang upsert document theo schema tối thiểu sau:

```text
id                 Edm.String, key
document_id        Edm.String, filterable
title              Edm.String, searchable
section            Edm.String, searchable/filterable
chunk_type         Edm.String, filterable
content            Edm.String, searchable
content_vector     Collection(Edm.Single), vector field
source_url         Edm.String
file_name          Edm.String, filterable
change_type        Edm.String, filterable
```

Tên vector field trong code là `content_vector`. Kích thước vector phải khớp embedding deployment bạn dùng.

## Local Run

```powershell
Copy-Item .\local.settings.json.example .\local.settings.json
pip install -r requirements.txt
func start
```

Khi chạy local, `DefaultAzureCredential` có thể lấy credential từ Azure CLI login hoặc credential local khác.

## Review Notes

- State không nên lưu filesystem Function App; code hiện lưu trong Blob Storage.
- Delta query chỉ trả trạng thái mới nhất của item, không phải mọi event trung gian.
- Khi Graph trả `410 Gone`, code resync và không emit event trong vòng resync để tránh index sai hàng loạt.
- Deleted item có thể thiếu metadata, nên code lấy lại `name`, `webUrl`, `driveId` từ state cũ.
- Nên track theo `id`, không track theo path, vì rename/move folder có thể không trả lại toàn bộ descendants.

Tài liệu Microsoft Graph: <https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0>


-------

npm install -g azure-functions-core-tools@4 --unsafe-perm true

-----------------------

# SharePoint Delta Query → Azure AI Search Pipeline

Ứng dụng chạy trên Azure Function App (Python 3.12), sử dụng Timer Trigger để đồng bộ document từ SharePoint thông qua Microsoft Graph Delta Query, xử lý chunking theo template văn bản nghiệp vụ, tạo embeddings và đưa vào Azure AI Search phục vụ chatbot RAG.

---------------------------------------

# Tổng Kiến Trúc

```text
SharePoint
    ↓
Timer Trigger Function
    ↓
Microsoft Graph Delta Query
    ↓
Blob Storage (raw documents + state)
    ↓
Azure Queue Storage
    ↓
Chunk Processing Worker
    ↓
Processed Chunk Storage
    ↓
Embedding Worker
    ↓
Azure OpenAI Embedding
    ↓
Azure AI Search

-------------

https://developer.microsoft.com/en-us/graph/graph-explorer

get https://graph.microsoft.com/v1.0/sites/sacombankvn.sharepoint.com:/sites/knowledgechatbot

get https://graph.microsoft.com/v1.0/sites/sacombankvn.sharepoint.com,53eae88e-a4ce-417e-b1a9-f7c9088ce32f,47f3ec4b-15d5-415f-9485-1907a1238571/drives