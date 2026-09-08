import time
import ctypes
import sqlite3
from ctypes import wintypes
from datetime import datetime, timedelta
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


# Used to detect keyboard/mouse inactivity.
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


user32.GetLastInputInfo.argtypes = [
    ctypes.POINTER(LASTINPUTINFO)
]

user32.GetLastInputInfo.restype = wintypes.BOOL


# --------------------------------------------------
# Settings
# --------------------------------------------------

CHECKPOINT_SECONDS = 10

# If there has been no keyboard/mouse input for this long,
# the user is considered AFK and time stops being tracked.
AFK_SECONDS = 120


# Windows/system processes that should never count as
# meaningful application usage.
IGNORED_PROCESSES = {
    # Windows shell / desktop
    "explorer.exe",
    "SearchHost.exe",
    "StartMenuExperienceHost.exe",
    "ShellExperienceHost.exe",

    # Windows system UI
    "Taskmgr.exe",
    "SnippingTool.exe",
    "ScreenClippingHost.exe",
    "TextInputHost.exe",
    "LockApp.exe",
    "SearchApp.exe",
    "ApplicationFrameHost.exe",

    # Windows background/UI infrastructure
    "RuntimeBroker.exe",
    "sihost.exe",
    "dwm.exe",
    "ctfmon.exe",
    "conhost.exe",
    "dllhost.exe",
}


APP_NAMES = {
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "chrome.exe": "Google Chrome",
    "Code.exe": "Visual Studio Code",
    "notepad.exe": "Notepad",

    # Useful built-in apps stay trackable.
    "mspaint.exe": "Paint",
    "PaintStudio.View.exe": "Paint",
    "CalculatorApp.exe": "Calculator",
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

    if not started_at or not ended_at:
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
# AFK detection
# --------------------------------------------------

def get_idle_seconds():
    """
    Return the number of seconds since the user's last
    keyboard or mouse input.

    Windows gives us the last-input tick count, which is
    independent of which application currently has focus.
    """

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)

    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0

    current_tick = ctypes.windll.kernel32.GetTickCount()

    elapsed_ms = (current_tick - info.dwTime) & 0xFFFFFFFF

    return elapsed_ms / 1000.0


def is_user_afk():
    return get_idle_seconds() >= AFK_SECONDS


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

# If the computer is already AFK when the tracker starts,
# don't immediately start counting the foreground app.
currently_afk = is_user_afk()

if currently_afk:
    current_process = None
    current_window = None

session_started = (
    datetime.now()
    if current_process
    else None
)

last_checkpoint = session_started


print("TimeTracker started.")
print(f"Active app: {get_app_name(current_process)}")
print(f"Window: {current_window}")
print(f"AFK threshold: {AFK_SECONDS}s")
print(f"Database: {DB_PATH}")
print()

if currently_afk:
    print("Currently AFK — tracking paused until you return.")
    print()


# --------------------------------------------------
# Main loop
# --------------------------------------------------

try:
    while True:
        time.sleep(1)

        now_time = datetime.now()

        idle_seconds = get_idle_seconds()
        user_is_afk = idle_seconds >= AFK_SECONDS

        # ------------------------------------------
        # User became AFK
        # ------------------------------------------

        if user_is_afk and not currently_afk:

            currently_afk = True

            # The user's last real interaction happened
            # idle_seconds ago. Don't count the AFK period.
            last_active_time = (
                now_time
                - timedelta(seconds=idle_seconds)
            )

            if current_process and session_started:
                save_session(
                    current_process,
                    current_window,
                    session_started,
                    last_active_time,
                )

            print(
                f"[{now_time.strftime('%H:%M:%S')}] "
                f"AFK detected — tracking paused."
            )

            current_process = None
            current_window = None
            session_started = None
            last_checkpoint = None

            continue


        # ------------------------------------------
        # User is still AFK
        # ------------------------------------------

        if user_is_afk:
            continue


        # ------------------------------------------
        # User returned from AFK
        # ------------------------------------------

        if currently_afk:

            currently_afk = False

            new_process, new_window = get_active_app()

            current_process = new_process
            current_window = new_window

            if current_process:
                session_started = now_time
                last_checkpoint = now_time

                print(
                    f"[{now_time.strftime('%H:%M:%S')}] "
                    f"Welcome back — tracking "
                    f"{get_app_name(current_process)}"
                )

            else:
                session_started = None
                last_checkpoint = None

            continue


        # ------------------------------------------
        # Normal tracking
        # ------------------------------------------

        new_process, new_window = get_active_app()


        # ------------------------------------------
        # App changed
        # ------------------------------------------

        if new_process != current_process:

            session_ended = now_time

            if current_process and session_started:
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

            if new_process:
                session_started = session_ended
                last_checkpoint = session_ended
            else:
                session_started = None
                last_checkpoint = None


        # ------------------------------------------
        # Periodic checkpoint
        # ------------------------------------------

        elif (
            current_process
            and session_started
            and last_checkpoint
            and (
                now_time - last_checkpoint
            ).total_seconds() >= CHECKPOINT_SECONDS
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
            and session_started
            and last_checkpoint == session_started
            and (
                now_time - session_started
            ).total_seconds() >= 1
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

    if current_process and session_started and not currently_afk:
        save_session(
            current_process,
            current_window,
            session_started,
            session_ended,
        )

    print()
    print("TimeTracker stopped.")