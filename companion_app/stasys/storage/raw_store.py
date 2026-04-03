"""Binary blob storage for high-rate IMU data as .npy files."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from stasys.protocol.packets import DataRawSample


class RawStore:
    """Stores high-rate IMU sensor data as NumPy binary files.

    Each session gets a directory ``data/sessions/<session_id>/`` containing:
        - ``imu.npy`` — 2D array (n_samples, 9), columns:
                        [timestamp_us, accel_x, accel_y, accel_z,
                         gyro_x, gyro_y, gyro_z, piezo, temp]
                        All values stored as int32 (raw sensor values).
        - ``shots.npy`` — 2D array (n_shots, 12), columns:
                          [timestamp_us, shot_number, piezo_peak,
                           accel_x/y/z_peak, gyro_x/y/z_peak, recoil_axis, recoil_sign]
                          All values stored as int32.

    Directories are created automatically on first write.
    """

    NUM_COLS = 9   # timestamp_us + 3 accel + 3 gyro + piezo + temp
    SHOT_COLS = 12  # timestamp_us + shot_number + piezo_peak + 3 accel + 3 gyro + axis + sign
    DTYPE = np.int32

    def __init__(self, base_path: str = "data/sessions") -> None:
        self._base_path = Path(base_path)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _session_dir(self, session_id: int) -> Path:
        """Return the directory for a session, creating it if necessary."""
        d = self._base_path / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _imu_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "imu.npy"

    def _shots_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "shots.npy"

    def _append_imu(self, path: Path, new_row: np.ndarray) -> None:
        """Append a single IMU row to the .npy file, creating it if needed."""
        if path.exists():
            existing = np.load(path, allow_pickle=False)  # type: ignore[no-untyped-call]
            combined = np.vstack((existing, new_row))
        else:
            combined = new_row
        np.save(path, combined)

    def _append_shot(self, path: Path, new_row: np.ndarray) -> None:
        """Append a shot row to the .npy file, creating it if needed."""
        if path.exists():
            existing = np.load(path, allow_pickle=False)  # type: ignore[no-untyped-call]
            combined = np.vstack((existing, new_row))
        else:
            combined = new_row
        np.save(path, combined)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def record_imu(self, session_id: int, packet: DataRawSample) -> None:
        """Append a single IMU sample to the session's imu.npy file.

        Args:
            session_id: The session this sample belongs to.
            packet: The parsed DATA_RAW_SAMPLE packet.
        """
        row = np.array(
            [[
                packet.timestamp_us,
                packet.accel_x,
                packet.accel_y,
                packet.accel_z,
                packet.gyro_x,
                packet.gyro_y,
                packet.gyro_z,
                packet.piezo,
                packet.temperature,
            ]],
            dtype=self.DTYPE,
        )
        self._append_imu(self._imu_path(session_id), row)

    def record_shot(self, session_id: int, packet) -> None:
        """Append a shot event to the session's shots.npy file.

        Args:
            session_id: The session this shot belongs to.
            packet: EvtShotDetected packet.
        """
        row = np.array(
            [[
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
                0,  # padding
            ]],
            dtype=self.DTYPE,
        )
        self._append_shot(self._shots_path(session_id), row)

    def load_imu(self, session_id: int) -> np.ndarray | None:
        """Load the IMU data array for a session.

        Returns:
            NumPy array (n_samples, 9) or None if file doesn't exist.
        """
        path = self._imu_path(session_id)
        if not path.exists():
            return None
        return np.load(path, allow_pickle=False)  # type: ignore[no-untyped-call]

    def load_shots(self, session_id: int) -> np.ndarray | None:
        """Load the shots data array for a session.

        Returns:
            NumPy array (n_shots, 12) or None if file doesn't exist.
        """
        path = self._shots_path(session_id)
        if not path.exists():
            return None
        return np.load(path, allow_pickle=False)  # type: ignore[no-untyped-call]
