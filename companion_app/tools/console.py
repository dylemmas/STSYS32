"""STASYS interactive console — device control and live data display.

Usage:
    python tools/console.py [--port COM5]

Features:
    - Auto-discovers STASYS COM port or uses --port argument.
    - Queries device info on connect.
    - Interactive menu: start, stop, get-config, set-config, quit.
    - Live packet display with summary stats (samples/s, shots detected).
    - Ctrl+C quits gracefully.
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_TOOLS_DIR = Path(__file__).parent
_COMPANION = _TOOLS_DIR.parent
sys.path.insert(0, str(_COMPANION))

from stasys.protocol.commands import (
    cmd_get_config,
    cmd_get_info,
    cmd_set_config,
    cmd_start_session,
    cmd_stop_session,
)
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
# ANSI helpers
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
# Console
# ---------------------------------------------------------------------------

class Console:
    """Interactive STASYS device console."""

    COMMANDS = [
        ("start", "Start recording session"),
        ("stop", "Stop recording session"),
        ("get-info", "Query device info"),
        ("get-config", "Query current configuration"),
        ("set-config", "Set configuration (interactive)"),
        ("sessions", "List recorded sessions"),
        ("quit", "Disconnect and exit"),
    ]

    def __init__(self, port: str | None) -> None:
        self._port = port
        self._transport: SerialTransport | None = None
        self._parser: ProtocolParser | None = None
        self._session_store: SessionStore | None = None
        self._raw_store: RawStore | None = None
        self._data_logger: DataLogger | None = None
        self._session_id: int | None = None
        self._running = True
        self._streaming = False
        self._shot_count = 0
        self._sample_count = 0
        self._sample_rate = 0.0
        self._last_sample_time = 0.0
        self._rsp_info: RspInfo | None = None
        self._rsp_config: RspConfig | None = None
        self._lock = threading.Lock()

        self._pending_ack: dict[int, threading.Event] = {}

    def run(self) -> None:
        """Start the console."""
        self._connect()
        self._reader()
        self._command_loop()

    def _connect(self) -> None:
        """Discover port and connect."""
        port = self._port
        if not port:
            port = SerialTransport.find_stasys_port()
            if not port:
                print(f"{_Ansi.RED}ERROR: No STASYS device found.{_Ansi.RESET}")
                print(f"  Make sure the device is paired in Windows Bluetooth settings.")
                sys.exit(1)

        self._transport = SerialTransport()
        if not self._transport.connect(port):
            print(f"{_Ansi.RED}ERROR: Failed to connect to {port}{_Ansi.RESET}")
            sys.exit(1)

        self._parser = ProtocolParser(packet_callback=self._on_packet)

        # Initialize storage
        db_path = str(_COMPANION / "stasys.db")
        self._session_store = SessionStore(db_path=db_path)
        self._raw_store = RawStore(base_path=str(_COMPANION / "data" / "sessions"))

        # Open DB session
        self._session_id = self._session_store.open_session()

        self._data_logger = DataLogger(
            session_id=self._session_id,
            session_store=self._session_store,
            raw_store=self._raw_store,
            packet_queue=self._parser.packet_queue,
        )

        # Reader thread
        reader = threading.Thread(target=self._reader, daemon=True, name="ConsoleReader")
        reader.start()

        print(f"{ansi(_Ansi.GREEN, 'Connected to')} {port}")
        print(f"Querying device info...")

        # Query device info
        self._send(cmd_get_info())
        time.sleep(2.0)

        if self._rsp_info:
            info = self._rsp_info
            print()
            print(f"  {ansi(_Ansi.BOLD, 'Device Information')}")
            print(f"  {'Firmware:':<16} {info.firmware_version_str}")
            print(f"  {'Hardware:':<16} rev{info.hardware_rev}")
            print(f"  {'Build:':<16} {info.build_datetime}")
            print(f"  {'MPU6050:':<16} {'OK (0x68)' if info.mpu_ok else f'FAIL (0x{info.mpu_whoami:02X})'}")
            print(f"  {'Features:':<16} 0x{info.supported_features:04X}")
            print()
        else:
            print(f"{_Ansi.YELLOW}WARNING: No response from device (old firmware?).{_Ansi.RESET}")
            print()

        # Query config
        self._send(cmd_get_config())
        time.sleep(1.0)

        self._print_banner()

    def _reader(self) -> None:
        """Background: feed serial bytes into parser."""
        while self._running:
            try:
                data = self._transport.read_queue.get(timeout=0.5)  # type: ignore[union-attr]
                self._parser.feed(data)  # type: ignore[union-attr]
            except queue.Empty:
                continue
            except Exception:
                break

    def _send(self, data: bytes) -> None:
        """Send raw bytes to the transport."""
        if self._transport:
            self._transport.write(data)

    def _print_banner(self) -> None:
        """Print the console banner and available commands."""
        print(f"{ansi(_Ansi.CYAN, 'STASYS Console')}")
        print(f"  DB session id: {self._session_id}")
        if self._rsp_config:
            cfg = self._rsp_config
            print(f"  Config: {cfg.sample_rate_hz}Hz | mode={cfg.data_mode_name} | "
                  f"piezo_thresh={cfg.piezo_threshold} | accel_thresh={cfg.accel_threshold}")
        print()
        print(f"  Commands:")
        for cmd, desc in self.COMMANDS:
            print(f"    {ansi(_Ansi.CYAN, cmd):<14} {desc}")
        print()
        print(f"  {ansi(_Ansi.GREEN, 'Streaming:')} {self._streaming} | "
              f"{ansi(_Ansi.MAGENTA, 'Shots:')} {self._shot_count} | "
              f"{ansi(_Ansi.CYAN, 'Samples:')} {self._sample_count} | "
              f"{ansi(_Ansi.YELLOW, 'Rate:')} {self._sample_rate:.1f} Hz")
        print()

    def _command_loop(self) -> None:
        """Read and process user commands."""
        while self._running:
            try:
                line = input("stasys> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            with self._lock:
                if cmd in ("start", "s"):
                    self._cmd_start()
                elif cmd in ("stop", "x"):
                    self._cmd_stop()
                elif cmd in ("get-info", "i"):
                    self._cmd_get_info()
                elif cmd in ("get-config", "c"):
                    self._cmd_get_config()
                elif cmd in ("set-config",):
                    self._cmd_set_config(parts[1:] if len(parts) > 1 else [])
                elif cmd in ("sessions", "ls"):
                    self._cmd_list_sessions()
                elif cmd in ("quit", "q", "exit"):
                    self._cmd_quit()
                    break
                elif cmd in ("help", "h", "?"):
                    self._print_banner()
                else:
                    print(f"{_Ansi.RED}Unknown command: {cmd}{_Ansi.RESET}")
                    print(f"  Type 'help' for available commands.")

    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------

    def _cmd_start(self) -> None:
        if self._streaming:
            print(f"{_Ansi.YELLOW}Already streaming{_Ansi.RESET}")
            return
        self._send(cmd_start_session())
        # EVT_SESSION_STARTED will update _streaming and _data_logger state

    def _cmd_stop(self) -> None:
        if not self._streaming:
            print(f"{_Ansi.YELLOW}Not streaming{_Ansi.RESET}")
            return
        self._send(cmd_stop_session())
        # EVT_SESSION_STOPPED will update state

    def _cmd_get_info(self) -> None:
        self._send(cmd_get_info())

    def _cmd_get_config(self) -> None:
        self._send(cmd_get_config())

    def _cmd_set_config(self, args: list[str]) -> None:
        """Interactive config setter. Without args, prints current config."""
        if not self._rsp_config:
            print(f"{_Ansi.YELLOW}No config loaded yet. Use 'get-config' first.{_Ansi.RESET}")
            return

        cfg = self._rsp_config

        # Simple argument parsing: set-config key=value [key=value...]
        # e.g. set-config piezo_threshold=1000 sample_rate_hz=200
        if not args:
            print(f"Current config:")
            print(f"  sample_rate_hz={cfg.sample_rate_hz}")
            print(f"  piezo_threshold={cfg.piezo_threshold}")
            print(f"  accel_threshold={cfg.accel_threshold}")
            print(f"  debounce_ms={cfg.debounce_ms}")
            print(f"  led_enabled={cfg.led_enabled}")
            print(f"  data_mode={cfg.data_mode}")
            print(f"  streaming_rate_hz={cfg.streaming_rate_hz}")
            print(f"  device_name={cfg.device_name}")
            print()
            print(f"Usage: set-config key=value [key=value...]")
            return

        # Apply overrides
        new_cfg = RspConfig(
            sample_rate_hz=cfg.sample_rate_hz,
            piezo_threshold=cfg.piezo_threshold,
            accel_threshold=cfg.accel_threshold,
            debounce_ms=cfg.debounce_ms,
            led_enabled=cfg.led_enabled,
            data_mode=cfg.data_mode,
            streaming_rate_hz=cfg.streaming_rate_hz,
            device_name=cfg.device_name,
        )

        for arg in args:
            if "=" not in arg:
                print(f"{_Ansi.RED}Invalid arg: {arg} (use key=value){_Ansi.RESET}")
                continue
            key, val = arg.split("=", 1)
            try:
                if key == "sample_rate_hz":
                    new_cfg.sample_rate_hz = int(val)
                elif key == "piezo_threshold":
                    new_cfg.piezo_threshold = int(val)
                elif key == "accel_threshold":
                    new_cfg.accel_threshold = int(val)
                elif key == "debounce_ms":
                    new_cfg.debounce_ms = int(val)
                elif key == "led_enabled":
                    new_cfg.led_enabled = bool(int(val))
                elif key == "data_mode":
                    new_cfg.data_mode = int(val)
                elif key == "streaming_rate_hz":
                    new_cfg.streaming_rate_hz = int(val)
                elif key == "device_name":
                    new_cfg.device_name = val
                else:
                    print(f"{_Ansi.YELLOW}Unknown key: {key}{_Ansi.RESET}")
            except ValueError as e:
                print(f"{_Ansi.RED}Invalid value for {key}: {e}{_Ansi.RESET}")

        print(f"Sending config update...")
        self._send(cmd_set_config(new_cfg))

    def _cmd_list_sessions(self) -> None:
        if not self._session_store:
            return
        sessions = self._session_store.get_sessions()
        if not sessions:
            print("No sessions recorded yet.")
            return
        print(f"{ansi(_Ansi.BOLD, 'Sessions:')}")
        for s in sessions[:20]:  # Show last 20
            started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["started_at"]))
            ended = time.strftime("%H:%M:%S", time.localtime(s["ended_at"])) if s["ended_at"] else "running"
            print(f"  id={s['id']:<4d} fw_id={s.get('firmware_session_id', '?'):<5} "
                  f"shots={s.get('shot_count', 0):<4} {started} - {ended}")

    def _cmd_quit(self) -> None:
        self._running = False
        if self._streaming:
            self._send(cmd_stop_session())
            time.sleep(0.5)
        if self._data_logger:
            self._data_logger.stop()
        if self._session_store and self._session_id:
            self._session_store.update_shot_count(self._session_id, self._shot_count)
            self._session_store.close_session(self._session_id)
        if self._transport:
            self._transport.disconnect()
        print(f"{ansi(_Ansi.GREEN, 'Disconnected. Goodbye.')}")

    # -------------------------------------------------------------------------
    # Packet handler
    # -------------------------------------------------------------------------

    def _on_packet(self, packet: object) -> None:
        """Handle received packets."""
        with self._lock:
            if isinstance(packet, RspInfo):
                self._rsp_info = packet
                info = packet
                print(
                    f"  {ansi(_Ansi.GREEN, 'INFO')} "
                    f"{info.firmware_version_str} | hw=rev{info.hardware_rev} | "
                    f"mpu={'OK' if info.mpu_ok else f'FAIL (0x{info.mpu_whoami:02X})'}"
                )

            elif isinstance(packet, RspConfig):
                self._rsp_config = packet
                cfg = packet
                print(
                    f"  {ansi(_Ansi.CYAN, 'CONFIG')} "
                    f"rate={cfg.sample_rate_hz}Hz mode={cfg.data_mode_name} | "
                    f"thresholds: piezo={cfg.piezo_threshold} accel={cfg.accel_threshold}"
                )

            elif isinstance(packet, EvtSessionStarted):
                self._streaming = True
                if self._session_store and self._session_id:
                    self._session_store._execute(
                        "UPDATE sessions SET firmware_session_id = ?, battery_start = ? WHERE id = ?",
                        (packet.session_id, packet.battery_percent, self._session_id),
                    )
                if self._data_logger:
                    self._data_logger.start()
                print(
                    f"  {ansi(_Ansi.GREEN, 'SESSION STARTED')} "
                    f"fw_id={packet.session_id} batt={packet.battery_percent}% "
                    f"heap={packet.free_heap}B"
                )

            elif isinstance(packet, EvtSessionStopped):
                self._streaming = False
                if self._session_store and self._session_id:
                    self._session_store.update_shot_count(self._session_id, packet.shot_count)
                    self._session_store.update_battery_end(self._session_id, packet.battery_end)
                if self._data_logger:
                    self._data_logger.stop()
                print(
                    f"  {ansi(_Ansi.YELLOW, 'SESSION STOPPED')} "
                    f"shots={packet.shot_count} duration={packet.duration_ms / 1000:.1f}s "
                    f"batt={packet.battery_end}%"
                )

            elif isinstance(packet, EvtShotDetected):
                self._shot_count += 1
                print(
                    f"  {ansi(_Ansi.MAGENTA, 'SHOT')} #{packet.shot_number:<3d} "
                    f"piezo={packet.piezo_peak:<5d} "
                    f"recoil={packet.recoil_axis_name}({packet.recoil_sign:+d})"
                )

            elif isinstance(packet, DataRawSample):
                self._sample_count += 1
                now = time.time()
                if self._last_sample_time > 0:
                    dt = now - self._last_sample_time
                    if dt > 0:
                        self._sample_rate = 1.0 / dt
                self._last_sample_time = now
                # Don't print every sample in console mode (too verbose)
                # Just update stats shown in banner

            elif isinstance(packet, EvtSensorHealth):
                print(
                    f"  {ansi(_Ansi.DIM, 'HEALTH')} "
                    f"mpu={'OK' if packet.mpu_present else 'MISSING'} "
                    f"i2c_err={packet.i2c_errors} "
                    f"samples={packet.samples_total} "
                    f"invalid={packet.samples_invalid} "
                    f"i2c_recovery={packet.i2c_recovery_count}"
                )

            elif isinstance(packet, RspAck):
                if not packet.is_success:
                    print(f"  {ansi(_Ansi.RED, 'ACK ERROR')} cmd=0x{packet.command_id:02X} status={packet.status}")
                elif packet.command_id == int(PacketType.CMD_SET_CONFIG):
                    print(f"  {ansi(_Ansi.GREEN, 'Config updated successfully')}")

            elif isinstance(packet, RspError):
                print(f"  {ansi(_Ansi.RED, 'ERROR')} code={packet.error_code} msg={packet.message}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="STASYS interactive console")
    parser.add_argument(
        "--port",
        type=str,
        default=None,
        help="COM port (e.g. COM5). Auto-discovers if omitted.",
    )
    args = parser.parse_args()

    console = Console(port=args.port)

    def on_signal(signum: int, frame) -> None:
        del signum, frame
        print(f"\n{_Ansi.YELLOW}--- Interrupted ---{_Ansi.RESET}")
        console._cmd_quit()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        console.run()
    except Exception as e:
        print(f"{_Ansi.RED}Fatal error: {e}{_Ansi.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
