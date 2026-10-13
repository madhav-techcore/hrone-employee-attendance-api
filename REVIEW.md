# Code Review

## Project

Employee Attendance & Analytics API

## Review Scope

Reviewed the starter `app/main.py`, including helper functions, request validation, attendance endpoints, analytics endpoints, MongoDB operations, and error handling.

## Defects Found

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `late_leaderboard` — MongoDB `$setWindowFields` / `$rank` stage | The ranking stage used a `sortBy` expression containing more than one field. MongoDB `$rank` requires the top-level `sortBy` expression to contain exactly one field, so the aggregation fails. | Request `GET /analytics/leaderboard/late?month=2026-10&limit=10`. The original pipeline returned HTTP 500 with a MongoDB error stating that `$rank` requires exactly one `sortBy` field. | Updated the ranking stage to use a single sort field for `$rank`, while retaining deterministic ordering in the preceding `$sort` stage. Verified the endpoint returned HTTP 200. |

## Other Checks Performed

- Verified `GET /health` returned HTTP 200.
- Verified employee creation and duplicate employee handling; a duplicate returned HTTP 409.
- Verified duplicate punch-in handling returned HTTP 409.
- Verified attendance correction returned HTTP 200 and included audit history.
- Verified employee monthly analytics returned HTTP 200.
- Verified department summary returned HTTP 200 with a valid `YYYY-MM` month.
- Verified department trend returned HTTP 200 for a valid date range.
- Verified the department-summary explain endpoint returned execution-plan information.

## Suspected Issues Reviewed but Not Classified as Defects

- **Root URL returning 404:** The API does not define a `/` route. This is expected; use `/health` to check service health and `/docs` to access Swagger UI.
- **Month supplied as `10`:** Analytics endpoints expect `YYYY-MM`, such as `2026-10`. A request using only `10` is invalid input, not an endpoint defect.
- **Duplicate employee or punch-in returning HTTP 409:** These responses are intentional conflict handling for duplicate records, not defects.
- **`.env` and `.venv` missing from Git status:** These paths are intentionally excluded through `.gitignore` and should not be committed.

## Remaining Checks

- Compare the final implementation against the original starter file to identify any additional defects.
- Test invalid inputs, boundary conditions, and date ranges.
- Complete testing of all supported admin explain options.
- Verify setup from a clean environment and document any remaining limitations.
