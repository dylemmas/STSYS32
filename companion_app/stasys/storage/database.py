"""SQLite database schema and connection management for STASYS session data."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    firmware_session_id   INTEGER,
    started_at            REAL    NOT NULL,
    ended_at              REAL,
    firmware_version      TEXT,
    hw_revision           INTEGER,
    build_timestamp       INTEGER,
    battery_start         INTEGER,
    battery_end           INTEGER,
    shot_count            INTEGER DEFAULT 0,
    note                  TEXT
);

CREATE TABLE IF NOT EXISTS shot_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL,
    timestamp_us   INTEGER NOT NULL,
    shot_number    INTEGER NOT NULL,
    piezo_peak     INTEGER NOT NULL,
    accel_x        INTEGER NOT NULL,
    accel_y        INTEGER NOT NULL,
    accel_z        INTEGER NOT NULL,
    gyro_x         INTEGER NOT NULL,
    gyro_y         INTEGER NOT NULL,
    gyro_z         INTEGER NOT NULL,
    recoil_axis    INTEGER NOT NULL,
    recoil_sign    INTEGER NOT NULL,
    received_at    REAL    NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS imu_samples (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL,
    timestamp_us   INTEGER NOT NULL,
    accel_x        INTEGER NOT NULL,
    accel_y        INTEGER NOT NULL,
    accel_z        INTEGER NOT NULL,
    gyro_x         INTEGER NOT NULL,
    gyro_y         INTEGER NOT NULL,
    gyro_z         INTEGER NOT NULL,
    piezo          INTEGER NOT NULL,
    temp           INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shot_events_session
    ON shot_events(session_id);

CREATE INDEX IF NOT EXISTS idx_imu_samples_session
    ON imu_samples(session_id);
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns to existing tables (safe to call repeatedly)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(sessions)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    migrations = [
        ("firmware_session_id", "INTEGER"),
        ("hw_revision",          "INTEGER"),
        ("build_timestamp",      "INTEGER"),
        ("battery_start",        "INTEGER"),
        ("battery_end",          "INTEGER"),
        ("shot_count",           "INTEGER DEFAULT 0"),
        ("note",                 "TEXT"),
    ]
    for col_name, col_type in migrations:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass  # Already exists (race condition or partial previous run)
    conn.commit()


def create_database(path: Path | str) -> sqlite3.Connection:
    """Create a SQLite connection, initialise the schema, and return it."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


@contextmanager
def open_database(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite database and yield a connection.

    Creates the schema automatically if the file does not exist.
    Commits on success, rolls back on error.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
