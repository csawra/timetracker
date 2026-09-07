import time
import ctypes
import sqlite3
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "timetracker.db"


# --------------------------------------------------
# Windows API
# --------------------------------------------------

user32 = ctypes.windll.user32

user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int,
]

user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]


# --------------------------------------------------
# Settings
# --------------------------------------------------

CHECKPOINT_SECONDS = 10


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
    "explorer.exe": "Windows Explorer",
}


# --------------------------------------------------
# Database
# --------------------------------------------------

def setup_database():
    connection = sqlite3.connect(DB_PATH)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_name TEXT NOT NULL,
            window_title TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def save_session(
    process_name,
    window_title,
    started_at,
    ended_at,
):
    if not process_name:
        return

    if process_name in IGNORED_PROCESSES:
        return

    duration = (ended_at - started_at).total_seconds()

    if duration <= 0:
        return

    connection = sqlite3.connect(DB_PATH)

    connection.execute(
        """
        INSERT INTO sessions (
            process_name,
            window_title,
            started_at,
            ended_at,
            duration_seconds
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            process_name,
            window_title or "",
            started_at.isoformat(),
            ended_at.isoformat(),
            duration,
        ),
    )

    connection.commit()
    connection.close()

    print(
        f"Saved: {get_app_name(process_name)} "
        f"→ {duration:.1f}s"
    )


# --------------------------------------------------
# Application information
# --------------------------------------------------

def get_app_name(process_name):
    if not process_name:
        return "Unknown"

    if process_name in IGNORED_PROCESSES:
        return "Unknown"

    return APP_NAMES.get(
        process_name,
        process_name.removesuffix(".exe"),
    )


# --------------------------------------------------
# Active application
# --------------------------------------------------

def get_active_app():
    hwnd = user32.GetForegroundWindow()

    if not hwnd:
        return None, None

    length = user32.GetWindowTextLengthW(hwnd)

    title = ""

    if length > 0:
        title_buffer = ctypes.create_unicode_buffer(
            length + 1
        )

        user32.GetWindowTextW(
            hwnd,
            title_buffer,
            length + 1,
        )

        title = title_buffer.value

    process_id = wintypes.DWORD()

    user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(process_id),
    )

    if not process_id.value:
        return None, None

    try:
        process = psutil.Process(process_id.value)
        process_name = process.name()

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
    ):
        return None, None

    if process_name in IGNORED_PROCESSES:
        return None, None

    return process_name, title


# --------------------------------------------------
# Startup
# --------------------------------------------------

setup_database()

current_process, current_window = get_active_app()
session_started = datetime.now()
last_checkpoint = session_started

print("TimeTracker started.")
print(f"Active app: {get_app_name(current_process)}")
print(f"Window: {current_window}")
print(f"Database: {DB_PATH}")
print()


# --------------------------------------------------
# Main loop
# --------------------------------------------------

try:
    while True:
        time.sleep(1)

        now_time = datetime.now()

        new_process, new_window = get_active_app()

        # ------------------------------------------
        # App changed
        # ------------------------------------------

        if new_process != current_process:

            session_ended = now_time

            save_session(
                current_process,
                current_window,
                session_started,
                session_ended,
            )

            if new_process:
                print(
                    f"[{session_ended.strftime('%H:%M:%S')}] "
                    f"Now using {get_app_name(new_process)}"
                )

            current_process = new_process
            current_window = new_window
            session_started = session_ended
            last_checkpoint = session_ended

        # ------------------------------------------
        # Periodic checkpoint
        # ------------------------------------------

        elif (
            current_process
            and (now_time - last_checkpoint).total_seconds()
            >= CHECKPOINT_SECONDS
        ):

            save_session(
                current_process,
                current_window,
                session_started,
                now_time,
            )

            session_started = now_time
            last_checkpoint = now_time

        # ------------------------------------------
        # Initial live save
        # ------------------------------------------

        elif (
            current_process
            and last_checkpoint == session_started
            and (now_time - session_started).total_seconds() >= 1
        ):

            save_session(
                current_process,
                current_window,
                session_started,
                now_time,
            )

            session_started = now_time
            last_checkpoint = now_time


# --------------------------------------------------
# Shutdown
# --------------------------------------------------

except KeyboardInterrupt:

    session_ended = datetime.now()

    save_session(
        current_process,
        current_window,
        session_started,
        session_ended,
    )

    print()
    print("TimeTracker stopped.")