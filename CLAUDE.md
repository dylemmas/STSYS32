# CLAUDE.md — STASYS ESP32 Firmware

> **IMPORTANT: CLAUDE.md Maintenance Rule**
> Any time a file within this project is modified (firmware source, companion app, scripts, config, protocol, etc.), CLAUDE.md must be reviewed and updated to reflect the change before committing.
> Specifically: if you modify `src/protocol.h`, `src/protocol.cpp`, `src/bluetooth.cpp`, `src/bluetooth.h`, `src/config.h`, `src/session.h`, `src/sensor.h`, `src/shot_detector.h`, `src/led.h`, `src/battery.h`, `src/storage.h`, `src/ota.h`, `src/security.h`, `src/coredump.h`, `src/main.cpp`, `platformio.ini`, `partitions_ota.csv`, or any file in `companion_app/stasys/protocol/`, `companion_app/stasys/transport/`, `companion_app/stasys/storage/`, `companion_app/gui/`, or `companion_app/tools/`, check whether CLAUDE.md needs updating and update it in the same commit.

## Git Workflow

**Repository**: https://github.com/dylemmas/STSYS32

After every edit, commit the changes and push to GitHub:
```
git add <changed files>
git commit -m "<description>"
git push origin main
```

## Project Overview

STASYS is a shooting athlete training device that captures muzzle trace movement and shot events via an ESP32 + MPU6050 + Piezoelectric sensor system. This directory contains the ESP32 firmware. The companion Python app lives in `companion_app/`.

## Hardware

| Component | Details |
|-----------|---------|
| MCU | ESP32 DEVKIT V1 |
| IMU | MPU6050 (6-axis accel+gyro, I2C 0x68) |
| Charge Module | TP4056 |
| Vibration Sensor | Piezoelectric |
| Battery | LiPo 3.7V |
| Mount | Picatinny rail |

## Firmware

- **Location**: `src/` (12 modules + main)
- **Version**: v1.0.1 (platformio.ini `BUILD_VERSION_*`, reflected in RSP_INFO)
- **Framework**: ESP32 Arduino + FreeRTOS
- **Build**: `pio run` (PlatformIO)
- **Flash**: `pio run --target upload`
- **Comm**: Bluetooth Classic SPP
- **Device Name**: `"STASYS"` (configurable via NVS, default from `config.h`)

## Architecture

### FreeRTOS Tasks (Core Assignment)

| Task | Core | Priority | Stack | Description |
|------|------|----------|-------|-------------|
| SensorTask | 1 | 3 | 4096 | Reads MPU6050 via ISR; pushes to sampleQueue |
| ShotDetector | 1 | 2 | 4096 | Consumes sampleQueue; detects shots; sends events |
| RecoveryTask | 1 | 2 | 2048 | Async I2C bus recovery (non-blocking for sensor) |
| StreamTask | 0 | 1 | 2048 | Consumes sampleQueue; sends DATA_RAW_SAMPLE packets |
| BatteryMonitor | 0 | 1 | 1024 | Reads battery every 30s; sends EVT_SENSOR_HEALTH |
| BluetoothTask | 0 | 2 | 4096 | Reads/writes SPP RFCOMM; dispatches commands |
| LEDTask | 0 | 1 | 2048 | Drives LED/LEDC PWM patterns + haptic feedback |

### Data Flow
```
MPU6050 ISR (Core 1)
    → sampleQueue (64 samples)
        → ShotDetector: shot events → TX queue → BluetoothTask → SPP
        → StreamTask: raw samples → TX queue → BluetoothTask → SPP

Commands: BluetoothTask RX → dispatchCommand → session/sensor/config handlers

I2C Error → recoveryQueue → RecoveryTask → recoverI2CBus() → reinit MPU6050
```

## Bluetooth Protocol

### Pairing (Windows)
1. ESP32 advertises as `"STASYS"` (or configured name) in Just Works mode — no PIN needed
2. Windows Settings → Bluetooth → Add device → pair with "STASYS" (no PIN entry)
3. After pairing, Windows assigns a virtual COM port: `"Standard Serial over Bluetooth link (COMx)"`
4. The Python app uses this COM port to connect over SPP

### Packet Format
```
[0xAA] [0x55] [TYPE] [LEN_LO] [LEN_HI] [PAYLOAD...] [CRC16_LO] [CRC16_HI]
```
- **Sync**: `0xAA 0x55`
- **Type**: 1 byte
- **Length**: 2 bytes, little-endian
- **Payload**: 0-64 bytes
- **CRC**: CRC-16/CCITT (seed=0xFFFF) of (TYPE + LEN + PAYLOAD)

### Packet Types

#### Python → ESP32 (Commands)
| Type | Name | Payload | Description |
|------|------|---------|-------------|
| 0x01 | CMD_START_SESSION | empty | Start recording session |
| 0x02 | CMD_STOP_SESSION | empty | Stop recording session |
| 0x03 | CMD_GET_INFO | empty | Get firmware/device info |
| 0x04 | CMD_GET_CONFIG | empty | Get current configuration |
| 0x05 | CMD_SET_CONFIG | 46 bytes | Set configuration |
| 0x06 | CMD_AUTH | 36 bytes | HMAC-SHA256 auth token response |
| 0x0B | CMD_FACTORY_RESET | empty | Wipe NVS, reboot |
| 0x0C | CMD_OTA_START | 4 bytes | Begin OTA update (total_size) |
| 0x0D | CMD_OTA_DATA | var | OTA firmware chunk |
| 0x0E | CMD_OTA_END | empty | Finalize OTA |
| 0x0F | CMD_OTA_ABORT | empty | Abort OTA |
| 0x11 | CMD_OTA_STATUS | empty | Get OTA progress/status |
| 0x20 | CMD_GET_SESSIONS | empty | Enumerate stored sessions |
| 0x21 | CMD_GET_SESSION_DATA | 4 bytes | Download session data |
| 0x22 | CMD_DELETE_SESSION | 4 bytes | Delete a session |
| 0x23 | CMD_CALIBRATE_START | empty | Start user calibration |
| 0x24 | CMD_CALIBRATE_STATUS | empty | Get calibration quality |
| 0x25 | CMD_SET_MOUNT_MODE | 1 byte | Set mount orientation |
| 0x26 | CMD_GET_CALIBRATION | empty | Get calibration data |
| 0x41 | CMD_GET_COREDUMP | empty | Download coredump from flash |
| 0x42 | CMD_ERASE_COREDUMP | empty | Erase stored coredump |
| 0x43 | CMD_GET_SHOT_STATS | empty | Get adaptive threshold stats |

#### ESP32 → Python (Responses & Events)
| Type | Name | Payload | Description |
|------|------|---------|-------------|
| 0x10 | EVT_SESSION_STARTED | 14 bytes | Session started, includes metadata |
| 0x11 | EVT_SESSION_STOPPED | 12 bytes | Session ended, includes summary |
| 0x12 | EVT_SHOT_DETECTED | 29 bytes | Shot detected event |
| 0x13 | EVT_SENSOR_HEALTH | 11 bytes | Periodic health report |
| 0x14 | EVT_AUTH_CHALLENGE | 20 bytes | Auth challenge from server |
| 0x20 | DATA_RAW_SAMPLE | 24 bytes | Continuous IMU+piezo stream |
| 0x80 | RSP_ERROR | 33 bytes | Error response |
| 0x81 | RSP_INFO | 14 bytes | Device/firmware info |
| 0x82 | RSP_CONFIG | 46 bytes | Current configuration |
| 0x83 | RSP_ACK | 2 bytes | Generic acknowledgement |
| 0x84 | RSP_OTA_STATUS | 9 bytes | OTA progress response |
| 0xF0 | PKT_TYPE_ENCRYPTED | var | AES-128-CCM encrypted wrapper |

### Payload Structures

**CMD_SET_CONFIG / RSP_CONFIG (46 bytes)**:
```
sample_rate_hz: 1 byte       (50, 100, or 200)
piezo_threshold: 2 bytes     (default: 800)
accel_threshold: 2 bytes     (default: 300)
debounce_ms: 2 bytes          (default: 200)
led_enabled: 1 byte          (0=off, 1=on)
data_mode: 1 byte            (0=both, 1=raw-only, 2=events-only)
streaming_rate_hz: 2 bytes   (default: 100)
device_name: 20 bytes       (BT device name)
reserved: 15 bytes
```

**CMD_AUTH (36 bytes)**:
```
session_id: 4 bytes
token: 32 bytes             (HMAC-SHA256 response)
```

**EVT_AUTH_CHALLENGE (20 bytes)**:
```
session_id: 4 bytes
challenge: 16 bytes         (random)
```

**EVT_SESSION_STARTED (14 bytes)**:
```
session_id: 4 bytes          (unique per session)
timestamp_us: 4 bytes       (session start, microseconds)
battery_percent: 1 byte     (0-100)
sensor_health: 1 byte        (health flags)
free_heap: 4 bytes          (free RAM in bytes)
```

**EVT_SESSION_STOPPED (12 bytes)**:
```
session_id: 4 bytes
duration_ms: 4 bytes
shot_count: 2 bytes
battery_end: 1 byte
sensor_health: 1 byte
```

**EVT_SENSOR_HEALTH (11 bytes)**:
```
mpu_present: 1 byte
i2c_errors: 1 byte
samples_total: 2 bytes
samples_invalid: 2 bytes
i2c_recovery_count: 1 byte
reserved: 4 bytes
```

**RSP_OTA_STATUS (9 bytes)**:
```
state: 1 byte              (0=IDLE,1=RECEIVING,2=VERIFYING,3=COMPLETE,4=ERROR)
reserved: 1 byte
bytes_received: 4 bytes
total_expected: 4 bytes
```

**EVT_SHOT_DETECTED (29 bytes)**:
```
session_id: 4 bytes
timestamp_us: 4 bytes          (microseconds since session start)
shot_number: 2 bytes           (sequential shot count)
piezo_peak: 2 bytes            (peak ADC value in detection window)
accel_peak_x: 2 bytes         (raw accel at peak)
accel_peak_y: 2 bytes
accel_peak_z: 2 bytes
gyro_peak_x: 2 bytes          (raw gyro at peak)
gyro_peak_y: 2 bytes
gyro_peak_z: 2 bytes
recoil_axis: 1 byte           (0=X, 1=Y, 2=Z)
recoil_sign: 1 byte           (+1 or -1)
```

**DATA_RAW_SAMPLE (24 bytes)**:
```
sample_counter: 4 bytes         (incrementing sample index)
timestamp_us: 4 bytes          (microseconds since session start)
accel_x: 2 bytes             (raw 16-bit, 4G range: /8192.0*9.81 = m/s²)
accel_y: 2 bytes
accel_z: 2 bytes
gyro_x: 2 bytes              (raw 16-bit, 500dps range: /65.5 = deg/s)
gyro_y: 2 bytes
gyro_z: 2 bytes
piezo: 2 bytes               (raw ADC)
temperature: 2 bytes          (MPU6050 temp, 0.01 deg C units)
```

**RSP_INFO (14 bytes)**:
```
firmware_version: 4 bytes    (e.g. 0x010000 = v1.0.0)
hardware_rev: 1 byte
build_timestamp: 4 bytes
supported_features: 2 bytes   (feature flags bitmap)
mpu_whoami: 1 byte         (should be 0x68)
reserved: 2 bytes
```

**RSP_ACK (2 bytes)**:
```
command_id: 1 byte           (echo of command type)
status: 1 byte               (0=success)
```

**RSP_ERROR (33 bytes)**:
```
error_code: 1 byte
message: 32 bytes            (null-terminated string)
```

**SessionHeader (24 bytes, SPIFFS storage)**:
```
session_id: 4 bytes
start_time_us: 4 bytes
duration_ms: 4 bytes
shot_count: 2 bytes
battery_start: 1 byte
battery_end: 1 byte
sensor_health_flags: 1 byte
reserved: 3 bytes
```

**CalibrationData (internal, NVS "calib" namespace)**:
```
accel_bias_x/y/z: 2 bytes each
gyro_bias_x/y/z: 2 bytes each
temp_coeff: 2 bytes         (degC offset per degC from 25C reference)
mount_mode: 1 byte           (0=standard, 1=rotated_90, 2=inverted, 3=rotated_270)
is_calibrated: 1 byte
factory_calibrated: 1 byte
```

**Encrypted Packet Wrapper (PKT_TYPE_ENCRYPTED = 0xF0)**:
```
IV: 8 bytes + CIPHERTEXT + TAG: 8 bytes
CRC computed over (IV + CIPHERTEXT + TAG)
```

## Typical Workflow

1. **Pair & Connect** — Pair ESP32 via Windows Bluetooth Settings (Just Works, no PIN needed)
2. **Note COM Port** — Find "Standard Serial over Bluetooth link (COMx)" in Device Manager
3. **Get Info** — Send `CMD_GET_INFO` to verify firmware version
4. **Configure** — Optionally send `CMD_SET_CONFIG` to adjust thresholds/rates
5. **Start Session** — Send `CMD_START_SESSION`, receive `EVT_SESSION_STARTED`
6. **Receive Data** — Receive `DATA_RAW_SAMPLE` stream at configured rate
7. **Shot Events** — Receive `EVT_SHOT_DETECTED` for each detected shot (LED + haptic fires on device)
8. **Stop Session** — Send `CMD_STOP_SESSION`, receive `EVT_SESSION_STOPPED` with summary

## Sensor Configuration

- **IMU**: MPU6050 (0x68) / MPU6500 (0x70), SDA=GPIO21, SCL=GPIO22
- **Accelerometer**: 4G range (LSB=8192/g), DLPF 188Hz
- **Gyroscope**: 500 dps range (LSB=65.5/deg/s), DLPF 188Hz
- **Sample rate**: 1kHz internal, decimated to 50/100/200 Hz configurable
- **Interrupt**: MPU6050 INT pin (GPIO4) triggers FreeRTOS binary semaphore
- **Piezo**: ADC1_CH7 (GPIO35), raw 12-bit ADC (0-4095)
- **I2C recovery**: RecoveryTask on Core 1 signals async recovery after 5 consecutive errors
- **Degraded mode**: If MPU6050 fails 5 consecutive reads, enters degraded mode (suppresses streaming)
- **I2C scan**: Diagnostic scan prints all responding I2C addresses on boot
- **Calibration**: Bias subtraction applied per sample (factory + user); temperature compensation for gyro

## Data Analysis

### Muzzle Trace (from DATA_RAW_SAMPLE)
- **Accelerometer**: 4G range → raw / 8192.0 * 9.81 = m/s²
- **Gyroscope**: 500 dps range → raw / 65.5 = deg/s
- **Sample rate**: Configurable (default 100 Hz)
- **Plot**: accel_x/y/z over time → trace movement in 3D
- **Recoil analysis**: Peak accel magnitude and direction during shot window

### Shot Detection (from EVT_SHOT_DETECTED)
- **timestamp_us**: Microsecond-accurate shot timestamp
- **piezo_peak**: Shock intensity (0-4095 ADC value)
- **recoil_axis/sign**: Estimated dominant recoil direction
- **shot_number**: Sequential count within session

## Connection Details

- **Protocol**: RFCOMM/SPP over Bluetooth Classic
- **Baud rate**: N/A (SPP is stream-oriented)
- **Pairing**: Just Works SSP (no PIN required)
- **Default device name**: "STASYS" (stored in NVS)
- **Windows COM port**: "Standard Serial over Bluetooth link (COMx)"
- **BT TX Power**: ESP_PWR_LVL_P9 (+9dBm, maximum) — improves range on battery

## Python Companion App

The companion app is in `companion_app/` and communicates with the ESP32 over Bluetooth Classic SPP. It was rebuilt from scratch to match the firmware protocol exactly.

### Setup
```bash
cd companion_app
pip install -r requirements.txt     # pyserial, PyQt6, pyqtgraph, numpy, pytest
```

### Running
```
python main.py                     # Launch PyQt6 desktop GUI (default)
python main.py --console --port COM5  # Interactive device console
python main.py --monitor --port COM5  # Live CLI monitor with real-time IMU output
python main.py --monitor --port COM5 --auto-start  # Auto-starts recording
python main.py --scan             # List available COM ports
```

### Architecture
```
companion_app/
├── main.py                  # Entry point (launches GUI, console, or monitor)
├── gui/
│   ├── main_window.py        # Main window, top bar, tab container, signal router
│   ├── tab_live.py           # LIVE tab: real-time trace plot + steadiness stats
│   ├── tab_shot_detail.py    # SHOT DETAIL tab: target plot + coaching tips
│   ├── tab_analysis.py       # ANALYSIS tab: direction wheel + score trend
│   ├── tab_history.py        # HISTORY tab: session list + grouping + replay
│   ├── tab_settings.py       # SETTINGS tab: detection mode, weapon type, thresholds
│   ├── theme.py              # Dark theme colors + QSS stylesheet
│   └── widgets/
│       ├── score_gauge.py    # Arc gauge for shot scores
│       ├── direction_wheel.py  # Polar sector wheel for shot direction
│       └── status_bar.py      # Bottom status bar
├── stasys/
│   ├── protocol/            # Binary protocol layer
│   │   ├── packets.py       # PacketType enum, dataclasses, conversion constants
│   │   ├── parser.py        # Streaming parser with CRC-16/CCITT validation
│   │   ├── commands.py      # Command encoder (sync + CRC framing)
│   │   ├── flow_control.py  # XON/XOFF backpressure handling
│   │   └── crc.py          # CRC-16/CCITT implementation
│   ├── transport/            # Serial/BT transport
│   │   └── serial_transport.py  # SPP COM port, auto-discovery, read thread
│   ├── storage/              # Data persistence
│   │   ├── database.py       # SQLite schema (sessions, shots, IMU index)
│   │   ├── session_store.py  # Session/shot CRUD via SQLite
│   │   ├── raw_store.py     # Raw IMU/shots in .npy files per session
│   │   ├── data_logger.py   # Background thread: packet → storage pipeline
│   │   ├── conversions.py   # raw→m/s², deg/s, °C conversion
│   │   ├── analysis.py      # Session metrics (split times, group size, scores)
│   │   └── export.py        # JSON/CSV export
│   └── core/                 # Core utilities (placeholder)
├── tools/
│   ├── console.py           # Interactive device console (CLI)
│   ├── monitor.py           # Live session monitor (CLI)
│   ├── replay.py             # Session playback tool
│   ├── scan_ports.py        # COM port scanner
│   └── loopback_test.py     # Protocol round-trip test over mock serial
└── tests/                    # pytest test suite
```

### Tests
```
python -m pytest tests/ -v        # Protocol, storage, transport, GUI tests
python tools/loopback_test.py     # Protocol round-trip test over mock serial
```

## Build & Scripts

| File | Description |
|------|-------------|
| `build_timestamp.py` | Pre-build script: sets `BUILD_TIMESTAMP` to Unix epoch at compile time |
| `scripts/secure_boot.py` | Post-build script: signs firmware with `espsecure.py` for secure boot |
| `scripts/sbom.py` | Generates software bill of materials (SBOM) from build artifacts |
| `scripts/hw_test.py` | Hardware test script (runs over USB serial) |
| `partitions_ota.csv` | Custom partition table: factory + 2x OTA + SPIFFS + coredump |

## Notes

- The ESP32 streams continuously during a session. Handle backpressure gracefully — if Python can't keep up, samples will be dropped in the TX queue. The companion app implements XON/XOFF flow control.
- Shot detection is firmware-side (dual-threshold on piezo + accel jerk). Python receives both raw data AND shot events simultaneously.
- The firmware uses CRC-16/CCITT (seed=0xFFFF). Verify CRC on received packets; discard corrupted ones. The companion app's parser does this automatically.
- **Parser debug logging**: Enable with `PARSER_DEBUG = True` or `parser.set_debug(True)`. Logs every parsed packet's type byte (hex), name, and CRC result at DEBUG level. CRC failures are always logged regardless of the flag.
- Data mode `0` (both) streams raw samples + sends shot events. Use `2` (events-only) for lowest bandwidth usage.
- **Adaptive thresholds**: After 5 shots, the detector computes mean + 2*stddev of piezo peaks and self-tunes. Threshold suggestions are printed on session stop.
- **Stale session guard**: If the ESP32 has an active session from a prior connection (e.g. BT dropout without clean disconnect), `handleStartSession` calls `stopSession()` first before starting a fresh session with a new ID. This prevents `EVT_SESSION_STARTED` timeouts in the companion app.
- **LED**: LEDC PWM on GPIO2 with configurable brightness (0-255). Patterns: BOOTING (1Hz blink), IDLE (double-blink), CONNECTED (solid), STREAMING (sine breathing), SHOT (3x rapid flash), LOW_BATTERY (slow pulse), ERROR (SOS).
- **Haptic**: LEDC PWM on GPIO32 (150Hz), configurable intensity. Fires on shot detection.
- **TX flow control**: XON (0x11) sent when TX queue drops below 16 items; XOFF (0x13) sent when TX queue exceeds 48 items. Sent as raw RFCOMM bytes, not framed protocol packets.
- **RX buffer**: 1024 bytes, overflow counter tracked. On overflow, wait for drain rather than discard.
- **Power management**: 5-minute idle timeout → light sleep (10s wake cycle) when charging. **Battery power: no sleep** — BT radio stays active at maximum TX power (+9dBm) at all times.
- **Auth**: HMAC-SHA256 challenge/response via `CMD_AUTH`. Compile with `#define REQUIRE_AUTH 1` in bluetooth.cpp to enforce. Currently disabled for dev workflow.
- **Encryption**: AES-128-CCM encrypted packets via `PKT_TYPE_ENCRYPTED` wrapper. Session key derived via HKDF from device secret after auth.
- **Shot stats**: `CMD_GET_SHOT_STATS` returns shot_count, mean_peak, stddev_peak, adaptive_threshold, adaptive_enabled.
- **Session storage**: SPIFFS stores sessions as `/sessions/<session_id>.bin` (header + shot events). Enumerate via `CMD_GET_SESSIONS`, download via `CMD_GET_SESSION_DATA`, delete via `CMD_DELETE_SESSION`.
- **Coredump**: `CMD_GET_COREDUMP` downloads stored coredump from flash partition; `CMD_ERASE_COREDUMP` wipes it.

## Current Debug Target
**Status**: All hardware subsystems verified (see `TEST_REPORT.md`). Ready for field testing.
**Pairing**: Windows Bluetooth Settings → pair as "STASYS" (Just Works, no PIN)
**COM port**: "Standard Serial over Bluetooth link (COMx)" — use `--scan` or Device Manager
**Test**: `python main.py --console --port COMx` → send `get-info` to verify firmware version

---

## Commercial Readiness Plan (All 6 Phases)

The firmware is a functional prototype. The following plan addresses all gaps for commercial deployment. See `C:\Users\Rakha\.claude\plans\rippling-tinkering-dusk.md` for the full detail.

### Phase 1: Security
| # | Item | Description | Status |
|---|------|-------------|--------|
| 1.1 | BT Link Encryption | AES-128 CCM on SPP, session key via HKDF | DONE (src/security.cpp, PKT_TYPE_ENCRYPTED) |
| 1.2 | Command Authentication | HMAC-SHA256 per-session token after start | DONE (src/security.cpp, CMD_AUTH) |
| 1.3 | Secure Boot + Flash Encryption | espsecure.py, efuse flags | DONE (scripts/secure_boot.py, flash encryption init) |
| 1.4 | Shared Secret in Efuse | Per-device 16-byte secret in BLOCK1 | DONE (src/security.cpp, provisionDeviceSecret) |
| 1.5 | JTAG / Debug Disable | Burn `DIS_JTAG` efuse on boot | DONE (main.cpp:disableJTAG) |
| 1.6 | Encrypted NVS | NVS encryption via flash encryption | DONE (main.cpp:initNVS, transparent flash enc) |

### Phase 2: Core Commercial Features
| # | Item | Description | Status |
|---|------|-------------|--------|
| 2.1 | OTA Firmware Updates | Dual-bank partition, CMD_OTA_START/DATA/END/ABORT | DONE (src/ota.cpp, partitions_ota.csv) |
| 2.2 | Local Data Persistence | SPIFFS session storage, enumerate/download/delete via BT | DONE (src/storage.cpp, CMD_GET/DELETE_SESSION) |
| 2.3 | Battery Safety | Discharge curve, deep sleep <5%, cycle count, health flag | DONE (src/battery.cpp:voltageToPercent, cycle tracking) |
| 2.4 | Version Negotiation | Feature flags bitmap in RSP_INFO | DONE (protocol.h:FEATURE_*, bluetooth.cpp:sendInfoPacket) |
| 2.5 | Factory Reset | Wipe NVS, SPIFFS, security keys | DONE (CMD_FACTORY_RESET, nvs_flash_erase) |
| 2.6 | Power Management | Light sleep idle, deep sleep critical battery, <10mA idle target | DONE (main.cpp:checkIdleSleep, battery.cpp:batteryCriticalShutdown) |

### Phase 3: Reliability & Robustness
| # | Item | Description | Status |
|---|------|-------------|--------|
| 3.1 | Fix CRC Decoder Bug | protocol.cpp — incremental CRC at LEN_HI | DONE (src/protocol.cpp:330-350) |
| 3.2 | Fix Watchdog Timer Race | main.cpp — move WDT init before task creation | DONE (main.cpp:619-621) |
| 3.3 | Async I2C Recovery | Dedicated recovery task, sensor not blocked | DONE (main.cpp:recoveryTask, recoveryQueue) |
| 3.4 | MPU6050 Failure Handling | Degraded mode after 5 invalid reads, suppress streaming | DONE (sensor.cpp:g_sensorDegraded) |
| 3.5 | LED PWM Control | LEDC 8-bit PWM on GPIO2, smooth sine breathing | DONE (led.cpp, LEDMode::STREAMING) |
| 3.6 | Haptic Intensity Control | LEDC PWM on GPIO32, configurable intensity | DONE (led.cpp:triggerShotFeedback, s_hapticIntensity) |
| 3.7 | Build Timestamp | PlatformIO build flag | DONE (build_timestamp.py: BUILD_TIMESTAMP) |
| 3.8 | RX Buffer Overflow | Increase to 1024 bytes, overflow counter in health | DONE (bluetooth.h:1024, s_rxOverflowCount) |
| 3.9 | TX Flow Control | XON/XOFF when TX queue >48 items | DONE (main.cpp:bluetoothTask XON/XOFF as raw bytes) |
| 3.10 | Stack Overflow Detection | Enable `configCHECK_FOR_STACK_OVERFLOW`, free stack in health | DONE (platformio.ini:configCHECK_FOR_STACK_OVERFLOW=2) |
| 3.11 | Coredump on Fatal Errors | esp_core_dump partition, CMD_GET/ERASE_COREDUMP | DONE (src/coredump.cpp) |
| 3.12 | Fix Stale Session State | `handleStartSession` stops stale session before starting fresh | DONE (src/bluetooth.cpp:handleStartSession) |
| 3.13 | Fix DATA_RAW_SAMPLE Struct | Parser struct format corrected to match firmware byte layout | DONE (companion_app/stasys/protocol/parser.py) |
| 3.14 | Parser Debug Logging | `PARSER_DEBUG` flag + `set_debug()` for per-packet trace | DONE (companion_app/stasys/protocol/parser.py) |
| 3.15 | Fix RX CRC Accumulation | protocol.cpp: feed TYPE+LEN as continuous bytes, not XOR of per-byte CRCs | DONE (src/protocol.cpp:READ_LEN_LO/HI) |

### Phase 4: Auto-Calibration
| # | Item | Description | Status |
|---|------|-------------|--------|
| 4.1 | Factory Calibration | 500 samples at boot, compute accel/gyro bias, store in NVS | DONE (sensor.cpp:runFactoryCalibration, auto-run on first boot) |
| 4.2 | User Calibration Routine | Guided calibration via app, quality score | DONE (sensor.cpp:runUserCalibration, CMD_CALIBRATE_START/STATUS) |
| 4.3 | Adaptive Shot Thresholds | Statistical analysis of shots, self-tune thresholds | DONE (shot_detector.cpp:adaptive_threshold ring buffer, CMD_GET_SHOT_STATS) |
| 4.4 | Temperature Compensation | Temp coeff calibration, linear correction per sample | DONE (sensor.cpp:temperature compensation in readSensorBurst) |
| 4.5 | Mount Position Calibration | Rotation matrix for rail orientation variants | DONE (sensor.cpp:applyMountRotation, CMD_SET_MOUNT_MODE) |

### Phase 5: Ecosystem
| # | Item | Description | Status |
|---|------|-------------|--------|
| 5.1 | Mobile App | Flutter/React Native — BLE, real-time plot, session history | TODO |
| 5.2 | Session Data Export | JSON/CSV/binary export via BT, USB, or app sharing | DONE (companion_app/stasys/storage/export.py) |
| 5.3 | Cloud Backend | REST API, JWT auth, session upload, user stats | TODO |
| 5.4 | Analysis Features | Split times, group size estimation, recoil analysis, scores | DONE (companion_app/stasys/storage/analysis.py) |
| 5.5 | Multi-User Accounts | User accounts, device pairing, leaderboards | TODO |

### Phase 6: Regulatory Compliance
| # | Item | Description | Status |
|---|------|-------------|--------|
| 6.1 | FCC/CE RF Certification | BT Part 15C / CE RED testing (~$8-20K) | TODO |
| 6.2 | EMC Testing | CISPR 32 / FCC Part 15B (~$5-10K) | TODO |
| 6.3 | ESD Protection | TVS diodes on all exposed I/O, IEC 61000-4-2 | TODO |
| 6.4 | Electrical Safety | UL 2054, IEC 62368-1, PTC fuse, ground continuity | TODO |
| 6.5 | Battery Certification | IEC 62133 + UN 38.3 (pre-certified cells recommended) | TODO |
| 6.6 | RoHS/REACH | EU environmental compliance, Prop 65 (CA) | TODO |
| 6.7 | IP54 Rating | Conformal coating, gasket, Gore-Tex vent, IP54 test | TODO |
| 6.8 | Enclosure Design | Custom PCB (ESP32-WROOM), injection mold, Picatinny mount, drop/vibration test | TODO |
| 6.9 | Compliance Docs | Declaration of Conformity, test reports, user manual, SBOM, EULA | TODO |

### Implementation Order
```
Phase 1 (Security)  →  Phase 2 (Core)  →  Phase 3 (Reliability)
     ↓                    ↓                   ↓
  Before field       Basic product        Stabilize before
  deployment            features           UX iteration

Phase 4 (Calibration)  →  Phases 5+6 (Ecosystem / Regulatory)
         ↓                         ↓
   Polish before              Launch readiness
   public testing
```
Work top-to-bottom within each phase. Run `pio run` after each item.

### Deferred (Post-Launch)
- BLE GATT protocol migration (lower power, mobile-native)
- Multi-device sync (shooter + coach devices)
- AR/VR overlay integration
- ML shot quality scoring
