# Azure Deployment Plan

Status: Code Prepared

## Scope

Build the Phase 1 Azure Functions Python v2 ingestion layer described in `phase1.md`.

## Application

- Runtime: Azure Functions Python v2
- Python: 3.12
- Trigger: Timer trigger named `sharepoint_delta_timer`
- Schedule: `0 */5 * * * *`
- Authentication: Managed Identity via `DefaultAzureCredential`
- Target integrations: Microsoft Graph, Azure Blob Storage, Azure Queue Storage

## Azure Resources Expected

- Function App with managed identity enabled
- Storage account for Azure Functions host
- Storage account or connection string for ingestion data
- Blob containers: `raw-documents`, `metadata`, `delta-state`, `deadletter`
- Queue: `document-processing-queue`

## Configuration

Required app settings:

- `AzureWebJobsStorage`
- `STORAGE_ACCOUNT_URL` or `INGESTION_STORAGE_CONNECTION_STRING`
- `SHAREPOINT_SITE_ID`
- `SHAREPOINT_DRIVE_ID`
- `GRAPH_DELTA_PAGE_SIZE`
- `GRAPH_TIMEOUT_SECONDS`

## Notes

- This plan covers code/config preparation only.
- No Azure deployment command is run by this task.
