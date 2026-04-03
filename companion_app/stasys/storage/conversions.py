"""Unit conversion helpers for STASYS raw sensor data."""

from __future__ import annotations

import numpy as np


# MPU6050 configuration (matching firmware)
ACCEL_LSB_PER_G = 8192.0    # 4G range
GYRO_LSB_PER_DEG = 65.5     # 500 dps range
TEMP_SCALE = 340.0          # MPU6050 temperature
TEMP_OFFSET_C = 36.53


def raw_to_accel_ms2(raw: np.ndarray | int) -> np.ndarray | float:
    """Convert raw accelerometer values to m/s²."""
    if isinstance(raw, np.ndarray):
        return raw / ACCEL_LSB_PER_G * 9.81
    return raw / ACCEL_LSB_PER_G * 9.81


def raw_to_gyro_dps(raw: np.ndarray | int) -> np.ndarray | float:
    """Convert raw gyroscope values to deg/s."""
    if isinstance(raw, np.ndarray):
        return raw / GYRO_LSB_PER_DEG
    return raw / GYRO_LSB_PER_DEG


def raw_to_temp_c(raw: np.ndarray | int) -> np.ndarray | float:
    """Convert raw MPU6050 temperature to Celsius."""
    if isinstance(raw, np.ndarray):
        return raw / TEMP_SCALE + TEMP_OFFSET_C
    return raw / TEMP_SCALE + TEMP_OFFSET_C


# IMU .npy column indices
_COL_TS = 0
_COL_AX = 1
_COL_AY = 2
_COL_AZ = 3
_COL_GX = 4
_COL_GY = 5
_COL_GZ = 6
_COL_PZ = 7
_COL_TEMP = 8

# Shot .npy column indices
_SCOL_TS = 0
_SCOL_NUM = 1
_SCOL_PIEZO = 2
_SCOL_AX = 3
_SCOL_AY = 4
_SCOL_AZ = 5
_SCOL_GX = 6
_SCOL_GY = 7
_SCOL_GZ = 8
_SCOL_AXIS = 9
_SCOL_SIGN = 10


def imu_accel_magnitude(data: np.ndarray) -> np.ndarray:
    """Compute accelerometer magnitude in m/s² from raw IMU array.

    Args:
        data: NumPy array (n_samples, 9) from raw_store.load_imu().

    Returns:
        Array of m/s² magnitude values.
    """
    ax = raw_to_accel_ms2(data[:, _COL_AX])
    ay = raw_to_accel_ms2(data[:, _COL_AY])
    az = raw_to_accel_ms2(data[:, _COL_AZ])
    return np.sqrt(ax * ax + ay * ay + az * az)


def imu_accel_gyro_convert(data: np.ndarray) -> np.ndarray:
    """Convert raw IMU array to float array with m/s² and deg/s units.

    Args:
        data: NumPy array (n_samples, 9) of raw int values.

    Returns:
        NumPy array (n_samples, 9) of converted values:
        [timestamp_us, accel_x_ms2, accel_y_ms2, accel_z_ms2,
         gyro_x_dps, gyro_y_dps, gyro_z_dps, piezo, temp_c]
    """
    converted = np.empty((data.shape[0], 9), dtype=np.float64)
    converted[:, _COL_TS] = data[:, _COL_TS]
    converted[:, _COL_AX] = raw_to_accel_ms2(data[:, _COL_AX])
    converted[:, _COL_AY] = raw_to_accel_ms2(data[:, _COL_AY])
    converted[:, _COL_AZ] = raw_to_accel_ms2(data[:, _COL_AZ])
    converted[:, _COL_GX] = raw_to_gyro_dps(data[:, _COL_GX])
    converted[:, _COL_GY] = raw_to_gyro_dps(data[:, _COL_GY])
    converted[:, _COL_GZ] = raw_to_gyro_dps(data[:, _COL_GZ])
    converted[:, _COL_PZ] = data[:, _COL_PZ]
    converted[:, _COL_TEMP] = raw_to_temp_c(data[:, _COL_TEMP])
    return converted
