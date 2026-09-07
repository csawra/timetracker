from pathlib import Path
from datetime import datetime, date, timedelta
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "timetracker.db"
STATIC_DIR = BASE_DIR / "static"


# ============================================================
# App
# ============================================================

app = FastAPI()


# ============================================================
# Configuration
# ============================================================

IGNORED_PROCESSES = {
    "SearchHost.exe",
    "StartMenuExperienceHost.exe",
    "ShellExperienceHost.exe",
}


APP_NAMES = {
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "chrome.exe": "Google Chrome",
    "Code.exe": "Visual Studio Code",
    "notepad.exe": "Notepad",
    "Notepad.exe": "Notepad",
    "Telegram.exe": "Telegram",
    "Taskmgr.exe": "Task Manager",
    "explorer.exe": "Windows Explorer",
}


# ============================================================
# Database
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_session_columns():
    conn = get_connection()

    rows = conn.execute(
        "PRAGMA table_info(sessions)"
    ).fetchall()

    conn.close()

    return [row["name"] for row in rows]


def get_process_column():
    """
    Find the column containing the executable/process name.

    We intentionally do NOT assume that the database has a
    column called 'process'.
    """

    columns = get_session_columns()

    preferred = [
        "process_name",
        "process",
        "exe",
        "executable",
        "app",
        "app_name",
        "process_exe",
        "executable_name",
        "program",
        "name",
    ]

    for candidate in preferred:
        if candidate in columns:
            return candidate

    excluded = {
        "id",
        "started_at",
        "ended_at",
        "start_time",
        "end_time",
        "window_title",
        "title",
    }

    possible = [
        column
        for column in columns
        if column not in excluded
    ]

    if len(possible) == 1:
        return possible[0]

    raise RuntimeError(
        "Could not determine the process column. "
        f"Sessions columns: {columns}"
    )


def quote_identifier(identifier):
    """
    Safely quote a SQLite identifier.
    """
    return '"' + identifier.replace('"', '""') + '"'


# ============================================================
# Helpers
# ============================================================

def parse_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date. Use YYYY-MM-DD.",
        )


def format_seconds(seconds):
    return round(float(seconds), 1)


def friendly_app_name(process):
    return APP_NAMES.get(process, process)


def get_week_start(target_date):
    return target_date - timedelta(
        days=target_date.weekday()
    )


def get_month_start(target_date):
    return date(
        target_date.year,
        target_date.month,
        1,
    )


def get_next_month_start(target_date):
    if target_date.month == 12:
        return date(
            target_date.year + 1,
            1,
            1,
        )

    return date(
        target_date.year,
        target_date.month + 1,
        1,
    )


# ============================================================
# Session loading
# ============================================================

def get_sessions_between(start_date, end_date):
    """
    Return sessions that overlap the requested date range.

    This is important because a session can cross midnight.
    """

    start_datetime = datetime.combine(
        start_date,
        datetime.min.time(),
    )

    end_datetime = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
    )

    process_column = get_process_column()
    process_sql = quote_identifier(process_column)

    conn = get_connection()

    rows = conn.execute(
        f"""
        SELECT
            {process_sql} AS process_name,
            started_at,
            ended_at
        FROM sessions
        WHERE started_at < ?
          AND ended_at > ?
        ORDER BY started_at
        """,
        (
            end_datetime.isoformat(),
            start_datetime.isoformat(),
        ),
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# Time calculation
# ============================================================

def calculate_overlap_seconds(
    started,
    ended,
    range_start,
    range_end,
):
    overlap_start = max(
        started,
        range_start,
    )

    overlap_end = min(
        ended,
        range_end,
    )

    if overlap_start >= overlap_end:
        return 0

    return (
        overlap_end - overlap_start
    ).total_seconds()


def get_daily_totals(start_date, end_date):
    results = []

    for offset in range(
        (end_date - start_date).days + 1
    ):
        target_date = (
            start_date +
            timedelta(days=offset)
        )

        day_start = datetime.combine(
            target_date,
            datetime.min.time(),
        )

        day_end = day_start + timedelta(days=1)

        total = 0

        rows = get_sessions_between(
            target_date,
            target_date,
        )

        for row in rows:
            started = datetime.fromisoformat(
                row["started_at"]
            )

            ended = datetime.fromisoformat(
                row["ended_at"]
            )

            total += calculate_overlap_seconds(
                started,
                ended,
                day_start,
                day_end,
            )

        results.append({
            "date": target_date.isoformat(),
            "seconds": format_seconds(total),
        })

    return results


def get_app_totals(start_date, end_date):
    totals = {}

    rows = get_sessions_between(
        start_date,
        end_date,
    )

    range_start = datetime.combine(
        start_date,
        datetime.min.time(),
    )

    range_end = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
    )

    for row in rows:
        process = row["process_name"]

        if process in IGNORED_PROCESSES:
            continue

        started = datetime.fromisoformat(
            row["started_at"]
        )

        ended = datetime.fromisoformat(
            row["ended_at"]
        )

        seconds = calculate_overlap_seconds(
            started,
            ended,
            range_start,
            range_end,
        )

        if seconds <= 0:
            continue

        if process not in totals:
            totals[process] = 0

        totals[process] += seconds

    results = []

    for process, seconds in sorted(
        totals.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        results.append({
            "app": friendly_app_name(process),
            "process": process,
            "seconds": format_seconds(seconds),
        })

    return results


# ============================================================
# Hourly totals
# ============================================================

def get_hourly_totals(target_date):
    hourly = {
        hour: 0
        for hour in range(24)
    }

    day_start = datetime.combine(
        target_date,
        datetime.min.time(),
    )

    day_end = day_start + timedelta(days=1)

    rows = get_sessions_between(
        target_date,
        target_date,
    )

    for row in rows:
        started = datetime.fromisoformat(
            row["started_at"]
        )

        ended = datetime.fromisoformat(
            row["ended_at"]
        )

        overlap_start = max(
            started,
            day_start,
        )

        overlap_end = min(
            ended,
            day_end,
        )

        if overlap_start >= overlap_end:
            continue

        current = overlap_start

        while current < overlap_end:
            next_hour = (
                current.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                + timedelta(hours=1)
            )

            segment_end = min(
                next_hour,
                overlap_end,
            )

            seconds = (
                segment_end - current
            ).total_seconds()

            hourly[current.hour] += seconds

            current = segment_end

    return [
        {
            "hour": hour,
            "seconds": format_seconds(seconds),
        }
        for hour, seconds in hourly.items()
    ]


# ============================================================
# Weekly totals
# ============================================================

def get_weekly_totals(start_date, end_date):
    daily = get_daily_totals(
        start_date,
        end_date,
    )

    weekly = {}

    for item in daily:
        current_date = date.fromisoformat(
            item["date"]
        )

        week_start = get_week_start(
            current_date
        )

        week_key = week_start.isoformat()

        if week_key not in weekly:
            weekly[week_key] = 0

        weekly[week_key] += item["seconds"]

    results = []

    for week_start_string, seconds in sorted(
        weekly.items()
    ):
        week_start = date.fromisoformat(
            week_start_string
        )

        week_end = (
            week_start +
            timedelta(days=6)
        )

        results.append({
            "start": week_start.isoformat(),
            "end": week_end.isoformat(),
            "seconds": format_seconds(seconds),
        })

    return results


# ============================================================
# Monthly totals
# ============================================================

def get_monthly_totals(start_date, end_date):
    daily = get_daily_totals(
        start_date,
        end_date,
    )

    monthly = {}

    for item in daily:
        current_date = date.fromisoformat(
            item["date"]
        )

        month_key = (
            current_date.year,
            current_date.month,
        )

        if month_key not in monthly:
            monthly[month_key] = 0

        monthly[month_key] += item["seconds"]

    results = []

    for (year, month), seconds in sorted(
        monthly.items()
    ):
        month_start = date(
            year,
            month,
            1,
        )

        results.append({
            "start": month_start.isoformat(),
            "seconds": format_seconds(seconds),
        })

    return results


# ============================================================
# API — metadata
# ============================================================

@app.get("/api/meta")
def get_meta():
    conn = get_connection()

    row = conn.execute(
        """
        SELECT MIN(started_at) AS first_started
        FROM sessions
        """
    ).fetchone()

    conn.close()

    today = date.today()

    if row["first_started"]:
        first_started = datetime.fromisoformat(
            row["first_started"]
        )

        first_date = first_started.date()
    else:
        first_date = today

    return {
        "first_date": first_date.isoformat(),
        "today": today.isoformat(),
    }


# ============================================================
# API — apps today
# ============================================================

@app.get("/api/apps")
def api_apps():
    today = date.today()

    return get_app_totals(
        today,
        today,
    )


@app.get("/api/apps/today")
def api_apps_today():
    today = date.today()

    return get_app_totals(
        today,
        today,
    )


# ============================================================
# API — day
# ============================================================

@app.get("/api/day")
def api_day(date_string: str):
    target_date = parse_date(
        date_string
    )

    daily = get_daily_totals(
        target_date,
        target_date,
    )

    total_seconds = daily[0]["seconds"]

    return {
        "date": target_date.isoformat(),
        "total_seconds": format_seconds(
            total_seconds
        ),
        "apps": get_app_totals(
            target_date,
            target_date,
        ),
        "hourly": get_hourly_totals(
            target_date
        ),
    }


# ============================================================
# API — week
# ============================================================

@app.get("/api/week")
def api_week(date_string: str):
    selected_date = parse_date(
        date_string
    )

    week_start = get_week_start(
        selected_date
    )

    week_end = (
        week_start +
        timedelta(days=6)
    )

    daily = get_daily_totals(
        week_start,
        week_end,
    )

    total_seconds = sum(
        item["seconds"]
        for item in daily
    )

    return {
        "start": week_start.isoformat(),
        "end": week_end.isoformat(),
        "total_seconds": format_seconds(
            total_seconds
        ),
        "daily": daily,
        "apps": get_app_totals(
            week_start,
            week_end,
        ),
    }


# ============================================================
# API — month
# ============================================================

@app.get("/api/month")
def api_month(date_string: str):
    selected_date = parse_date(
        date_string
    )

    month_start = get_month_start(
        selected_date
    )

    month_end = (
        get_next_month_start(
            month_start
        )
        - timedelta(days=1)
    )

    daily = get_daily_totals(
        month_start,
        month_end,
    )

    total_seconds = sum(
        item["seconds"]
        for item in daily
    )

    return {
        "start": month_start.isoformat(),
        "end": month_end.isoformat(),
        "total_seconds": format_seconds(
            total_seconds
        ),
        "daily": daily,
        "apps": get_app_totals(
            month_start,
            month_end,
        ),
    }


# ============================================================
# API — arbitrary history
# ============================================================

@app.get("/api/history")
def get_history(start: str, end: str):
    start_date = parse_date(start)
    end_date = parse_date(end)

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="End date cannot be before start date.",
        )

    daily = get_daily_totals(
        start_date,
        end_date,
    )

    weekly = get_weekly_totals(
        start_date,
        end_date,
    )

    monthly = get_monthly_totals(
        start_date,
        end_date,
    )

    apps = get_app_totals(
        start_date,
        end_date,
    )

    total_seconds = sum(
        item["seconds"]
        for item in daily
    )

    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "total_seconds": format_seconds(
            total_seconds
        ),
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "apps": apps,
    }


# ============================================================
# Dashboard
# ============================================================

@app.get("/dashboard")
def dashboard():
    return FileResponse(
        STATIC_DIR / "index.html"
    )