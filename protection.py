import json
import sqlite3
from datetime import datetime, time
from pathlib import Path

import psutil


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "timetracker.db"
PROTECTION_PATH = BASE_DIR / "protection.json"


DEFAULT_CONFIG = {
    "enabled": True,
    "apps": {}
}


def load_config():
    if not PROTECTION_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with PROTECTION_PATH.open(
            "r",
            encoding="utf-8"
        ) as file:
            config = json.load(file)

        if not isinstance(config, dict):
            return DEFAULT_CONFIG.copy()

        config.setdefault("enabled", True)
        config.setdefault("apps", {})

        return config

    except (
        OSError,
        json.JSONDecodeError
    ):
        return DEFAULT_CONFIG.copy()


def save_config(config):
    with PROTECTION_PATH.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            config,
            file,
            indent=4
        )


def get_today_usage(process_name):
    """
    Return today's tracked seconds for one process.

    Sessions are clipped to today's boundaries so a session
    crossing midnight cannot accidentally count twice.
    """

    today = datetime.now().date()

    day_start = datetime.combine(
        today,
        time.min
    )

    day_end = datetime.combine(
        today,
        time.max
    )

    connection = sqlite3.connect(
        DB_PATH
    )

    rows = connection.execute(
        """
        SELECT started_at, ended_at
        FROM sessions
        WHERE process_name = ?
          AND started_at < ?
          AND ended_at > ?
        """,
        (
            process_name,
            day_end.isoformat(),
            day_start.isoformat(),
        )
    ).fetchall()

    connection.close()

    total = 0.0

    for started_text, ended_text in rows:
        try:
            started = datetime.fromisoformat(
                started_text
            )

            ended = datetime.fromisoformat(
                ended_text
            )

        except ValueError:
            continue

        overlap_start = max(
            started,
            day_start
        )

        overlap_end = min(
            ended,
            day_end
        )

        if overlap_start >= overlap_end:
            continue

        total += (
            overlap_end -
            overlap_start
        ).total_seconds()

    return total


def get_limit_seconds(process_name):
    config = load_config()

    if not config.get("enabled", True):
        return None

    app_config = config.get(
        "apps",
        {}
    ).get(process_name)

    if not isinstance(
        app_config,
        dict
    ):
        return None

    if not app_config.get(
        "enabled",
        False
    ):
        return None

    try:
        minutes = float(
            app_config.get(
                "limit_minutes",
                0
            )
        )
    except (
        TypeError,
        ValueError
    ):
        return None

    if minutes <= 0:
        return None

    return minutes * 60


def is_limit_reached(process_name):
    limit_seconds = get_limit_seconds(
        process_name
    )

    if limit_seconds is None:
        return False

    usage_seconds = get_today_usage(
        process_name
    )

    return usage_seconds >= limit_seconds


def close_process(process_name):
    """
    Close every running process with the requested executable name.
    """

    closed_any = False

    for process in psutil.process_iter(
        ["pid", "name"]
    ):
        try:
            name = process.info["name"]

            if (
                name and
                name.lower() ==
                process_name.lower()
            ):
                process.terminate()
                closed_any = True

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied
        ):
            continue

    return closed_any


def check_and_enforce(process_name):
    """
    Check one foreground process.

    Returns True if the process was blocked.
    """

    if not process_name:
        return False

    if not is_limit_reached(
        process_name
    ):
        return False

    closed = close_process(
        process_name
    )

    if closed:
        limit_seconds = get_limit_seconds(
            process_name
        )

        usage_seconds = get_today_usage(
            process_name
        )

        print(
            f"🛡️ Protection: "
            f"{process_name} reached "
            f"{limit_seconds / 60:.0f} min "
            f"daily limit "
            f"({usage_seconds / 60:.1f} min). "
            f"App closed."
        )

    return closed