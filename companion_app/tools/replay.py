"""STASYS session replay tool.

Usage:
    python tools/replay.py --session 3 [--speed 2]

Loads a recorded session from SQLite + NumPy and reconstructs/re-prints events
in chronological order. --speed controls the replay rate multiplier (default 1x).
Useful for debugging without hardware.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

_TOOLS_DIR = Path(__file__).parent
_COMPANION = _TOOLS_DIR.parent
sys.path.insert(0, str(_COMPANION))

from stasys.storage.conversions import imu_accel_gyro_convert, imu_accel_magnitude
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

class _Ansi:
    RESET = "\033[0m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"


def ansi(code: str, text: str) -> str:
    return f"{code}{text}{_Ansi.RESET}"


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def _format_ts(seconds: float) -> str:
    """Format a unix timestamp as HH:MM:SS.mmm."""
    lt = time.localtime(seconds)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Replay loader
# ---------------------------------------------------------------------------

class ReplayLoader:
    """Loads and yields reconstructed packets from a session.

    Produces tuples of (wall_timestamp, label, detail) for printing.
    """

    def __init__(self, session_id: int, db_path: str = "stasys.db") -> None:
        self._session_id = session_id
        self._store = SessionStore(db_path=db_path)
        self._raw_store = RawStore(base_path=str(Path(db_path).parent / "data" / "sessions"))

    def load(self) -> list[tuple[float, str, str]]:
        """Load all events for the session and return a sorted list of printable lines.

        Returns:
            List of (timestamp_seconds, label, detail) tuples ordered by time.
        """
        events: list[tuple[float, str, str]] = []
        session_meta = self._store.get_session(self._session_id)
        if session_meta is None:
            print(f"{_Ansi.RED}ERROR: Session {self._session_id} not found.{_Ansi.RESET}")
            return []

        started_at = session_meta["started_at"]

        # Load shot events from DB
        for shot in self._store.get_shots(self._session_id):
            # timestamp_us is firmware microseconds; convert to wall clock
            ts_sec = started_at + (shot["timestamp_us"] / 1_000_000.0)
            label = "SHOT"
            detail = (
                f"#{shot['shot_number']:<3d}  "
                f"piezo={shot['piezo_peak']:<5d}  "
                f"accel=({shot['accel_x']:+6d},{shot['accel_y']:+6d},{shot['accel_z']:+6d})  "
                f"recoil={'XYZ'[shot['recoil_axis']]}({shot['recoil_sign']:+d})"
            )
            events.append((ts_sec, label, detail))

        # Load IMU samples from .npy file (raw int, convert to floats for display)
        raw_data = self._raw_store.load_imu(self._session_id)
        if raw_data is not None and len(raw_data) > 0:
            converted = imu_accel_gyro_convert(raw_data)
            start_ts_us = raw_data[0, 0]
            for i in range(len(converted)):
                row = converted[i]
                # Convert relative timestamp_us to wall clock
                elapsed_us = int(raw_data[i, 0]) - int(start_ts_us)
                ts_sec = started_at + (elapsed_us / 1_000_000.0)
                label = "IMU"
                detail = (
                    f"ax={row[1]:>7.3f} ay={row[2]:>7.3f} az={row[3]:>7.3f} | "
                    f"gx={row[4]:>6.2f} gy={row[5]:>6.2f} gz={row[6]:>6.2f} | "
                    f"pz={int(raw_data[i, 7]):>4d} t={row[8]:>5.1f}C"
                )
                events.append((ts_sec, label, detail))

        # Sort by timestamp
        events.sort(key=lambda e: e[0])
        return events


# ---------------------------------------------------------------------------
# Replay player
# ---------------------------------------------------------------------------

class ReplayPlayer:
    """Plays back a list of (timestamp, label, detail) events at a given speed."""

    def __init__(self, events: list[tuple[float, str, str]], speed: float = 1.0) -> None:
        self._events = events
        self._speed = speed

    def run(self) -> None:
        """Print events, pacing them according to --speed."""
        if not self._events:
            print(f"{_Ansi.YELLOW}No events to replay.{_Ansi.RESET}")
            return

        t0 = time.time()
        first_ts = self._events[0][0]

        for event_ts, label, detail in self._events:
            sim_elapsed = (event_ts - first_ts) / self._speed
            real_elapsed = time.time() - t0
            if sim_elapsed > real_elapsed:
                time.sleep(sim_elapsed - real_elapsed)

            colour = {
                "IMU": _Ansi.CYAN,
                "SHOT": _Ansi.MAGENTA,
            }.get(label, "")

            ts_str = _format_ts(event_ts)
            print(f"[{ts_str}] {ansi(colour, label):<7}  {detail}")


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_session_summary(session_id: int, db_path: str = "stasys.db") -> None:
    """Print a session summary loaded from the database."""
    store = SessionStore(db_path=db_path)
    session_meta = store.get_session(session_id)
    if not session_meta:
        return

    shots = store.get_shots(session_id)
    duration = (session_meta["ended_at"] or time.time()) - session_meta["started_at"]

    print()
    print(f"  --- Session {session_id} Summary ---")
    print(f"  Duration       : {duration:.1f}s")
    print(f"  Shots detected : {len(shots)}")
    if shots:
        # Compute magnitude of first shot's accel peak
        sample = shots[0]
        mag = ((sample["accel_x"] / 8192.0 * 9.81) ** 2 +
               (sample["accel_y"] / 8192.0 * 9.81) ** 2 +
               (sample["accel_z"] / 8192.0 * 9.81) ** 2) ** 0.5
        print(f"  First shot mag : {mag:.2f} m/s²")
    print(f"  Firmware       : {session_meta.get('firmware_version', 'unknown')}")
    print(f"  Data path      : data/sessions/{session_id}/")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="STASYS session replay")
    parser.add_argument(
        "--session",
        type=int,
        required=True,
        help="Session id to replay (integer).",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier (default: 1.0, 2.0 = 2x faster).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to stasys.db (default: companion_app/stasys.db).",
    )
    args = parser.parse_args()

    db_path = args.db or str(_COMPANION / "stasys.db")

    print(f"{_Ansi.GREEN}Loading session {args.session} from {db_path}...{_Ansi.RESET}")

    loader = ReplayLoader(session_id=args.session, db_path=db_path)
    events = loader.load()

    if not events:
        print(f"{_Ansi.YELLOW}Session {args.session} not found or has no events.{_Ansi.RESET}")
        sys.exit(1)

    print_session_summary(args.session, db_path)
    print(f"{_Ansi.GREEN}Replaying {len(events)} events at {args.speed}x speed...{_Ansi.RESET}")
    print()

    player = ReplayPlayer(events, speed=args.speed)
    player.run()


if __name__ == "__main__":
    main()
