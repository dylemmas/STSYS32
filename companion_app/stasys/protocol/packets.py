"""Packet type definitions and dataclasses matching the STASYS ESP32 binary protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union

import numpy as np


# =============================================================================
# Packet Types
# =============================================================================

class PacketType(IntEnum):
    """STASYS ESP32 packet types."""

    # Commands: Python -> ESP32
    CMD_START_SESSION = 0x01
    CMD_STOP_SESSION  = 0x02
    CMD_GET_INFO      = 0x03
    CMD_GET_CONFIG    = 0x04
    CMD_SET_CONFIG    = 0x05

    # Events: ESP32 -> Python
    EVT_SESSION_STARTED  = 0x10
    EVT_SESSION_STOPPED  = 0x11
    EVT_SHOT_DETECTED    = 0x12
    EVT_SENSOR_HEALTH    = 0x13

    # Data stream
    DATA_RAW_SAMPLE = 0x20

    # Responses
    RSP_ERROR   = 0x80
    RSP_INFO    = 0x81
    RSP_CONFIG  = 0x82
    RSP_ACK     = 0x83

    # Special
    PKT_TYPE_ENCRYPTED = 0xF0

    # Flow control (not real protocol packets, handled separately)
    FLOW_XON = 0x14
    FLOW_XOFF = 0x14


# =============================================================================
# Unit Conversion Constants (matching MPU6050 firmware config)
# =============================================================================

ACCEL_LSB_PER_G = 8192.0    # 4G range: raw / 8192.0 * 9.81 = m/s²
GYRO_LSB_PER_DEG = 65.5     # 500 dps range: raw / 65.5 = deg/s
TEMP_SCALE = 340.0          # MPU6050 temperature: raw / 340.0 + 36.53
TEMP_OFFSET_C = 36.53

PIEZO_MAX = 4095            # 12-bit ADC


def raw_to_accel_ms2(raw: int) -> float:
    """Convert raw accelerometer value to m/s²."""
    return raw / ACCEL_LSB_PER_G * 9.81


def raw_to_gyro_dps(raw: int) -> float:
    """Convert raw gyroscope value to deg/s."""
    return raw / GYRO_LSB_PER_DEG


def raw_to_temp_c(raw: int) -> float:
    """Convert raw MPU6050 temperature to Celsius."""
    return raw / TEMP_SCALE + TEMP_OFFSET_C


def recoil_axis_name(axis: int) -> str:
    """Human-readable recoil axis name."""
    return ["X", "Y", "Z"][axis] if 0 <= axis <= 2 else "?"


# =============================================================================
# Packet Dataclasses
# =============================================================================

@dataclass
class EvtSessionStarted:
    """Session started event (14 bytes).

    Attributes:
        session_id: Unique session identifier from firmware.
        timestamp_us: Session start time in microseconds.
        battery_percent: Battery level 0-100.
        sensor_health: Health flags byte.
        free_heap: Free RAM in bytes.
    """
    session_id: int
    timestamp_us: int
    battery_percent: int
    sensor_health: int
    free_heap: int


@dataclass
class EvtSessionStopped:
    """Session stopped event (12 bytes).

    Attributes:
        session_id: Session identifier.
        duration_ms: Session duration in milliseconds.
        shot_count: Number of shots detected.
        battery_end: Battery level at session end.
        sensor_health: Health flags byte.
    """
    session_id: int
    duration_ms: int
    shot_count: int
    battery_end: int
    sensor_health: int


@dataclass
class EvtShotDetected:
    """Shot detected event (29 bytes).

    Attributes:
        session_id: Session identifier.
        timestamp_us: Shot timestamp in microseconds since session start.
        shot_number: Sequential shot count within session.
        piezo_peak: Peak ADC value in detection window (0-4095).
        accel_x/y/z_peak: Raw accelerometer values at peak.
        gyro_x/y/z_peak: Raw gyroscope values at peak.
        recoil_axis: Dominant recoil axis (0=X, 1=Y, 2=Z).
        recoil_sign: Sign of recoil on that axis (+1 or -1).
    """
    session_id: int
    timestamp_us: int
    shot_number: int
    piezo_peak: int
    accel_x_peak: int
    accel_y_peak: int
    accel_z_peak: int
    gyro_x_peak: int
    gyro_y_peak: int
    gyro_z_peak: int
    recoil_axis: int
    recoil_sign: int

    @property
    def recoil_axis_name(self) -> str:
        return recoil_axis_name(self.recoil_axis)


@dataclass
class EvtSensorHealth:
    """Sensor health event (11 bytes).

    Mirrors firmware PktSensorHealth layout.

    Attributes:
        mpu_present: MPU6050 present flag (1=present, 0=not found).
        i2c_errors: Cumulative I2C error count.
        samples_total: Total sample read attempts.
        samples_invalid: Total invalid/malformed samples.
        i2c_recovery_count: Number of I2C bus recoveries performed.
        dropped_samples: Samples dropped due to queue overflow (derived: samples_invalid).
    """
    mpu_present: int
    i2c_errors: int
    samples_total: int
    samples_invalid: int
    i2c_recovery_count: int
    dropped_samples: int = 0


@dataclass
class DataRawSample:
    """Raw IMU sample (24 bytes).

    Attributes:
        sample_counter: Incrementing sample index.
        timestamp_us: Microseconds since session start.
        accel_x/y/z: Raw int16 accelerometer values (4G range).
        gyro_x/y/z: Raw int16 gyroscope values (500 dps range).
        temperature: Raw int16 MPU6050 temperature (at struct offset 20).
        piezo: Raw uint16 piezoelectric ADC (at struct offset 22).
    """
    sample_counter: int
    timestamp_us: int
    accel_x: int
    accel_y: int
    accel_z: int
    gyro_x: int
    gyro_y: int
    gyro_z: int
    piezo: int
    temperature: int

    # Converted properties
    @property
    def accel_x_ms2(self) -> float:
        return raw_to_accel_ms2(self.accel_x)

    @property
    def accel_y_ms2(self) -> float:
        return raw_to_accel_ms2(self.accel_y)

    @property
    def accel_z_ms2(self) -> float:
        return raw_to_accel_ms2(self.accel_z)

    @property
    def gyro_x_dps(self) -> float:
        return raw_to_gyro_dps(self.gyro_x)

    @property
    def gyro_y_dps(self) -> float:
        return raw_to_gyro_dps(self.gyro_y)

    @property
    def gyro_z_dps(self) -> float:
        return raw_to_gyro_dps(self.gyro_z)

    @property
    def temperature_c(self) -> float:
        return raw_to_temp_c(self.temperature)

    @property
    def accel_magnitude_ms2(self) -> float:
        ax = raw_to_accel_ms2(self.accel_x)
        ay = raw_to_accel_ms2(self.accel_y)
        az = raw_to_accel_ms2(self.accel_z)
        return (ax * ax + ay * ay + az * az) ** 0.5


@dataclass
class RspInfo:
    """Device/firmware info response (14 bytes).

    Attributes:
        firmware_version: Packed version e.g. 0x010000 = v1.0.0.
        hardware_rev: Hardware revision byte.
        build_timestamp: Unix timestamp of firmware build.
        supported_features: Feature flags bitmap.
        mpu_whoami: MPU6050 WHOAMI register (should be 0x68).
    """
    firmware_version: int
    hardware_rev: int
    build_timestamp: int
    supported_features: int
    mpu_whoami: int

    @property
    def firmware_version_str(self) -> str:
        v = self.firmware_version
        major = (v >> 16) & 0xFF
        minor = (v >> 8) & 0xFF
        patch = v & 0xFF
        return f"v{major}.{minor}.{patch}"

    @property
    def build_datetime(self) -> str:
        import datetime
        try:
            return datetime.datetime.fromtimestamp(self.build_timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return f"<ts:{self.build_timestamp}>"

    @property
    def mpu_ok(self) -> bool:
        return self.mpu_whoami in (0x68, 0x70)  # MPU6050 or MPU6500


@dataclass
class RspConfig:
    """Configuration response/set command (46 bytes).

    Mirrors CMD_SET_CONFIG layout.

    Attributes:
        sample_rate_hz: IMU sample rate (50, 100, or 200).
        piezo_threshold: Piezoelectric detection threshold (default 800).
        accel_threshold: Accelerometer jerk threshold (default 300).
        debounce_ms: Shot detection debounce in ms (default 200).
        led_enabled: LED feedback enabled (0=off, 1=on).
        data_mode: Stream mode (0=both, 1=raw-only, 2=events-only).
        streaming_rate_hz: Raw sample streaming rate (default 100).
        device_name: BT device name (up to 20 bytes, null-trimmed).
    """
    sample_rate_hz: int
    piezo_threshold: int
    accel_threshold: int
    debounce_ms: int
    led_enabled: bool
    data_mode: int
    streaming_rate_hz: int
    device_name: str

    @staticmethod
    def default() -> "RspConfig":
        """Return default configuration matching firmware defaults."""
        return RspConfig(
            sample_rate_hz=100,
            piezo_threshold=800,
            accel_threshold=300,
            debounce_ms=200,
            led_enabled=True,
            data_mode=0,
            streaming_rate_hz=100,
            device_name="STASYS",
        )

    @property
    def data_mode_name(self) -> str:
        names = ["both (raw + events)", "raw-only", "events-only"]
        return names[self.data_mode] if 0 <= self.data_mode <= 2 else "unknown"


@dataclass
class RspAck:
    """Generic acknowledgement (2 bytes).

    Attributes:
        command_id: Echo of the command type being acknowledged.
        status: 0 = success, non-zero = error.
    """
    command_id: int
    status: int

    @property
    def is_success(self) -> bool:
        return self.status == 0


@dataclass
class RspError:
    """Error response (33 bytes).

    Attributes:
        error_code: Numeric error code.
        message: Null-terminated error message string.
    """
    error_code: int
    message: str


@dataclass
class RawPacket:
    """Raw packet for unknown or unparsed packet types.

    Attributes:
        packet_type: The packet type byte.
        payload: Raw payload bytes.
    """
    packet_type: PacketType
    payload: bytes


# Type alias for all parsed packet types
ParsedPacket = Union[
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    EvtSensorHealth,
    DataRawSample,
    RspInfo,
    RspConfig,
    RspAck,
    RspError,
    RawPacket,
]
