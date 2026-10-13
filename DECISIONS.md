# Technical Decisions

## 1. FastAPI

FastAPI is used to expose REST endpoints and provide interactive API documentation through Swagger UI.

## 2. MongoDB

MongoDB stores employee records and attendance logs in separate collections.

## 3. Environment Configuration

The MongoDB URI and database name are configured through environment variables rather than being hard-coded into application logic.

## 4. Attendance Analytics

Aggregation pipelines are used to calculate attendance summaries, late-arrival statistics, and department trends within MongoDB.

## 5. Attendance Corrections

Manual attendance corrections record the changes and the administrator-provided reason to support traceability.

## 6. Query Performance

The admin explain endpoint exposes MongoDB execution plans to help inspect index usage and query execution.

## 7. API Validation

Request validation and appropriate HTTP status codes are used to handle invalid requests and duplicate records.

