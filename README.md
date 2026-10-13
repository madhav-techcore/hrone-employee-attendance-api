# HROne Employee Attendance & Analytics API

Employee Attendance and Analytics REST API built with Python, FastAPI, and MongoDB.

## Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Duplicate punch-in prevention
- Manual attendance corrections with audit history
- Monthly employee attendance analytics
- Department attendance summaries
- Late-arrival leaderboard
- Department attendance trends
- MongoDB query execution plan analysis

## Requirements

- Python 3.11+
- MongoDB 6.0+
- Git

## Setup

1. Clone this repository.
2. Create and activate a Python virtual environment.
3. Install dependencies:

   `pip install -r requirements.txt`

4. Create a `.env` file in the project root:

   ```env
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   ```

5. Start the API:

   `uvicorn app.main:app --reload --port 8000`

6. Open the interactive API documentation at `http://127.0.0.1:8000/docs`.

## Testing

Manual endpoint testing has been performed. See `REVIEW.md` for the tested functionality and remaining checks.

## Design Decisions

See `DECISIONS.md` for the main design choices and `DATA_MODEL.md` for the data model.

## Limitations

Review `REVIEW.md` for remaining validation and testing work.