"""Session export: JSON and CSV output for STASYS sessions."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

_TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from stasys.storage.conversions import imu_accel_gyro_convert, raw_to_temp_c
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore


class SessionExporter:
    """Export STASYS session data to JSON and CSV formats."""

    def __init__(self, db_path: str = "stasys.db") -> None:
        self._store = SessionStore(db_path=db_path)
        self._raw_store = RawStore(base_path=str(Path(db_path).parent / "data" / "sessions"))

    def get_session_summary(self, session_id: int) -> dict[str, Any] | None:
        """Return session metadata as a dict."""
        meta = self._store.get_session(session_id)
        if not meta:
            return None
        shots = self._store.get_shots(session_id)
        duration = (meta["ended_at"] or 0) - meta["started_at"]
        return {
            "session_id": session_id,
            "firmware_session_id": meta.get("firmware_session_id"),
            "started_at": meta["started_at"],
            "ended_at": meta["ended_at"],
            "duration_s": round(duration, 3),
            "shot_count": len(shots),
            "firmware_version": meta.get("firmware_version", ""),
            "battery_start": meta.get("battery_start"),
            "battery_end": meta.get("battery_end"),
            "note": meta.get("note", ""),
        }

    # -------------------------------------------------------------------------
    # JSON export
    # -------------------------------------------------------------------------

    def export_json(self, session_id: int, output_path: str | Path | None = None) -> str:
        """Export a session to JSON.

        Args:
            session_id: Session to export.
            output_path: Output file path. If None, returns JSON string.

        Returns:
            JSON string, or writes to output_path and returns the path.
        """
        meta = self.get_session_summary(session_id)
        if meta is None:
            raise ValueError(f"Session {session_id} not found")

        # Load shots from DB
        shots = self._store.get_shots(session_id)
        shot_list = []
        for s in shots:
            shot_list.append({
                "shot_number": s["shot_number"],
                "timestamp_us": s["timestamp_us"],
                "piezo_peak": s["piezo_peak"],
                "accel_x_peak": s["accel_x"],
                "accel_y_peak": s["accel_y"],
                "accel_z_peak": s["accel_z"],
                "gyro_x_peak": s["gyro_x"],
                "gyro_y_peak": s["gyro_y"],
                "gyro_z_peak": s["gyro_z"],
                "recoil_axis": s["recoil_axis"],
                "recoil_sign": s["recoil_sign"],
            })

        # Load IMU data from .npy and convert
        imu_data: list[dict[str, Any]] = []
        raw_data = self._raw_store.load_imu(session_id)
        if raw_data is not None and len(raw_data) > 0:
            converted = imu_accel_gyro_convert(raw_data)
            start_ts = raw_data[0, 0]
            for i in range(len(converted)):
                row = converted[i]
                imu_data.append({
                    "sample_index": i,
                    "timestamp_us": int(row[0]),
                    "elapsed_us": int(row[0]) - int(start_ts),
                    # Raw integers
                    "accel_x_raw": int(raw_data[i, 1]),
                    "accel_y_raw": int(raw_data[i, 2]),
                    "accel_z_raw": int(raw_data[i, 3]),
                    "gyro_x_raw": int(raw_data[i, 4]),
                    "gyro_y_raw": int(raw_data[i, 5]),
                    "gyro_z_raw": int(raw_data[i, 6]),
                    "piezo_raw": int(raw_data[i, 7]),
                    "temp_raw": int(raw_data[i, 8]),
                    # Converted floats
                    "accel_x_ms2": round(float(row[1]), 6),
                    "accel_y_ms2": round(float(row[2]), 6),
                    "accel_z_ms2": round(float(row[3]), 6),
                    "gyro_x_dps": round(float(row[4]), 4),
                    "gyro_y_dps": round(float(row[5]), 4),
                    "gyro_z_dps": round(float(row[6]), 4),
                    "temp_c": round(float(row[8]), 2),
                })

        output = {
            "session": meta,
            "shots": shot_list,
            "imu_samples": imu_data,
        }

        json_str = json.dumps(output, indent=2)

        if output_path:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json_str, encoding="utf-8")
            return str(p)
        return json_str

    # -------------------------------------------------------------------------
    # CSV export
    # -------------------------------------------------------------------------

    def export_csv(self, session_id: int, output_dir: str | Path | None = None) -> list[str]:
        """Export a session to CSV files.

        Creates two files:
          - <output_dir>/session_<id>_shots.csv — shot events
          - <output_dir>/session_<id>_imu.csv     — IMU samples (converted floats)

        Args:
            session_id: Session to export.
            output_dir: Output directory. If None, uses ./exports.

        Returns:
            List of created file paths.
        """
        meta = self.get_session_summary(session_id)
        if meta is None:
            raise ValueError(f"Session {session_id} not found")

        out_dir = Path(output_dir) if output_dir else Path("./exports")
        out_dir.mkdir(parents=True, exist_ok=True)

        created = []

        # Shots CSV
        shots_path = out_dir / f"session_{session_id}_shots.csv"
        shots = self._store.get_shots(session_id)
        with open(shots_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "shot_number", "timestamp_us", "piezo_peak",
                "accel_x_peak", "accel_y_peak", "accel_z_peak",
                "gyro_x_peak", "gyro_y_peak", "gyro_z_peak",
                "recoil_axis", "recoil_sign",
            ])
            writer.writeheader()
            for s in shots:
                writer.writerow({
                    "shot_number": s["shot_number"],
                    "timestamp_us": s["timestamp_us"],
                    "piezo_peak": s["piezo_peak"],
                    "accel_x_peak": s["accel_x"],
                    "accel_y_peak": s["accel_y"],
                    "accel_z_peak": s["accel_z"],
                    "gyro_x_peak": s["gyro_x"],
                    "gyro_y_peak": s["gyro_y"],
                    "gyro_z_peak": s["gyro_z"],
                    "recoil_axis": s["recoil_axis"],
                    "recoil_sign": s["recoil_sign"],
                })
        created.append(str(shots_path))

        # IMU CSV (converted floats)
        imu_path = out_dir / f"session_{session_id}_imu.csv"
        raw_data = self._raw_store.load_imu(session_id)
        if raw_data is not None and len(raw_data) > 0:
            converted = imu_accel_gyro_convert(raw_data)
            start_ts = raw_data[0, 0]
            with open(imu_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp_us", "elapsed_us",
                    "accel_x_ms2", "accel_y_ms2", "accel_z_ms2",
                    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps",
                    "piezo_raw", "temp_c",
                ])
                writer.writeheader()
                for i in range(len(converted)):
                    row = converted[i]
                    writer.writerow({
                        "timestamp_us": int(raw_data[i, 0]),
                        "elapsed_us": int(raw_data[i, 0]) - int(start_ts),
                        "accel_x_ms2": round(float(row[1]), 6),
                        "accel_y_ms2": round(float(row[2]), 6),
                        "accel_z_ms2": round(float(row[3]), 6),
                        "gyro_x_dps": round(float(row[4]), 4),
                        "gyro_y_dps": round(float(row[5]), 4),
                        "gyro_z_dps": round(float(row[6]), 4),
                        "piezo_raw": int(raw_data[i, 7]),
                        "temp_c": round(float(row[8]), 2),
                    })
            created.append(str(imu_path))

        return created


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="STASYS session export")
    parser.add_argument("--session", type=int, required=True, help="Session ID to export")
    parser.add_argument("--format", choices=["json", "csv", "both"], default="both",
                        help="Export format (default: both)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output path (file for JSON, dir for CSV)")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to stasys.db")

    args = parser.parse_args()

    db_path = args.db or str(_TOOLS_DIR / "stasys.db")
    exporter = SessionExporter(db_path)

    meta = exporter.get_session_summary(args.session)
    if meta is None:
        print(f"ERROR: Session {args.session} not found")
        sys.exit(1)

    print(f"Exporting session {args.session} (shots={meta['shot_count']}, "
          f"duration={meta['duration_s']:.1f}s)")

    if args.format in ("json", "both"):
        out = exporter.export_json(args.session, args.output)
        print(f"JSON: {out}")

    if args.format in ("csv", "both"):
        out_dir = args.output or "./exports"
        files = exporter.export_csv(args.session, out_dir)
        for f in files:
            print(f"CSV: {f}")

    print("Done.")


if __name__ == "__main__":
    main()
