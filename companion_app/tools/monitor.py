"""STASYS live CLI monitor.

Usage:
    python tools/monitor.py [--port COM5] [--auto-start]

Behavior:
    1. Auto-discovers STASYS COM port or uses --port argument.
    2. Connects via SerialTransport.
    3. Sends CMD_GET_INFO to identify firmware.
    4. Sends CMD_START_SESSION on startup (with --auto-start).
    5. Prints received packets to the terminal.
    6. On Ctrl+C: sends CMD_STOP_SESSION, closes session, prints summary.
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import sys
import threading
import time
from pathlib import Path

# Resolve companion_app/ relative to this script's location
_TOOLS_DIR = Path(__file__).parent
_COMPANION = _TOOLS_DIR.parent
sys.path.insert(0, str(_COMPANION))

from stasys.protocol.commands import (
    cmd_get_config,
    cmd_get_info,
    cmd_start_session,
    cmd_stop_session,
)
from stasys.protocol.flow_control import FlowControl
from stasys.protocol.parser import ProtocolParser
from stasys.protocol.packets import (
    DataRawSample,
    EvtSensorHealth,
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    PacketType,
    RspAck,
    RspConfig,
    RspError,
    RspInfo,
    recoil_axis_name,
)
from stasys.storage.data_logger import DataLogger
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore
from stasys.transport.serial_transport import SerialTransport

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor")


# ---------------------------------------------------------------------------
# Terminal colour helpers
# ---------------------------------------------------------------------------

class _Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    DIM = "\033[2m"


def ansi(code: str, text: str) -> str:
    return f"{code}{text}{_Ansi.RESET}"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_sample(pkt: DataRawSample) -> str:
    """Format a DATA_RAW_SAMPLE for terminal display."""
    return (
        f"  IMU   ax={pkt.accel_x_ms2:>7.3f} ay={pkt.accel_y_ms2:>7.3f} az={pkt.accel_z_ms2:>7.3f} | "
        f"gx={pkt.gyro_x_dps:>6.2f} gy={pkt.gyro_y_dps:>6.2f} gz={pkt.gyro_z_dps:>6.2f} | "
        f"pz={pkt.piezo:>4d} t={pkt.temperature_c:>5.1f}C"
    )


def _format_shot(pkt: EvtShotDetected) -> str:
    """Format an EVT_SHOT_DETECTED for terminal display."""
    return (
        f"  SHOT  #{pkt.shot_number:<3d}  piezo={pkt.piezo_peak:<5d}  "
        f"recoil={pkt.recoil_axis_name}({pkt.recoil_sign:+d})  "
        f"accel_peak=({pkt.accel_x_peak:+6d},{pkt.accel_y_peak:+6d},{pkt.accel_z_peak:+6d})"
    )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class Monitor:
    """Live STASYS session monitor.

    Manages the transport connection, protocol parser, session storage,
    and CLI output loop. Cleans up gracefully on Ctrl+C.
    """

    def __init__(self, port: str | None, auto_start: bool = False) -> None:
        self._port = port
        self._auto_start = auto_start
        self._transport: SerialTransport | None = None
        self._parser: ProtocolParser | None = None
        self._flow: FlowControl | None = None
        self._session_store: SessionStore | None = None
        self._raw_store: RawStore | None = None
        self._data_logger: DataLogger | None = None
        self._session_id: int | None = None
        self._firmware_session_id: int | None = None
        self._started_at: float = 0.0
        self._rsp_info: RspInfo | None = None
        self._rsp_config: RspConfig | None = None
        self._running = True
        self._streaming = False
        self._shot_count = 0
        self._sample_count = 0
        self._last_sample_time = 0.0
        self._sample_rate = 0.0
        self._print_lock = threading.Lock()
        self._ack_event = threading.Event()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def run(self) -> None:
        """Start monitoring and block until interrupted."""
        self._setup()
        self._main_loop()

    def _setup(self) -> None:
        """Discover port, connect transport, open session, start logger."""
        # Port discovery
        port = self._port
        if not port:
            port = SerialTransport.find_stasys_port()
            if not port:
                print(f"{_Ansi.RED}ERROR: No STASYS port found. Run tools/scan_ports.py first.{_Ansi.RESET}")
                sys.exit(1)
            print(f"{ansi(_Ansi.GREEN, 'Auto-discovered port:')} {port}")
        else:
            print(f"{ansi(_Ansi.GREEN, 'Using port:')} {port}")

        # Connect transport
        self._transport = SerialTransport()
        self._parser = ProtocolParser(packet_callback=self._on_packet)
        self._flow = FlowControl(write_callback=self._transport.write)

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._reader, daemon=True, name="MonitorReader")
        self._reader_thread.start()

        if not self._transport.connect(port):
            print(f"{_Ansi.RED}ERROR: Failed to connect to {port}{_Ansi.RESET}")
            sys.exit(1)

        print(f"{ansi(_Ansi.GREEN, 'Connected.')} Querying device info...")

        # Send CMD_GET_INFO
        self._send_cmd(cmd_get_info())

        # Wait for RSP_INFO with timeout
        start = time.time()
        while self._rsp_info is None and time.time() - start < 5.0:
            time.sleep(0.1)

        if self._rsp_info:
            info = self._rsp_info
            print(f"{ansi(_Ansi.GREEN, 'Device info:')} {info.firmware_version_str} | "
                  f"hw=rev{info.hardware_rev} | build={info.build_datetime} | "
                  f"mpu={'OK' if info.mpu_ok else f'FAIL (0x{info.mpu_whoami:02X})'} | "
                  f"features=0x{info.supported_features:04X}")
        else:
            print(f"{_Ansi.YELLOW}WARNING: No RSP_INFO received within 5s — device may be running old firmware{_Ansi.RESET}")

        # Get config
        self._send_cmd(cmd_get_config())
        start = time.time()
        while self._rsp_config is None and time.time() - start < 5.0:
            time.sleep(0.1)

        # Open session in DB
        db_path = str(_COMPANION / "stasys.db")
        self._session_store = SessionStore(db_path=db_path)
        self._raw_store = RawStore(base_path=str(_COMPANION / "data" / "sessions"))

        # Open session in DB (firmware_session_id set when EVT_SESSION_STARTED arrives)
        self._session_id = self._session_store.open_session(
            firmware_version=self._rsp_info.firmware_version_str if self._rsp_info else "unknown",
            hw_revision=self._rsp_info.hardware_rev if self._rsp_info else 0,
            build_timestamp=self._rsp_info.build_timestamp if self._rsp_info else 0,
            battery_start=0,
        )

        self._data_logger = DataLogger(
            session_id=self._session_id,
            session_store=self._session_store,
            raw_store=self._raw_store,
            packet_queue=self._parser.packet_queue,
        )
        self._data_logger.start()
        self._started_at = time.time()

        print(f"{ansi(_Ansi.GREEN, 'Session opened:')} id={self._session_id}")
        print(f"  Type {ansi(_Ansi.CYAN, 'start')}/{ansi(_Ansi.CYAN, 'stop')} to control recording. "
              f"Ctrl+C to exit.")

        if self._auto_start:
            self._do_start()

    def _reader(self) -> None:
        """Background thread: feed serial bytes into parser."""
        while self._running:
            try:
                data = self._transport.read_queue.get(timeout=0.5)  # type: ignore[union-attr]
                self._parser.feed(data)  # type: ignore[union-attr]
            except queue.Empty:
                continue
            except Exception:
                break

    def _send_cmd(self, data: bytes) -> None:
        """Send a command packet through flow control."""
        if self._flow is None:
            return
        self._flow.write(data)

    def _do_start(self) -> None:
        """Send CMD_START_SESSION."""
        if self._streaming:
            print(f"{_Ansi.YELLOW}Already streaming{_Ansi.RESET}")
            return
        print(f"{ansi(_Ansi.GREEN, '>>> START SESSION')}")
        self._send_cmd(cmd_start_session())

    def _do_stop(self) -> None:
        """Send CMD_STOP_SESSION."""
        if not self._streaming:
            print(f"{_Ansi.YELLOW}Not streaming{_Ansi.RESET}")
            return
        print(f"{ansi(_Ansi.YELLOW, '>>> STOP SESSION')}")
        self._send_cmd(cmd_stop_session())

    def _main_loop(self) -> None:
        """Block until _running is False (set by signal handler)."""
        while self._running:
            time.sleep(0.2)

    def _teardown(self) -> None:
        """Gracefully close session and disconnect."""
        self._running = False

        if self._streaming:
            self._send_cmd(cmd_stop_session())
            time.sleep(0.5)

        if self._data_logger is not None:
            self._data_logger.stop()

        if self._session_id is not None and self._session_store is not None:
            self._session_store.update_shot_count(self._session_id, self._shot_count)
            self._session_store.close_session(self._session_id)

        if self._transport is not None:
            self._transport.disconnect()

        # Print summary
        duration_s = time.time() - self._started_at
        print()
        print(f"  --- Session Summary ---")
        print(f"  DB session id    : {self._session_id}")
        print(f"  FW session id    : {self._firmware_session_id}")
        print(f"  Duration         : {duration_s:.1f}s")
        print(f"  Shots detected   : {self._shot_count}")
        print(f"  Samples recorded : {self._sample_count}")
        print(f"  Avg sample rate  : {self._sample_rate:.1f} Hz")
        if self._rsp_info:
            print(f"  Firmware         : {self._rsp_info.firmware_version_str}")
        print(f"  Data saved to    : data/sessions/{self._session_id}/")

    # -------------------------------------------------------------------------
    # Packet handler
    # -------------------------------------------------------------------------

    def _on_packet(self, packet: object) -> None:
        """Called by ProtocolParser on each parsed packet."""
        with self._print_lock:
            if isinstance(packet, RspInfo):
                self._rsp_info = packet
                # Already printed in setup

            elif isinstance(packet, RspConfig):
                self._rsp_config = packet
                cfg = packet
                print(f"  Config: rate={cfg.sample_rate_hz}Hz mode={cfg.data_mode_name} "
                      f"piezo_thresh={cfg.piezo_threshold} accel_thresh={cfg.accel_threshold}")

            elif isinstance(packet, EvtSessionStarted):
                self._streaming = True
                self._firmware_session_id = packet.session_id
                ts_start = packet.timestamp_us / 1_000_000.0
                print(
                    f"  {ansi(_Ansi.GREEN, 'SESSION STARTED')} "
                    f"fw_id={packet.session_id} batt={packet.battery_percent}% "
                    f"heap={packet.free_heap}B sensor_health=0x{packet.sensor_health:02X}"
                )
                # Update DB with firmware session ID
                if self._session_id is not None and self._session_store is not None:
                    self._session_store._execute(
                        "UPDATE sessions SET firmware_session_id = ?, battery_start = ? WHERE id = ?",
                        (packet.session_id, packet.battery_percent, self._session_id),
                    )

            elif isinstance(packet, EvtSessionStopped):
                self._streaming = False
                duration_s = packet.duration_ms / 1000.0
                print(
                    f"  {ansi(_Ansi.YELLOW, 'SESSION STOPPED')} "
                    f"fw_id={packet.session_id} duration={duration_s:.1f}s "
                    f"shots={packet.shot_count} batt={packet.battery_end}%"
                )
                if self._session_id is not None and self._session_store is not None:
                    self._session_store.update_shot_count(self._session_id, packet.shot_count)
                    self._session_store.update_battery_end(self._session_id, packet.battery_end)

            elif isinstance(packet, EvtShotDetected):
                self._shot_count += 1
                print(_format_shot(packet))

            elif isinstance(packet, DataRawSample):
                self._sample_count += 1
                now = time.time()
                if self._last_sample_time > 0:
                    elapsed = now - self._last_sample_time
                    if elapsed > 0:
                        self._sample_rate = 1.0 / elapsed
                self._last_sample_time = now
                print(_format_sample(packet))

            elif isinstance(packet, EvtSensorHealth):
                print(
                    f"  {ansi(_Ansi.DIM, 'HEALTH')} "
                    f"mpu={'OK' if packet.mpu_present else 'MISSING'} "
                    f"total={packet.samples_total} invalid={packet.samples_invalid} "
                    f"i2c_err={packet.i2c_errors} recoveries={packet.i2c_recovery_count}"
                )

            elif isinstance(packet, RspAck):
                if packet.is_success:
                    logger.debug("ACK: cmd 0x%02X success", packet.command_id)
                else:
                    print(f"  {ansi(_Ansi.RED, 'ACK ERROR')} cmd=0x{packet.command_id:02X} status={packet.status}")

            elif isinstance(packet, RspError):
                print(f"  {ansi(_Ansi.RED, 'ERROR')} code={packet.error_code} msg={packet.message}")

    # -------------------------------------------------------------------------
    # CLI input (for interactive start/stop)
    # -------------------------------------------------------------------------

    def handle_input(self, line: str) -> None:
        """Handle a line of input from stdin."""
        line = line.strip().lower()
        if line in ("start", "s"):
            self._do_start()
        elif line in ("stop", "x"):
            self._do_stop()
        elif line in ("quit", "q", "exit"):
            self._teardown()
            sys.exit(0)
        elif line in ("info", "i"):
            if self._rsp_info:
                info = self._rsp_info
                print(f"  FW: {info.firmware_version_str} | HW: rev{info.hardware_rev} | "
                      f"MPU: 0x{info.mpu_whoami:02X} | Features: 0x{info.supported_features:04X}")
            else:
                print("  No device info received yet")
        elif line == "help":
            print("  Commands: start/s, stop/x, info/i, quit/q")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="STASYS live CLI monitor")
    parser.add_argument(
        "--port",
        type=str,
        default=None,
        help="COM port to use (e.g. COM5). "
             "If omitted, auto-discovers STASYS port.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Automatically start recording session on connect.",
    )
    args = parser.parse_args()

    monitor = Monitor(port=args.port, auto_start=args.auto_start)

    def on_signal(signum: int, frame) -> None:
        del signum, frame
        print(f"\n{_Ansi.YELLOW}--- Interrupted ---{_Ansi.RESET}")
        monitor._teardown()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Run monitor in a thread so input can be read from stdin
    monitor_thread = threading.Thread(target=monitor.run, daemon=True, name="MonitorMain")
    monitor_thread.start()

    # Wait for setup to complete
    time.sleep(1.0)

    # Read stdin for commands
    try:
        while True:
            try:
                line = input()
                monitor.handle_input(line)
            except EOFError:
                break
    except KeyboardInterrupt:
        pass

    monitor._teardown()


if __name__ == "__main__":
    main()
