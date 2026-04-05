"""Session-level relational storage for shot events and IMU samples."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from stasys.protocol.packets import DataRawSample, EvtShotDetected
from stasys.storage.database import create_database


class SessionStore:
    """Relational storage for session metadata, shot events, and IMU samples.

    Wraps a SQLite database and exposes a flat API for opening/closing sessions
    and recording individual packets.
    """

    def __init__(self, db_path: str = "stasys.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

        if db_path == ":memory:":
            self._conn = create_database(db_path)
        else:
            self._conn = create_database(db_path)

    @property
    def _db_conn(self) -> sqlite3.Connection:
        """Return the active connection."""
        assert self._conn is not None
        return self._conn

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        """Execute a statement on the active connection with auto-commit."""
        self._db_conn.execute(sql, params)
        self._db_conn.commit()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a query and return results as a list of dicts."""
        rows = self._db_conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    def open_session(
        self,
        firmware_session_id: int = 0,
        firmware_version: str = "",
        hw_revision: int = 0,
        build_timestamp: int = 0,
        battery_start: int = 0,
    ) -> int:
        """Open a new session and return its integer id.

        Args:
            firmware_session_id: Session ID from the ESP32 firmware.
            firmware_version: Firmware version string.
            hw_revision: Hardware revision byte.
            build_timestamp: Firmware build timestamp.
            battery_start: Battery percentage at session start.

        Returns:
            The newly assigned session id (rowid).
        """
        cursor = self._db_conn.execute(
            "INSERT INTO sessions "
            "(firmware_session_id, started_at, firmware_version, hw_revision, "
            "build_timestamp, battery_start) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (firmware_session_id, time.time(), firmware_version, hw_revision,
             build_timestamp, battery_start),
        )
        self._db_conn.commit()
        return cursor.lastrowid

    def close_session(self, session_id: int | None = None) -> None:
        """Close a session by setting its ended_at timestamp.

        Args:
            session_id: Session id to close. If None, this is a no-op.
        """
        if session_id is not None:
            self._execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), session_id),
            )

    def update_shot_count(self, session_id: int, shot_count: int) -> None:
        """Update the shot count for a session."""
        self._execute(
            "UPDATE sessions SET shot_count = ? WHERE id = ?",
            (shot_count, session_id),
        )

    def update_battery_end(self, session_id: int, battery_end: int) -> None:
        """Update the battery level at session end."""
        self._execute(
            "UPDATE sessions SET battery_end = ? WHERE id = ?",
            (battery_end, session_id),
        )

    # -------------------------------------------------------------------------
    # Recording
    # -------------------------------------------------------------------------

    def record_shot(self, session_id: int, packet: EvtShotDetected) -> None:
        """Insert a shot event record into the database.

        Args:
            session_id: The session this shot belongs to.
            packet: The parsed EVT_SHOT_DETECTED packet.
        """
        self._execute(
            "INSERT INTO shot_events "
            "(session_id, timestamp_us, shot_number, piezo_peak, "
            "accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, "
            "recoil_axis, recoil_sign, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                packet.timestamp_us,
                packet.shot_number,
                packet.piezo_peak,
                packet.accel_x_peak,
                packet.accel_y_peak,
                packet.accel_z_peak,
                packet.gyro_x_peak,
                packet.gyro_y_peak,
                packet.gyro_z_peak,
                packet.recoil_axis,
                packet.recoil_sign,
                time.time(),
            ),
        )

    def record_imu(self, session_id: int, packet: DataRawSample) -> None:
        """Insert a single IMU sample into the database.

        Args:
            session_id: The session this sample belongs to.
            packet: The parsed DATA_RAW_SAMPLE packet.
        """
        self._execute(
            "INSERT INTO imu_samples "
            "(session_id, timestamp_us, accel_x, accel_y, accel_z, "
            "gyro_x, gyro_y, gyro_z, piezo, temp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                packet.timestamp_us,
                packet.accel_x,
                packet.accel_y,
                packet.accel_z,
                packet.gyro_x,
                packet.gyro_y,
                packet.gyro_z,
                packet.piezo,
                packet.temperature,
            ),
        )

    def record_imu_batch(self, session_id: int, packets: list[DataRawSample]) -> None:
        """Insert multiple IMU samples in a single database transaction.

        Uses executemany() with one commit for the entire batch, which is
        dramatically faster than calling record_imu() per-sample.

        Args:
            session_id: The session these samples belong to.
            packets: List of parsed DATA_RAW_SAMPLE packets.
        """
        if not packets:
            return
        rows = [
            (
                session_id,
                p.timestamp_us,
                p.accel_x,
                p.accel_y,
                p.accel_z,
                p.gyro_x,
                p.gyro_y,
                p.gyro_z,
                p.piezo,
                p.temperature,
            )
            for p in packets
        ]
        self._db_conn.executemany(
            "INSERT INTO imu_samples "
            "(session_id, timestamp_us, accel_x, accel_y, accel_z, "
            "gyro_x, gyro_y, gyro_z, piezo, temp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._db_conn.commit()

    # -------------------------------------------------------------------------
    # Retrieval
    # -------------------------------------------------------------------------

    def get_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions ordered by started_at descending."""
        return self._query(
            "SELECT id, firmware_session_id, started_at, ended_at, firmware_version, "
            "hw_revision, build_timestamp, battery_start, battery_end, shot_count, note "
            "FROM sessions ORDER BY started_at DESC"
        )

    def get_shots(self, session_id: int) -> list[dict[str, Any]]:
        """Return all shot events for a session, ordered by timestamp_us."""
        return self._query(
            "SELECT id, session_id, timestamp_us, shot_number, piezo_peak, "
            "accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, "
            "recoil_axis, recoil_sign, received_at "
            "FROM shot_events WHERE session_id = ? ORDER BY timestamp_us",
            (session_id,),
        )

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        """Return a single session by id."""
        rows = self._query(
            "SELECT id, firmware_session_id, started_at, ended_at, firmware_version, "
            "hw_revision, build_timestamp, battery_start, battery_end, shot_count, note "
            "FROM sessions WHERE id = ?",
            (session_id,),
        )
        return rows[0] if rows else None
