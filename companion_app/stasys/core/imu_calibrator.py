"""IMU calibration module for STASYS.

Detects static state from accelerometer magnitude standard deviation, collects
N samples, computes gyro and accelerometer bias (mean), and applies bias
correction to all subsequent raw samples.

Reference C++ ROS logic ported from detectStaticState() + performCalibration().
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional


# C++ reference: static_threshold_ = 0.1 m/s²
# Equivalent Python default based on 50-sample rolling window at ~100 Hz.
# 50 samples gives ~0.5s of history, which is appropriate for detecting
# a shooter holding the device steady between shots.
STATIC_THRESHOLD_MS2: float = 0.1       # m/s² — std dev of accel magnitude
MAGNITUDE_WINDOW: int = 50              # samples for std dev calculation
CALIBRATION_SAMPLES: int = 500          # target: ~5s at 100Hz, ~2.5s at 200Hz


@dataclass
class CalibrationBias:
    """Bias offsets computed during calibration."""
    gyro_x: float = 0.0   # deg/s
    gyro_y: float = 0.0   # deg/s
    gyro_z: float = 0.0   # deg/s
    accel_x: float = 0.0  # raw counts (LSB)
    accel_y: float = 0.0  # raw counts (LSB)
    accel_z: float = 0.0  # raw counts (LSB)


class IMUCalibrator:
    """IMU calibrator — detects static state, collects samples, computes bias.

    C++ reference logic::

        void detectStaticState(const Eigen::Vector3d& accel) {
            accel_magnitudes_.push_back(accel.norm());
            if (accel_magnitudes_.size() > 50) { accel_magnitudes_.pop_front(); }
            double mean = sum(magnitudes) / count;
            double std_dev = sqrt(sum((m - mean)^2) / count);
            is_static_ = (std_dev < static_threshold_);
        }

        void performCalibration(const Eigen::Vector3d& accel, const Eigen::Vector3d& gyro) {
            gyro_samples_.push_back(gyro);
            accel_samples_.push_back(accel);
            calibration_count_++;
            if (calibration_count_ >= calibration_samples_) {
                gyro_bias_ = mean(gyro_samples_);
                is_calibrated_ = true;
            }
        }

    Usage::

        calibrator = IMUCalibrator()

        # On each incoming DataRawSample:
        calibrator.feed(raw_gyro_x, raw_gyro_y, raw_gyro_z,
                        raw_accel_x, raw_accel_y, raw_accel_z)

        if calibrator.is_static:
            pass  # user is holding steady

        if calibrator.is_calibrated:
            # Apply bias to raw gyro values:
            gyro_x_corrected = gyro_x_raw - calibrator.bias.gyro_x
        # Or use the convenience method:
        corrected = calibrator.apply_bias(raw_gyro_x, raw_gyro_y, raw_gyro_z,
                                         raw_accel_x, raw_accel_y, raw_accel_z)
    """

    def __init__(
        self,
        static_threshold: float = STATIC_THRESHOLD_MS2,
        magnitude_window: int = MAGNITUDE_WINDOW,
        calibration_samples: int = CALIBRATION_SAMPLES,
    ) -> None:
        self._static_threshold = static_threshold
        self._magnitude_window = magnitude_window
        self._calibration_samples = calibration_samples

        # Rolling accelerometer magnitude window for static detection
        self._accel_magnitudes: deque[float] = deque(maxlen=magnitude_window)

        # Raw sample accumulators (bias = mean of accumulated samples)
        self._gyro_x_sum: float = 0.0
        self._gyro_y_sum: float = 0.0
        self._gyro_z_sum: float = 0.0
        self._accel_x_sum: float = 0.0
        self._accel_y_sum: float = 0.0
        self._accel_z_sum: float = 0.0

        # State
        self._count: int = 0
        self._bias: CalibrationBias = CalibrationBias()
        self._is_calibrated: bool = False
        self._is_static: bool = False

    @property
    def is_static(self) -> bool:
        """True when accelerometer magnitude std dev is below threshold."""
        return self._is_static

    @property
    def is_calibrated(self) -> bool:
        """True after CALIBRATION_SAMPLES have been collected and bias computed."""
        return self._is_calibrated

    @property
    def bias(self) -> CalibrationBias:
        """Current bias offsets."""
        return self._bias

    @property
    def sample_count(self) -> int:
        """Number of samples collected so far during calibration."""
        return self._count

    @property
    def calibration_target(self) -> int:
        """Target number of samples for full calibration."""
        return self._calibration_samples

    @property
    def progress(self) -> float:
        """Progress 0.0–1.0 of calibration."""
        if self._calibration_samples == 0:
            return 1.0
        return min(1.0, self._count / self._calibration_samples)

    def feed(
        self,
        gyro_x: int,
        gyro_y: int,
        gyro_z: int,
        accel_x: int,
        accel_y: int,
        accel_z: int,
    ) -> None:
        """Feed raw IMU values. Call on every DATA_RAW_SAMPLE packet.

        Args:
            gyro_x/y/z: raw 16-bit gyro values (divide by 65.5 for deg/s)
            accel_x/y/z: raw 16-bit accel values (divide by 8192.0*9.81 for m/s²)
        """
        if self._is_calibrated:
            return

        # ── Static detection ──────────────────────────────────────────────────
        # Convert to float for magnitude calculation
        ax_ms2 = accel_x / 8192.0 * 9.81
        ay_ms2 = accel_y / 8192.0 * 9.81
        az_ms2 = accel_z / 8192.0 * 9.81
        accel_mag = (ax_ms2 * ax_ms2 + ay_ms2 * ay_ms2 + az_ms2 * az_ms2) ** 0.5

        self._accel_magnitudes.append(accel_mag)

        if len(self._accel_magnitudes) < 3:
            self._is_static = False
            return

        # Compute mean
        total = sum(self._accel_magnitudes)
        mean = total / len(self._accel_magnitudes)

        # Compute std dev (population formula, matching C++ reference)
        sum_sq_diff = sum((m - mean) ** 2 for m in self._accel_magnitudes)
        std_dev = (sum_sq_diff / len(self._accel_magnitudes)) ** 0.5

        self._is_static = std_dev < self._static_threshold

        # ── Calibration collection ────────────────────────────────────────────
        # Accumulate samples regardless of motion state. The running mean naturally
        # filters out motion: brief movements contribute a few noisy samples to the
        # average, but the dominant gravity signal dominates over 500 samples.
        # This avoids the previous bug where accelerometer quantization noise between
        # samples caused is_static to flicker, resetting the counter and never
        # reaching 500.
        self._gyro_x_sum += gyro_x
        self._gyro_y_sum += gyro_y
        self._gyro_z_sum += gyro_z
        self._accel_x_sum += accel_x
        self._accel_y_sum += accel_y
        self._accel_z_sum += accel_z
        self._count += 1

        if self._count >= self._calibration_samples:
            n = float(self._calibration_samples)
            self._bias = CalibrationBias(
                gyro_x=self._gyro_x_sum / n,
                gyro_y=self._gyro_y_sum / n,
                gyro_z=self._gyro_z_sum / n,
                accel_x=self._accel_x_sum / n,
                accel_y=self._accel_y_sum / n,
                accel_z=self._accel_z_sum / n,
            )
            self._is_calibrated = True

    def apply_bias(
        self,
        gyro_x: int,
        gyro_y: int,
        gyro_z: int,
        accel_x: int,
        accel_y: int,
        accel_z: int,
    ) -> tuple[float, float, float, float, float, float]:
        """Apply computed bias to raw values. Returns corrected values in deg/s and m/s².

        Returns:
            (gyro_x_dps, gyro_y_dps, gyro_z_dps, accel_x_ms2, accel_y_ms2, accel_z_ms2)
        """
        if self._is_calibrated:
            gx = (gyro_x - self._bias.gyro_x) / 65.5
            gy = (gyro_y - self._bias.gyro_y) / 65.5
            gz = (gyro_z - self._bias.gyro_z) / 65.5
        else:
            gx = gyro_x / 65.5
            gy = gyro_y / 65.5
            gz = gyro_z / 65.5

        ax = (accel_x - self._bias.accel_x) / 8192.0 * 9.81
        ay = (accel_y - self._bias.accel_y) / 8192.0 * 9.81
        az = (accel_z - self._bias.accel_z) / 8192.0 * 9.81

        return gx, gy, gz, ax, ay, az

    def reset(self) -> None:
        """Reset calibration state. Call before starting a new session."""
        self._accel_magnitudes.clear()
        self._gyro_x_sum = 0.0
        self._gyro_y_sum = 0.0
        self._gyro_z_sum = 0.0
        self._accel_x_sum = 0.0
        self._accel_y_sum = 0.0
        self._accel_z_sum = 0.0
        self._count = 0
        self._bias = CalibrationBias()
        self._is_calibrated = False
        self._is_static = False