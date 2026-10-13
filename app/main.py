"""Employee Attendance & Analytics API. Run: uvicorn app.main:app --reload --port 8000"""
import os, re, calendar
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
app = FastAPI(title="Employee Attendance & Analytics API", version="3.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="emp_code_unique")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="emp_date_unique")
    db.attendance_logs.create_index([("date", ASCENDING), ("emp_code", ASCENDING)], name="date_emp")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", ASCENDING)], name="status_date")

# Helpers
def now_ist(): return datetime.now(IST).replace(microsecond=0)
def to_ms(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=IST)
    return int(value.timestamp() * 1000)
def from_ms(value):
    if value is None: return None
    if not isinstance(value, int) or value < 100000000000 or value > 4102444800000:
        raise HTTPException(422, "Timestamp must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, IST).replace(tzinfo=None, microsecond=0)
def dt_ms(value):
    if value is None: return None
    # Mongo stores naive datetimes as UTC; convert returned instant to epoch ms.
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)
def date_ok(s):
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        if d.isoformat() != s: raise ValueError
        return d
    except (ValueError, TypeError): raise HTTPException(422, "Date must use YYYY-MM-DD format")
def month_bounds(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must use YYYY-MM format")
    year, mon = map(int, month.split("-")); last = calendar.monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last:02d}"
def rnd(value, places=2):
    if value is None: return None
    q = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))
def shift_datetime(date_str, time_str, overnight_next=False):
    d = date_ok(date_str)
    h, m = map(int, time_str.split(":"))
    value = datetime(d.year, d.month, d.day, h, m)
    return value + (timedelta(days=1) if overnight_next else timedelta())
def employee(emp_code):
    doc = db.employees.find_one({"emp_code": emp_code})
    if not doc: raise HTTPException(404, "Employee not found")
    return doc
def serialize_record(doc):
    doc.pop("_id", None)
    for field in ("punch_in", "punch_out"):
        doc[field] = dt_ms(doc.get(field))
    for entry in doc.get("history", []):
        if "at_dt" in entry:
            entry["at"] = dt_ms(entry.pop("at_dt"))
        for _, change in entry.get("changes", {}).items():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime): change[side] = dt_ms(change[side])
    return doc
def late_minutes(punch_in, emp):
    start = shift_datetime(punch_in.date().isoformat(), emp["shift_start"])
    mins = int((punch_in - start).total_seconds() // 60)
    return mins if (punch_in - start).total_seconds() > 600 else 0
def overtime_minutes(punch_out, emp, date_str):
    start_t, end_t = emp["shift_start"], emp["shift_end"]
    overnight = end_t <= start_t
    end = shift_datetime(date_str, end_t, overnight_next=overnight)
    delta = int((punch_out - end).total_seconds() // 60)
    return delta if delta >= 30 else 0
def work_hours(punch_in, punch_out):
    if punch_in is None or punch_out is None: return None
    seconds = int((punch_out - punch_in).total_seconds())
    return rnd(seconds / 3600, 2)
def recompute(doc, emp):
    pi, po = doc.get("punch_in"), doc.get("punch_out")
    doc["late_minutes"] = late_minutes(pi, emp) if pi else 0
    doc["work_hours"] = work_hours(pi, po)
    doc["overtime_minutes"] = overtime_minutes(po, emp, doc["date"]) if po else 0
    doc["half_day"] = bool(doc["work_hours"] is not None and doc["work_hours"] < 4.5)
    return doc
def present_expr(): return {"$in": ["$status", list(PRESENCE)]}
def date_filter(month):
    first, last = month_bounds(month)
    return {"$gte": first, "$lte": last}
def working_days(joined_on, month):
    first, last = month_bounds(month)
    start = max(date_ok(first), date_ok(joined_on))
    end = date_ok(last)
    if start > end: return 0
    return sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday() < 5)
def count_present_day(log):
    if log.get("status") not in PRESENCE: return 0
    if date_ok(log["date"]).weekday() >= 5: return 0
    return 0.5 if log.get("half_day") else 1

# Models
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str
class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"
class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
class AttendanceCorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

@app.get("/health", tags=["system"])
def health():
    try: client.admin.command("ping"); return {"status": "ok"}
    except Exception: raise HTTPException(503, "Database connection failed")

@app.post("/employees", status_code=201, tags=["employees"])
def create_employee(body: EmployeeIn):
    date_ok(body.joined_on)
    if db.employees.find_one({"emp_code": body.emp_code}): raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump(); doc["created_at"] = now_ist().astimezone(timezone.utc).replace(tzinfo=None)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "emp_code already exists")
    doc["created_at"] = to_ms(doc["created_at"].replace(tzinfo=timezone.utc)); return doc

@app.get("/employees", tags=["employees"])
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department else {}
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size))
    for item in items:
        if isinstance(item.get("created_at"), datetime): item["created_at"] = dt_ms(item["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201, tags=["attendance"])
def punch_in(body: PunchInIn):
    emp = employee(body.emp_code)
    if body.status not in PRESENCE: raise HTTPException(422, "status must be PRESENT, WFH, or ON_DUTY")
    ts = from_ms(body.punched_at) if body.punched_at is not None else now_ist().replace(tzinfo=None)
    start, end = emp["shift_start"], emp["shift_end"]
    overnight = end <= start
    if overnight and ts.strftime("%H:%M") < end:
        attendance_date = (ts.date()-timedelta(days=1)).isoformat()
    else: attendance_date = ts.date().isoformat()
    # For overnight shifts, the stored punch time may be on the following calendar day.
    doc = {"emp_code": body.emp_code, "date": attendance_date, "status": body.status, "punch_in": ts,
           "punch_out": None, "work_hours": None, "late_minutes": 0, "overtime_minutes": 0,
           "half_day": False, "history": []}
    doc["late_minutes"] = late_minutes(ts, emp) if not (overnight and attendance_date != ts.date().isoformat()) else 0
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "Already punched in for this date")
    return serialize_record(doc)

@app.post("/attendance/punch-out", tags=["attendance"])
def punch_out(body: PunchOutIn):
    emp = employee(body.emp_code)
    ts = from_ms(body.punched_at) if body.punched_at is not None else now_ist().replace(tzinfo=None)
    # Locate same-day or previous-day overnight shift.
    dates = [ts.date().isoformat(), (ts.date()-timedelta(days=1)).isoformat()]
    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": {"$in": dates}}, sort=[("date", DESCENDING)])
    if not record: raise HTTPException(404, "Punch-in record not found for this date")
    if record.get("punch_out") is not None: raise HTTPException(409, "Already punched out for this date")
    if record.get("punch_in") is None or ts <= record["punch_in"]: raise HTTPException(422, "Punch-out must be after punch-in")
    if ts - record["punch_in"] > timedelta(hours=24): raise HTTPException(422, "Punch-out must be within 24 hours of punch-in")
    record["punch_out"] = ts; recompute(record, emp)
    db.attendance_logs.update_one({"_id": record["_id"]}, {"$set": {k: record[k] for k in ["punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"]}})
    return serialize_record(db.attendance_logs.find_one({"_id": record["_id"]}))

@app.get("/attendance", tags=["attendance"])
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if date_from or date_to:
        qd = {}
        if date_from: date_ok(date_from); qd["$gte"] = date_from
        if date_to: date_ok(date_to); qd["$lte"] = date_to
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from cannot be after date_to")
        q["date"] = qd
    if status:
        if status not in STATUSES: raise HTTPException(422, "Invalid status")
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    items = [serialize_record(x) for x in db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}", tags=["attendance"])
def correct_attendance(emp_code: str, date: str, body: AttendanceCorrectionIn):
    date_ok(date); emp = employee(emp_code)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record: raise HTTPException(404, "Attendance record not found")
    changes_in = body.model_dump(exclude_unset=True); reason = changes_in.pop("reason"); by = changes_in.pop("regularized_by")
    changes = {}
    for field, value in changes_in.items():
        if field in {"punch_in", "punch_out"}: changes[field] = from_ms(value) if value is not None else None
        elif field == "status":
            if value not in STATUSES: raise HTTPException(422, "Invalid status")
            changes[field] = value
    if not changes: raise HTTPException(422, "Provide a field to correct")
    if changes.get("status") in {"ABSENT", "LEAVE"}:
        if "punch_in" in changes or "punch_out" in changes: raise HTTPException(422, "ABSENT/LEAVE cannot include punch times")
        changes["punch_in"] = None; changes["punch_out"] = None
    final = dict(record); final.update(changes)
    if final.get("status") in PRESENCE and final.get("punch_in") is None: raise HTTPException(422, "Presence status requires punch_in")
    pi, po = final.get("punch_in"), final.get("punch_out")
    if pi and pi.date().isoformat() != date and not (emp["shift_end"] <= emp["shift_start"] and (pi.date()-timedelta(days=1)).isoformat() == date and pi.strftime("%H:%M") < emp["shift_end"]):
        raise HTTPException(422, "punch_in must belong to attendance date")
    if po and (pi is None or po <= pi or po-pi > timedelta(hours=24)): raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    recompute(final, emp)
    audit_fields = ["status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"]
    audit_changes = {}
    for field in audit_fields:
        before, after = record.get(field), final.get(field)
        if before != after: audit_changes[field] = {"from": before, "to": after}
    if not audit_changes: raise HTTPException(422, "No changes to save")
    entry = {"at": to_ms(now_ist()), "by": by, "reason": reason, "changes": audit_changes}
    # Conditional update prevents silently overwriting a concurrent correction.
    result = db.attendance_logs.update_one({"_id": record["_id"], "history": record.get("history", [])},
        {"$set": {k: final.get(k) for k in audit_fields}, "$push": {"history": entry}})
    if result.modified_count != 1: raise HTTPException(409, "Record changed concurrently; retry correction")
    return serialize_record(db.attendance_logs.find_one({"_id": record["_id"]}))

# Analytics: all month endpoints require month=YYYY-MM
@app.get("/analytics/employees/{emp_code}/monthly", tags=["analytics"])
def employee_monthly(emp_code: str, month: str):
    emp = employee(emp_code); first, last = month_bounds(month)
    logs = list(db.attendance_logs.find({"emp_code": emp_code, "date": date_filter(month)}))
    wd = working_days(emp["joined_on"], month)
    pdays = sum(count_present_day(x) for x in logs)
    leave = sum(x.get("status") == "LEAVE" for x in logs)
    late = [x for x in logs if x.get("late_minutes", 0) > 0]
    present_days = rnd(pdays)
    return {"emp_code": emp_code, "month": month, "working_days": wd, "present_days": present_days,
            "leave_days": int(leave), "late_count": len(late),
            "total_late_minutes": sum(x.get("late_minutes", 0) for x in logs),
            "total_overtime_minutes": sum(x.get("overtime_minutes", 0) for x in logs),
            "attendance_pct": rnd(pdays / wd * 100, 2) if wd else None}

@app.get("/analytics/departments/summary", tags=["analytics"])
def department_summary(month: str, department: Optional[str] = None):
    first, last = month_bounds(month)
    emp_q = {"joined_on": {"$lte": last}}
    if department: emp_q["department"] = department
    emps = list(db.employees.find(emp_q, {"_id": 0, "emp_code": 1, "department": 1}))
    by_dept = {}
    for e in emps: by_dept.setdefault(e["department"], []).append(e["emp_code"])
    items = []
    for dept, codes in sorted(by_dept.items()):
        logs = list(db.attendance_logs.find({"emp_code": {"$in": codes}, "date": date_filter(month)}))
        present_days = rnd(sum(count_present_day(x) for x in logs))
        present_hours = [x["work_hours"] for x in logs if x.get("status") in PRESENCE and x.get("work_hours") is not None]
        late_logs = [x for x in logs if x.get("late_minutes", 0) > 0]
        items.append({"department": dept, "headcount": len(codes), "present_days": present_days,
                      "avg_work_hours": rnd(sum(present_hours)/len(present_hours)) if present_hours else None,
                      "late_count": len(late_logs), "total_late_minutes": sum(x.get("late_minutes", 0) for x in late_logs),
                      "leave_count": sum(x.get("status") == "LEAVE" for x in logs),
                      "on_duty_count": sum(x.get("status") == "ON_DUTY" for x in logs)})
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late", tags=["analytics"])
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    date_range = date_filter(month)

    pipeline = [
        {
            "$match": {
                "date": date_range,
                "late_minutes": {"$gt": 0},
                "status": {"$nin": ["LEAVE", "ABSENT"]},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline += [
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ]

    return {
        "month": month,
        "items": list(db.attendance_logs.aggregate(pipeline)),
    }

@app.get("/analytics/departments/{department}/trend", tags=["analytics"])
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    start, end = date_ok(from_), date_ok(to)
    if end < start: raise HTTPException(422, "to must be on or after from")
    if (end-start).days+1 > 92: raise HTTPException(422, "Date range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}): raise HTTPException(404, "Unknown department")
    # Mongo aggregation densifies dates so missing calendar days are represented.
    pipeline = [
        {"$match": {"date": {"$gte": from_, "$lte": to}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"}, {"$match": {"emp.department": department}},
        {"$group": {"_id": "$date", "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": ["$half_day", 0.5, 1]}, 0]}},
                     "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}}},
        {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}},
        {"$set": {"date": {"$dateFromString": {"dateString": "$date"}}}},
        {"$densify": {"field": "date", "range": {"step": 1, "unit": "day", "bounds": [datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())]}}},
        {"$set": {"date": {"$dateToString": {"date": "$date", "format": "%Y-%m-%d"}}, "present_count": {"$ifNull": ["$present_count", 0]}, "late_count": {"$ifNull": ["$late_count", 0]}}},
        {"$sort": {"date": 1}},
    ]
    # Use aggregation for day filling; compute headcount/rates and rolling average over returned dense rows.
    rows = list(db.attendance_logs.aggregate(pipeline))
    items = []; rates = []
    for row in rows:
        d = date_ok(row["date"])
        headcount = db.employees.count_documents({"department": department, "joined_on": {"$lte": row["date"]}})
        working = d.weekday() < 5
        rate = rnd(row["present_count"] / headcount, 4) if working and headcount else None
        rates.append(rate)
        window = [x for x in rates[-7:] if x is not None]
        moving = rnd(sum(window)/len(window), 4) if window else None
        items.append({"date": row["date"], "is_working_day": working, "headcount": headcount,
                      "present_count": row["present_count"], "late_count": row["late_count"],
                      "attendance_rate": rate, "moving_avg_7d": moving})
    # Guarantee one row per requested day, including when a day (or the whole range) has no logs.
    by_date = {item["date"]: item for item in items}
    dense_items = []
    cursor_day = start
    while cursor_day <= end:
        key = cursor_day.isoformat()
        if key in by_date:
            dense_items.append(by_date[key])
        else:
            headcount = db.employees.count_documents({"department": department, "joined_on": {"$lte": key}})
            working = cursor_day.weekday() < 5
            rate = 0.0 if working and headcount else None
            prior_rates = [x["attendance_rate"] for x in dense_items[-6:] if x["attendance_rate"] is not None]
            moving_values = prior_rates + ([rate] if rate is not None else [])
            moving = rnd(sum(moving_values) / len(moving_values), 4) if moving_values else None
            dense_items.append({"date": key, "is_working_day": working, "headcount": headcount, "present_count": 0, "late_count": 0, "attendance_rate": rate, "moving_avg_7d": moving})
        cursor_day += timedelta(days=1)
    # Recalculate rolling means consistently across all rows.
    for i, item in enumerate(dense_items):
        vals = [x["attendance_rate"] for x in dense_items[max(0, i-6):i+1] if x["attendance_rate"] is not None]
        item["moving_avg_7d"] = rnd(sum(vals) / len(vals), 4) if vals else None
    return {"department": department, "items": dense_items}

@app.get("/admin/explain/{endpoint}", tags=["admin"])
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
                     department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                     date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}:
        raise HTTPException(422, "Unsupported endpoint")
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            qd = {}
            if date_from: date_ok(date_from); qd["$gte"] = date_from
            if date_to: date_ok(date_to); qd["$lte"] = date_to
            q["date"] = qd
        if status:
            if status not in STATUSES: raise HTTPException(422, "Invalid status")
            q["status"] = status
        cmd = {"find": "attendance_logs", "filter": q, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}
        collection = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "emp_code and month are required")
        month_bounds(month); q = {"emp_code": emp_code, "date": date_filter(month)}
        cmd = {"find": "attendance_logs", "filter": q, "sort": {"date": 1}}
        collection = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "month is required")
        first, last = month_bounds(month); q = {"date": {"$gte": first, "$lte": last}}
        cmd = {"aggregate": "attendance_logs", "pipeline": [{"$match": q}, {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}}], "cursor": {}}
        collection = "attendance_logs"
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "month is required")
        month_bounds(month); pipe = [{"$match": {"date": date_filter(month), "late_minutes": {"$gt": 0}}}, {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}}, {"$sort": {"total_late_minutes": -1, "_id": 1}}]
        cmd = {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}}; collection = "attendance_logs"
    else:
        if not department or not from_ or not to: raise HTTPException(422, "department, from and to are required")
        date_ok(from_); date_ok(to)
        if date_ok(to) < date_ok(from_) or (date_ok(to)-date_ok(from_)).days+1 > 92: raise HTTPException(422, "Invalid trend date range")
        cmd = {"aggregate": "attendance_logs", "pipeline": [{"$match": {"date": {"$gte": from_, "$lte": to}}}, {"$sort": {"date": 1}}], "cursor": {}}
        collection = "attendance_logs"
    try:
        result = db.command({"explain": cmd, "verbosity": "executionStats"})
    except Exception as exc:
        raise HTTPException(422, f"Could not explain query: {str(exc)}")
    return {"endpoint": endpoint, "collection": collection, "explain": result}
