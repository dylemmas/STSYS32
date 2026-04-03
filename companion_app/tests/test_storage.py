"""Tests for the STASYS storage layer: SessionStore, RawStore, DataLogger, conversions."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from stasys.protocol.packets import DataRawSample, EvtShotDetected
from stasys.storage.conversions import (
    imu_accel_gyro_convert,
    imu_accel_magnitude,
    raw_to_accel_ms2,
    raw_to_gyro_dps,
    raw_to_temp_c,
)
from stasys.storage.database import create_database
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore


# =============================================================================
# Conversions
# =============================================================================

class TestConversions:
    def test_raw_to_accel_scalar(self) -> None:
        assert raw_to_accel_ms2(8192) == pytest.approx(9.81, rel=1e-3)
        assert raw_to_accel_ms2(0) == 0.0
        assert raw_to_accel_ms2(-8192) == pytest.approx(-9.81, rel=1e-3)

    def test_raw_to_gyro_scalar(self) -> None:
        assert raw_to_gyro_dps(655) == pytest.approx(10.0, rel=1e-3)
        assert raw_to_gyro_dps(0) == 0.0

    def test_raw_to_temp_scalar(self) -> None:
        assert raw_to_temp_c(0) == pytest.approx(36.53, rel=1e-2)

    def test_imu_accel_gyro_convert_array(self) -> None:
        # 9 columns: timestamp_us, accel_x/y/z, gyro_x/y/z, piezo, temp
        raw = np.array([[0, 8192, 0, 0, 655, 0, 0, 0, 0]], dtype=np.int32)
        converted = imu_accel_gyro_convert(raw)
        assert converted[0, 1] == pytest.approx(9.81, rel=1e-3)  # accel_x
        assert converted[0, 4] == pytest.approx(10.0, rel=1e-3)   # gyro_x

    def test_imu_accel_magnitude(self) -> None:
        raw = np.array([[0, 8192, 8192, 8192, 0, 0, 0, 0, 0]], dtype=np.int32)
        mag = imu_accel_magnitude(raw)
        expected = (9.81 ** 2 * 3) ** 0.5
        assert mag[0] == pytest.approx(expected, rel=1e-3)


# =============================================================================
# SessionStore
# =============================================================================

class TestSessionStore:
    def test_open_session_returns_id(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session(firmware_version="v1.0.0", hw_revision=1)
        assert sid == 1
        sessions = store.get_sessions()
        assert len(sessions) == 1
        assert sessions[0]["firmware_version"] == "v1.0.0"
        assert sessions[0]["hw_revision"] == 1
        assert sessions[0]["ended_at"] is None

    def test_open_session_multiple(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid1 = store.open_session()
        sid2 = store.open_session()
        assert sid1 != sid2
        assert len(store.get_sessions()) == 2

    def test_close_session_sets_ended_at(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()
        time.sleep(0.01)
        store.close_session(sid)
        sessions = store.get_sessions()
        assert sessions[0]["ended_at"] is not None
        assert sessions[0]["ended_at"] >= sessions[0]["started_at"]

    def test_update_shot_count(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()
        store.update_shot_count(sid, 15)
        sessions = store.get_sessions()
        assert sessions[0]["shot_count"] == 15

    def test_update_battery_end(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()
        store.update_battery_end(sid, 72)
        sessions = store.get_sessions()
        assert sessions[0]["battery_end"] == 72

    def test_record_shot_inserts(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()
        packet = EvtShotDetected(
            session_id=0, timestamp_us=2_000_000, shot_number=1,
            piezo_peak=2048, accel_x_peak=5000, accel_y_peak=0, accel_z_peak=0,
            gyro_x_peak=0, gyro_y_peak=0, gyro_z_peak=0,
            recoil_axis=0, recoil_sign=1,
        )
        store.record_shot(sid, packet)

        shots = store.get_shots(sid)
        assert len(shots) == 1
        assert shots[0]["timestamp_us"] == 2_000_000
        assert shots[0]["shot_number"] == 1
        assert shots[0]["piezo_peak"] == 2048
        assert shots[0]["accel_x"] == 5000
        assert shots[0]["recoil_axis"] == 0

    def test_record_shot_ordered_by_timestamp(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()

        def shot(timestamp_us: int) -> EvtShotDetected:
            return EvtShotDetected(
                0, timestamp_us, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
            )

        store.record_shot(sid, shot(3_000_000))
        store.record_shot(sid, shot(1_000_000))
        store.record_shot(sid, shot(2_000_000))

        shots = store.get_shots(sid)
        assert len(shots) == 3
        assert [s["timestamp_us"] for s in shots] == [1_000_000, 2_000_000, 3_000_000]

    def test_record_imu_inserts(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session()
        packet = DataRawSample(
            sample_counter=1, timestamp_us=500_000,
            accel_x=8192, accel_y=0, accel_z=0,
            gyro_x=0, gyro_y=0, gyro_z=0,
            piezo=100, temperature=340,
        )
        store.record_imu(sid, packet)
        sessions = store.get_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == sid

    def test_get_session_returns_none_for_missing(self) -> None:
        store = SessionStore(db_path=":memory:")
        assert store.get_session(9999) is None

    def test_get_session_returns_row(self) -> None:
        store = SessionStore(db_path=":memory:")
        sid = store.open_session(firmware_version="v2.0.0")
        row = store.get_session(sid)
        assert row is not None
        assert row["firmware_version"] == "v2.0.0"


# =============================================================================
# RawStore
# =============================================================================

class TestRawStore:
    def test_record_imu_creates_npy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            pkt = DataRawSample(
                sample_counter=0, timestamp_us=100_000,
                accel_x=8192, accel_y=0, accel_z=0,
                gyro_x=0, gyro_y=0, gyro_z=0,
                piezo=500, temperature=340,
            )
            store.record_imu(1, pkt)

            path = Path(tmpdir) / "1" / "imu.npy"
            assert path.exists()
            data = np.load(path)
            assert data.shape == (1, 9)
            assert data[0, 0] == 100_000      # timestamp_us
            assert data[0, 1] == 8192        # accel_x raw

    def test_record_imu_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            for i in range(3):
                pkt = DataRawSample(
                    sample_counter=i, timestamp_us=(i + 1) * 10_000,
                    accel_x=i * 1000, accel_y=0, accel_z=0,
                    gyro_x=0, gyro_y=0, gyro_z=0, piezo=0, temperature=0,
                )
                store.record_imu(1, pkt)

            path = Path(tmpdir) / "1" / "imu.npy"
            data = np.load(path)
            assert data.shape == (3, 9)
            assert data[0, 0] == 10_000
            assert data[2, 0] == 30_000

    def test_record_shot_creates_shots_npy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            pkt = EvtShotDetected(
                session_id=0, timestamp_us=1_000_000, shot_number=5,
                piezo_peak=2048, accel_x_peak=5000, accel_y_peak=-3000, accel_z_peak=1000,
                gyro_x_peak=200, gyro_y_peak=-100, gyro_z_peak=50,
                recoil_axis=1, recoil_sign=-1,
            )
            store.record_shot(10, pkt)

            path = Path(tmpdir) / "10" / "shots.npy"
            assert path.exists()
            data = np.load(path)
            assert data.shape == (1, 12)
            assert data[0, 0] == 1_000_000   # timestamp_us
            assert data[0, 1] == 5            # shot_number
            assert data[0, 2] == 2048         # piezo_peak

    def test_sessions_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            pkt1 = DataRawSample(0, 0, 1000, 0, 0, 0, 0, 0, 0, 0)
            pkt2 = DataRawSample(0, 0, 2000, 0, 0, 0, 0, 0, 0, 0)
            store.record_imu(1, pkt1)
            store.record_imu(2, pkt2)

            d1 = np.load(Path(tmpdir) / "1" / "imu.npy")
            d2 = np.load(Path(tmpdir) / "2" / "imu.npy")
            assert d1[0, 1] == 1000
            assert d2[0, 1] == 2000

    def test_load_imu_returns_none_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            assert store.load_imu(999) is None

    def test_load_imu_returns_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RawStore(base_path=tmpdir)
            pkt = DataRawSample(0, 0, 1, 2, 3, 4, 5, 6, 7, 8)
            store.record_imu(1, pkt)
            data = store.load_imu(1)
            assert data is not None
            assert data.shape == (1, 9)
