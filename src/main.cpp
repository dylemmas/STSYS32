#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
#include <nvs_flash.h>
#include <esp_system.h>
#include <esp_efuse.h>
#include <esp_flash_encrypt.h>
#include <esp_efuse_table.h>

// Core headers
#include "protocol.h"
#include "sensor.h"
#include "config.h"
#include "session.h"
#include "shot_detector.h"
#include "security.h"
#include "bluetooth.h"
#include "battery.h"
#include "led.h"
#include "storage.h"
#include "ota.h"

// ================= CONFIGURATION =================
#define SENSOR_TASK_STACK   4096
#define DETECTOR_TASK_STACK  4096
#define STREAM_TASK_STACK    2048
#define BATTERY_TASK_STACK   1024
#define RECOVERY_TASK_STACK 2048

#define BATTERY_INTERVAL_MS  30000UL  // 30s between battery reads

// ================= QUEUES =================
QueueHandle_t sampleQueue    = NULL;
QueueHandle_t shotEventQueue = NULL;
QueueHandle_t streamQueue    = NULL;  // Separate queue for streaming (Fix: stream task)

// ================= STATS =================
static uint32_t s_sampleCounter = 0;
static uint32_t s_droppedSamples = 0;
static uint32_t s_shotsDetected = 0;
static uint32_t s_lastBatteryUpdate = 0;
static uint32_t s_lastActivityTime = 0;
static bool s_sleepScheduled = false;

#define IDLE_TIMEOUT_MS (5UL * 60 * 1000)  // 5 minutes idle → sleep

// ================= POWER MANAGEMENT =================
static void recordActivity() {
    s_lastActivityTime = millis();
    s_sleepScheduled = false;
}

static void checkIdleSleep() {
    uint32_t now = millis();

    // Don't sleep while connected, streaming, or charging
    if (g_btConnected) return;
    if (getSessionState() == SessionState::STREAMING) return;
    if (isBatteryCharging()) return;

    if (!s_sleepScheduled) {
        if (now - s_lastActivityTime > IDLE_TIMEOUT_MS) {
            Serial.println("[PWR] Idle timeout reached, entering light sleep...");
            s_sleepScheduled = true;

            // Light sleep for 10 seconds, wake and recheck
            esp_sleep_enable_timer_wakeup(10 * 1000000ULL);
            esp_light_sleep_start();

            // On wake
            s_sleepScheduled = false;
            recordActivity();
            Serial.println("[PWR] Woke from light sleep");
        }
    }
}

// ================= FIRMWARE VERSION =================
#define FIRMWARE_VERSION_MAJOR 1
#define FIRMWARE_VERSION_MINOR 0
#define FIRMWARE_VERSION_PATCH 0
#define FIRMWARE_VERSION ((FIRMWARE_VERSION_MAJOR << 16) | (FIRMWARE_VERSION_MINOR << 8) | FIRMWARE_VERSION_PATCH)

// ================= TASK FUNCTIONS =================

// --- Recovery Task (Core 1, Medium Priority) ---
// Runs I2C bus recovery asynchronously so sensor task isn't blocked
void recoveryTask(void* param) {
    (void)param;
    Serial.println("[RECOVERY] Task started");
    esp_task_wdt_add(NULL);

    for (;;) {
        // Guard against recoveryQueue not being created (MPU6050 may not be detected)
        if (recoveryQueue != NULL) {
            bool signal;
            if (xQueueReceive(recoveryQueue, &signal, pdMS_TO_TICKS(1000)) == pdTRUE) {
                Serial.println("[RECOVERY] I2C error detected, attempting recovery...");
                // Run synchronous recovery (this blocks the recovery task, not sensor task)
                recoverI2CBus();
                s_consecutiveErrors = 0;  // Reset from sensor.cpp's view
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(1000));  // Wait without busy-waiting
        }
    }
}

// --- Sensor Task (Core 1, High Priority) ---
void sensorTask(void* param) {
    (void)param;

    Serial.println("[SENSOR] Task started");
    esp_task_wdt_add(NULL);

    SensorSample sample;

    for (;;) {
        // Block on data-ready semaphore (set by ISR)
        // Guard against NULL semaphore when MPU6050 is not detected
        if (dataReadySem != NULL && xSemaphoreTake(dataReadySem, pdMS_TO_TICKS(5)) == pdTRUE) {
            // Read sensor immediately
            if (readSensorBurst(&sample)) {
                // Push to both queues (non-blocking). Each consumer drains its own queue.
                if (sampleQueue != NULL) {
                    if (xQueueSendToBack(sampleQueue, &sample, 0) != pdTRUE) {
                        s_droppedSamples++;
                    }
                }
                if (streamQueue != NULL) {
                    xQueueSendToBack(streamQueue, &sample, 0);
                }
                s_sampleCounter++;
            }
        } else {
            // Timeout or no semaphore: sensor stalled. Do a polling fallback read.
            if (readSensorBurst(&sample)) {
                if (sampleQueue != NULL) {
                    xQueueSendToBack(sampleQueue, &sample, 0);
                }
                if (streamQueue != NULL) {
                    xQueueSendToBack(streamQueue, &sample, 0);
                }
                s_sampleCounter++;
            }
        }
        // Always reset WDT to prevent timeout even when sensor is absent
        esp_task_wdt_reset();
    }
}

// --- Shot Detector Task (Core 1, Medium Priority) ---
void shotDetectorTask(void* param) {
    (void)param;

    Serial.println("[DETECTOR] Task started");
    esp_task_wdt_add(NULL);

    SensorSample sample;
    ShotEvent event;
    FirmwareConfig cfg;
    uint32_t configRefreshCounter = 0;

    // Initial config load
    getConfigCopy(&cfg);
    updateShotDetectorConfig(&cfg);

    for (;;) {
        if (sampleQueue != NULL && xQueueReceive(sampleQueue, &sample, pdMS_TO_TICKS(10)) == pdTRUE) {
            // Periodically refresh config (every 100 samples)
            if (++configRefreshCounter >= 100) {
                getConfigCopy(&cfg);
                updateShotDetectorConfig(&cfg);
                configRefreshCounter = 0;
            }

            // Only process if session is active
            if (getSessionState() == SessionState::STREAMING) {
                bool shotDetected = processSample(&sample, &event,
                                                  g_lastSession.session_id);
                if (shotDetected) {
                    s_shotsDetected++;
                    addShotToSession(&event);

                    // Send shot event packet via TX queue (non-blocking)
                    sendPacket(PKT_TYPE_EVT_SHOT_DETECTED, &event, sizeof(event));

                    // LED + haptic feedback
                    triggerShotFeedback();

                    // Also send shot via event queue for any other consumers
                    if (shotEventQueue != NULL) {
                        xQueueSendToBack(shotEventQueue, &event, 0);
                    }
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(1));
        esp_task_wdt_reset();  // Prevent WDT timeout during idle polling
    }
}

// --- Stream Task (Core 0, Low Priority) ---
void streamTask(void* param) {
    (void)param;

    Serial.println("[STREAM] Task started");
    esp_task_wdt_add(NULL);

    SensorSample sample;
    FirmwareConfig cfg;
    uint32_t lastStreamTime_us = 0;
    uint32_t streamInterval_us = 10000; // default 100Hz
    uint32_t configRefreshCounter = 0;

    getConfigCopy(&cfg);

    for (;;) {
        uint32_t now = esp_timer_get_time();

        // Refresh config periodically
        if (++configRefreshCounter >= 500) {
            getConfigCopy(&cfg);
            streamInterval_us = 1000000 / cfg.streaming_rate_hz;
            configRefreshCounter = 0;
        }

        // Check data mode: 0=both, 1=raw-only, 2=events-only
        if (cfg.data_mode == 2) {
            // Events only — drain the stream queue to avoid buildup
            if (streamQueue != NULL) {
                while (xQueueReceive(streamQueue, &sample, 0) == pdTRUE) {}
            }
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        if (getSessionState() == SessionState::STREAMING) {
            if (now - lastStreamTime_us >= streamInterval_us) {
                lastStreamTime_us = now;

                // Drain all queued samples except the last one (latest)
                SensorSample lastSample;
                bool gotSample = false;
                if (streamQueue != NULL) {
                    while (xQueueReceive(streamQueue, &sample, 0) == pdTRUE) {
                        lastSample = sample;
                        gotSample = true;
                    }
                }

                // Send the latest sample (non-blocking via TX queue)
                if (gotSample) {
                    PktRawSample pkt;
                    pkt.sample_counter = s_sampleCounter;
                    pkt.timestamp_us = lastSample.timestamp_us;
                    pkt.accel_x = lastSample.accel_x;
                    pkt.accel_y = lastSample.accel_y;
                    pkt.accel_z = lastSample.accel_z;
                    pkt.gyro_x = lastSample.gyro_x;
                    pkt.gyro_y = lastSample.gyro_y;
                    pkt.gyro_z = lastSample.gyro_z;
                    pkt.piezo = lastSample.piezo;
                    pkt.temperature = lastSample.temperature;

                    sendPacket(PKT_TYPE_DATA_RAW_SAMPLE, &pkt, sizeof(pkt));
                }
            }
        } else {
            // Not streaming — drain stream queue to prevent buildup
            if (streamQueue != NULL) {
                while (xQueueReceive(streamQueue, &sample, 0) == pdTRUE) {}
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1));
        esp_task_wdt_reset();
    }
}

// --- Battery Monitor Task (Core 0, Low Priority) ---
void batteryMonitorTask(void* param) {
    (void)param;

    Serial.println("[BATTERY] Task started");
    esp_task_wdt_add(NULL);

    int lastReportedVal = -1;

    for (;;) {
        updateBattery();
        uint8_t currentVal = getBatteryPercent();

        // Reset idle timer (periodic activity)
        recordActivity();

        if (lastReportedVal < 0 || abs((int)currentVal - (int)lastReportedVal) >= 2) {
            lastReportedVal = currentVal;
        }

        // Low battery warning via LED
        BatteryStatus st = readBattery();
        if (st.isLow && !st.isCharging) {
            setLEDMode(LEDMode::LOW_BATTERY);
        }

        // Periodic health report
        if (s_sampleCounter > 0 && (s_sampleCounter % 10000 == 0)) {
            Serial.printf("[STATS] samples=%lu dropped=%lu shots=%lu batt=%d%%\n",
                         s_sampleCounter, s_droppedSamples, s_shotsDetected, currentVal);
        }

        vTaskDelay(pdMS_TO_TICKS(BATTERY_INTERVAL_MS / portTICK_PERIOD_MS));
        esp_task_wdt_reset();
    }
}

// --- Bluetooth Task (Core 0, High Priority) ---
void bluetoothTask(void* param) {
    (void)param;

    Serial.println("[BT] Task started");
    esp_task_wdt_add(NULL);

    DecodedPacket cmd;
    uint32_t lastHeartbeat_ms = 0;
    const uint32_t HEARTBEAT_INTERVAL_MS = 5000;  // Send health every 5s during session
    bool wasConnected = false;

    for (;;) {
        // Check connection state
        bool connected = SerialBT.connected();
        if (connected != g_btConnected) {
            g_btConnected = connected;
            if (connected) {
                Serial.println("[BT] Connected");
                setLEDMode(LEDMode::CONNECTED);
                // Clear stale RX data from previous connection
                s_rxLen = 0;

                // Auto-stop any active session on disconnect
                if (getSessionState() == SessionState::STREAMING) {
                    stopSession();
                    PktSessionStopped pkt;
                    pkt.session_id = g_lastSession.session_id;
                    pkt.duration_ms = g_lastSession.duration_ms;
                    pkt.shot_count = g_lastSession.shot_count;
                    pkt.battery_end = getBatteryPercent();
                    pkt.sensor_health = getSensorHealthFlags();
                    sendPacket(PKT_TYPE_EVT_SESSION_STOPPED, &pkt, sizeof(pkt));
                }
                wasConnected = true;
            } else {
                Serial.println("[BT] Disconnected");
                // Fix: Immediately restart advertising for automatic reconnection.
                // ESP32 continues to advertise as "STASYS" so Android can reconnect
                // without power cycling the device.
                esp_err_t err = esp_bt_gap_set_scan_mode(
                    ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
                if (err != ESP_OK) {
                    Serial.printf("[BT] Re-advertise failed: %d, restarting SPP...\n", err);
                    // If advertising can't be restored, restart the BT serial service
                    FirmwareConfig cfg;
                    getConfigCopy(&cfg);
                    SerialBT.begin(cfg.device_name);
                    esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
                } else {
                    Serial.println("[BT] Re-advertising for reconnect...");
                }
                setLEDMode(LEDMode::IDLE);
                // Clear RX buffer on disconnect
                s_rxLen = 0;
                lastHeartbeat_ms = 0;  // Reset heartbeat on disconnect
            }
        }

        // Heartbeat: send sensor health every 5s during active sessions.
        // This lets the Android app detect dead connections and serves as keepalive.
        if (g_btConnected && getSessionState() == SessionState::STREAMING) {
            uint32_t now = millis();
            if (now - lastHeartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
                lastHeartbeat_ms = now;
                sendSensorHealthPacket();
                Serial.println("[BT] Heartbeat sent");
            }
        }

        // Read incoming BT data
        int available = SerialBT.available();
        if (available > 0) {
            int toRead = min(available, (int)(sizeof(s_rxBuffer) - s_rxLen));
            if (toRead == 0) {
                // Buffer full — don't discard everything, just wait for drain
                s_rxOverflowCount++;
                Serial.printf("[BT] RX overflow #%lu (buffer full)\n", s_rxOverflowCount);
                vTaskDelay(pdMS_TO_TICKS(5));  // Let buffer drain
                continue;
            }
            int bytesRead = SerialBT.readBytes(s_rxBuffer + s_rxLen, toRead);
            if (bytesRead > 0) {
                s_rxLen += bytesRead;

                // Parse through buffer
                uint16_t consumed = 0;
                for (uint16_t i = 0; i < s_rxLen; ) {
                    bool found = false;
                    uint16_t best = 0;

                    // Try to find complete packet
                    for (uint16_t j = 0; j < s_rxLen - i; j++) {
                        if (decodeByte(s_rxBuffer[i + j], &cmd)) {
                            found = true;
                            best = i + j + 1;
                            dispatchCommand(&cmd);
                            break;
                        }
                    }

                    if (found) {
                        consumed = best;
                        // Shift remaining data
                        memmove(s_rxBuffer, s_rxBuffer + consumed, s_rxLen - consumed);
                        s_rxLen -= consumed;
                        consumed = 0;
                        break;
                    } else {
                        // Check if sync byte 0xAA is in buffer, shift to it
                        bool foundSync = false;
                        for (uint16_t j = 1; j < s_rxLen - i; j++) {
                            if (s_rxBuffer[i + j] == SYNC_BYTE_0) {
                                memmove(s_rxBuffer, s_rxBuffer + i + j,
                                        s_rxLen - i - j);
                                s_rxLen -= (i + j);
                                foundSync = true;
                                break;
                            }
                        }
                        if (!foundSync) {
                            s_rxLen = 0; // No sync found, discard
                        }
                        break;
                    }
                }
            }
        }

        // Send outgoing TX queue packets + flow control
        TXItem txItem;
        UBaseType_t qlen = uxQueueMessagesWaiting(txQueue);

        // Flow control: send raw XON/XOFF bytes outside packet framing.
        // Fix: was using type 0x14 (EVT_AUTH_CHALLENGE) as a packet type,
        // which conflicted with the protocol. Now sending as raw RFCOMM bytes.
        static bool s_flowPaused = false;
        if (s_flowPaused && qlen < 16) {
            SerialBT.write(0x11);  // XON (ASCII DC1)
            s_flowPaused = false;
            Serial.println("[BT] XON sent (raw)");
        }

        while (xQueueReceive(txQueue, &txItem, 0) == pdTRUE) {
            SerialBT.write(txItem.data, txItem.length);
            // XOFF when queue exceeds 48
            if (!s_flowPaused && uxQueueMessagesWaiting(txQueue) > 48) {
                SerialBT.write(0x13);  // XOFF (ASCII DC3) — sent as raw byte
                s_flowPaused = true;
                Serial.println("[BT] XOFF sent (raw)");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1));
        esp_task_wdt_reset();
    }
}

// ================= JTAG DISABLE =================
// Permanently disables JTAG interface via efuses.
// Call AFTER security init, before any task starts (in case JTAG was used
// to flash the chip). Burns DIS_JTAG efuse bit — this is IRREVERSIBLE.
// If JTAG is already disabled, this is a no-op (safe to call every boot).
// NOTE: JTAG can only be disabled via efuse burn, not software reset.
//       This function is safe because burning an already-set efuse bit is a no-op.
static void disableJTAG() {
    esp_err_t err = esp_efuse_write_field_bit(ESP_EFUSE_DISABLE_JTAG);
    if (err == ESP_OK) {
        Serial.println("[MAIN] JTAG disabled via efuse");
    } else if (err == ESP_ERR_EFUSE_REPEATED_PROG) {
        // Already disabled — this is the expected case on subsequent boots
        Serial.println("[MAIN] JTAG already disabled (efuse burned)");
    } else {
        Serial.printf("[MAIN] JTAG disable warning: %d\n", err);
    }
}
// ESP32 flash encryption uses transparent XTS-128 — the CPU always sees
// plaintext. Raw flash dumps are unreadable when flash encryption is
// enabled via efuse. All NVS namespaces (config, battery, sensor, calib)
// are automatically encrypted at rest without additional key management.
// This function checks the flash encryption status and initializes NVS.
static void initNVS() {
    if (esp_flash_encryption_enabled()) {
        Serial.println("[MAIN] NVS: transparent flash encryption active (XTS-128)");
    }

    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES ||
        nvs_err == ESP_ERR_INVALID_STATE) {
        nvs_flash_erase();
        nvs_err = nvs_flash_init();
    }
    if (nvs_err == ESP_OK) {
        Serial.println("[MAIN] NVS: initialized");
    } else {
        Serial.printf("[MAIN] NVS: init failed (%d)\n", nvs_err);
    }
}

static void checkSecurityStatus() {
    bool efuse_provisioned = isDeviceSecretProvisioned();
    if (!efuse_provisioned) {
        Serial.println("[MAIN] SECURITY: WARNING — no efuse device secret provisioned");
        Serial.println("[MAIN] SECURITY: WARNING — using MAC-based secret (dev mode only)");
    } else {
        Serial.println("[MAIN] SECURITY: efuse device secret detected (production mode)");
    }

    if (esp_flash_encryption_enabled()) {
        Serial.println("[MAIN] SECURITY: flash encryption ENABLED");
    } else {
        Serial.println("[MAIN] SECURITY: flash encryption NOT enabled (run secure boot script)");
    }
}
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.printf("\n\n=== STASYS ESP32 v%d.%d.%d ===\n",
                 FIRMWARE_VERSION_MAJOR, FIRMWARE_VERSION_MINOR, FIRMWARE_VERSION_PATCH);
    Serial.println("[MAIN] Initializing...");

    // Initialize NVS (encrypted if flash encryption is enabled via efuse)
    initNVS();

    // Check and report security provisioning status
    checkSecurityStatus();

    // Disable JTAG interface (irreversible, safe to call on every boot)
    disableJTAG();

    // Load persistent config
    initConfig();

    // Create data-ready semaphore unconditionally (needed even if MPU6050 absent)
    if (dataReadySem == NULL) {
        dataReadySem = xSemaphoreCreateBinary();
        if (dataReadySem == NULL) {
            Serial.println("[MAIN] ERROR: Failed to create data ready semaphore");
        }
    }

    // Scan I2C bus to identify all connected devices
    scanI2CBus();

    // Initialize sensor
    bool sensorOk = initMPU6050();
    if (!sensorOk) {
        Serial.println("[MAIN] WARNING: MPU6050 not detected — running in degraded mode");
    }

    // Auto-run factory calibration on first boot (Phase 3.1)
    CalibrationData cal;
    loadCalibrationData(&cal);
    if (!cal.is_calibrated && !cal.factory_calibrated) {
        Serial.println("[MAIN] No calibration data found — running factory calibration...");
        runFactoryCalibration();
    } else {
        Serial.println("[MAIN] Calibration data loaded");
    }

    // Initialize battery
    initBattery();
    Serial.printf("[MAIN] Battery: %d%%\n", getBatteryPercent());

    // Initialize LED
    initLED();

    // Initialize storage
    initStorage();

    // Get device name from config
    FirmwareConfig cfg;
    getConfigCopy(&cfg);

    // Initialize security (before BT)
    initSecurity();

    // Initialize Bluetooth
    initBluetooth(cfg.device_name);

    // Create queues
    sampleQueue = xQueueCreate(64, sizeof(SensorSample));
    shotEventQueue = xQueueCreate(32, sizeof(ShotEvent));
    streamQueue = xQueueCreate(64, sizeof(SensorSample));  // Fix: separate queue for stream task

    if (sampleQueue == NULL || shotEventQueue == NULL) {
        Serial.println("[MAIN] ERROR: Failed to create queues");
    }

    // Initialize shot detector
    initShotDetector();

    // Initialize system watchdog BEFORE creating tasks (fixes WDT race condition)
    // Each task subscribes via esp_task_wdt_add(NULL) at its start
    esp_task_wdt_init(60, true); // 60 second timeout, panic on timeout

    // Create FreeRTOS tasks
    BaseType_t res;

    res = xTaskCreatePinnedToCore(
        recoveryTask,
        "RecoveryTask",
        RECOVERY_TASK_STACK,
        NULL,
        2,        // Medium priority
        NULL,
        1         // Core 1
    );
    Serial.printf("[MAIN] RecoveryTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    res = xTaskCreatePinnedToCore(
        sensorTask,
        "SensorTask",
        SENSOR_TASK_STACK,
        NULL,
        3,        // High priority
        NULL,
        1         // Core 1
    );
    Serial.printf("[MAIN] SensorTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    res = xTaskCreatePinnedToCore(
        shotDetectorTask,
        "ShotDetector",
        DETECTOR_TASK_STACK,
        NULL,
        2,        // Medium priority
        NULL,
        1         // Core 1
    );
    Serial.printf("[MAIN] ShotDetectorTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    res = xTaskCreatePinnedToCore(
        streamTask,
        "StreamTask",
        STREAM_TASK_STACK,
        NULL,
        1,        // Low priority
        NULL,
        0         // Core 0
    );
    Serial.printf("[MAIN] StreamTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    res = xTaskCreatePinnedToCore(
        batteryMonitorTask,
        "BatteryMonitor",
        BATTERY_TASK_STACK,
        NULL,
        1,        // Low priority
        NULL,
        0         // Core 0
    );
    Serial.printf("[MAIN] BatteryMonitorTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    res = xTaskCreatePinnedToCore(
        bluetoothTask,
        "BluetoothTask",
        4096,
        NULL,
        2,        // High priority
        NULL,
        0         // Core 0
    );
    Serial.printf("[MAIN] BluetoothTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    // Create LED task on core 0
    res = xTaskCreatePinnedToCore(
        ledTask,
        "LEDTask",
        2048,
        NULL,
        1,
        NULL,
        0
    );
    Serial.printf("[MAIN] LEDTask created: %s\n", (res == pdPASS) ? "OK" : "FAIL");

    Serial.println("[MAIN] Setup complete — ready for connections");
    Serial.printf("[MAIN] Heap free: %lu bytes\n", esp_get_free_heap_size());
}

void loop() {
    // All work is done in FreeRTOS tasks
    // Power management: check idle sleep periodically
    checkIdleSleep();
    vTaskDelay(pdMS_TO_TICKS(1000));
}
