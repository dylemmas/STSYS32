"""Session analysis: split times, group size, recoil distribution, and scoring."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from stasys.storage.conversions import imu_accel_magnitude, raw_to_accel_ms2
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore


class SessionAnalysis:
    """Compute analysis metrics from a recorded STASYS session."""

    def __init__(self, db_path: str = "stasys.db") -> None:
        self._store = SessionStore(db_path=db_path)
        self._raw_store = RawStore(base_path=str(Path(db_path).parent / "data" / "sessions"))

    def analyze(self, session_id: int) -> dict[str, Any]:
        """Run all analysis metrics on a session.

        Returns:
            Dict with: shot_count, split_times_ms, group_size, recoil_distribution, score, summary.
        """
        meta = self._store.get_session(session_id)
        if not meta:
            return {"error": "Session not found"}

        shots = self._store.get_shots(session_id)
        if not shots:
            return {
                "session_id": session_id,
                "shot_count": 0,
                "split_times_ms": [],
                "group_size_ms2": 0.0,
                "recoil_distribution": {},
                "score": 0,
                "summary": "No shots detected",
            }

        # Split times (microseconds from firmware → ms)
        split_times_ms = []
        for i in range(1, len(shots)):
            dt = (shots[i]["timestamp_us"] - shots[i - 1]["timestamp_us"]) / 1000.0
            split_times_ms.append(round(dt, 3))

        # Group size: centroid + max deviation in accel space
        group_size_ms2 = 0.0
        recoil_dist: dict[str, int] = {"X": 0, "Y": 0, "Z": 0}

        shots_data = self._raw_store.load_shots(session_id)
        if shots_data is not None and len(shots_data) > 0:
            # shots.npy columns: [timestamp_us, shot_number, piezo_peak,
            #   accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, recoil_axis, recoil_sign, _]
            n = min(len(shots_data), len(shots))
            accel_peaks: list[tuple[float, float, float]] = []

            for i in range(n):
                shot = shots[i]
                ax_raw = int(shots_data[i, 3])
                ay_raw = int(shots_data[i, 4])
                az_raw = int(shots_data[i, 5])
                accel_peaks.append((
                    raw_to_accel_ms2(ax_raw),
                    raw_to_accel_ms2(ay_raw),
                    raw_to_accel_ms2(az_raw),
                ))
                axis = int(shots_data[i, 9])
                axis_name = ["X", "Y", "Z"][axis] if 0 <= axis <= 2 else "?"
                recoil_dist[axis_name] = recoil_dist.get(axis_name, 0) + 1

            if accel_peaks:
                pts = np.array(accel_peaks)
                centroid = pts.mean(axis=0)
                deviations = np.sqrt(((pts - centroid) ** 2).sum(axis=1))
                group_size_ms2 = float(deviations.max())

        # Score computation
        score = self._compute_score(shots, split_times_ms, group_size_ms2)

        duration_ms = 0
        if meta["ended_at"] and meta["started_at"]:
            duration_ms = int((meta["ended_at"] - meta["started_at"]) * 1000)

        return {
            "session_id": session_id,
            "firmware_session_id": meta.get("firmware_session_id"),
            "shot_count": len(shots),
            "duration_ms": duration_ms,
            "split_times_ms": split_times_ms,
            "avg_split_ms": round(sum(split_times_ms) / len(split_times_ms), 1) if split_times_ms else 0,
            "min_split_ms": min(split_times_ms) if split_times_ms else 0,
            "max_split_ms": max(split_times_ms) if split_times_ms else 0,
            "group_size_ms2": round(group_size_ms2, 4),
            "recoil_distribution": recoil_dist,
            "score": score,
            "summary": self._summarize(score, len(shots), group_size_ms2),
        }

    def _compute_score(self, shots: list, split_times: list, group_size_ms2: float) -> int:
        """Compute a 0-100 session score.

        Factors:
        - Group consistency (60%): smaller group = better
        - Split time consistency (40%): lower variance = better
        """
        # Group component: normalize group_size to 0-60 range
        # group_size_ms2 is in m/s²; typical good groups are <20 m/s² deviation
        group_norm = max(0.0, min(1.0, group_size_ms2 / 50.0))
        group_score = int(60 * (1.0 - group_norm))

        # Split time consistency
        if len(split_times) >= 2:
            mean_split = sum(split_times) / len(split_times)
            variance = sum((dt - mean_split) ** 2 for dt in split_times) / len(split_times)
            stddev = math.sqrt(variance)
            split_norm = max(0.0, min(1.0, stddev / 2000.0))
            split_score = int(40 * (1.0 - split_norm))
        else:
            split_score = 40

        return min(100, group_score + split_score)

    def _summarize(self, score: int, shot_count: int, group_size_ms2: float) -> str:
        if shot_count == 0:
            return "No shots recorded"
        rating = "Excellent" if score >= 85 else "Good" if score >= 70 else "Fair" if score >= 50 else "Needs Improvement"
        return f"{rating} (score={score}, group={group_size_ms2:.1f} m/s²)"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="STASYS session analysis")
    parser.add_argument("--session", type=int, required=True, help="Session ID")
    parser.add_argument("--db", type=str, default=None, help="Path to stasys.db")
    args = parser.parse_args()

    db_path = args.db or str(_TOOLS_DIR / "stasys.db")
    analysis = SessionAnalysis(db_path)
    result = analysis.analyze(args.session)

    import json
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
