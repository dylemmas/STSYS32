# STASYS ESP32

Market-ready ESP32 firmware for shooter athlete muzzle trace and shot detection.

## Firmware Version
v1.0.0

## Hardware
- ESP32 DEVKIT V1
- MPU6050 (IMU: 6-axis accel+gyro, I2C)
- TP4056 (LiPo charge module)
- Piezoelectric vibration sensor
- LiPo 3.7V battery
- Picatinny rail mount

## Features
- Interrupt-driven MPU6050 sensor reading (1kHz)
- Dual-threshold shot detection (piezo + accelerometer)
- Configurable sample rate (50/100/200 Hz)
- Configurable shot detection thresholds
- Session management (start/stop, shot count, duration)
- Bluetooth Classic SPP binary protocol
- LED + haptic feedback on shot detection
- Battery monitoring with charging status
- NVS-based persistent configuration
- Watchdog timers for reliability
- I2C error recovery
