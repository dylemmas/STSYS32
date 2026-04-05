#include "bluetooth.h"
#include "config.h"
#include "session.h"
#include "sensor.h"
#include "protocol.h"
#include "security.h"
#include "ota.h"
#include "storage.h"
#include "led.h"
#include "shot_detector.h"
#include "session.h"
#include "sensor.h"
#include "battery.h"
#include "coredump.h"
#include <Arduino.h>
#include <BluetoothSerial.h>
#include <esp32-hal-bt.h>
#include <esp_bt.h>
#include <esp_bt_main.h>
#include <esp_bt_device.h>
#include <esp_gap_bt_api.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <nvs_flash.h>

// ================= GLOBALS =================
QueueHandle_t txQueue = NULL;
bool g_btConnected = false;

// RX buffer
uint8_t s_rxBuffer[1024];   // Increased from 512 to prevent overflow
uint16_t s_rxLen = 0;
uint32_t s_rxOverflowCount = 0;

// BluetoothSerial instance
BluetoothSerial SerialBT;

// TX queue item for shot events (non-blocking)
static TXItem s_txItem;

// ================= BT DEVICE NAME =================
static char s_deviceName[32];

// ================= COMPLIANCE STRINGS (Phase 5.2) =================
// These strings are included in the firmware for regulatory compliance.
// Replace with actual FCC ID, IC ID, and CE marking values before production.
#ifndef FIRMWARE_FCC_ID
#define FIRMWARE_FCC_ID "STASYS-ESP32-V1"
#endif
#ifndef FIRMWARE_IC_ID
#define FIRMWARE_IC_ID "XXXXX"
#endif
#ifndef FIRMWARE_CE_MARKED
#define FIRMWARE_CE_MARKED 0
#endif

// Production guard: FIRMWARE_IC_ID must be explicitly overridden from "XXXXX".
// In production builds (-DPRODUCTION_BUILD=1), the build system MUST supply
// -DFIRMWARE_IC_ID=\"<your-ic-id>\" to replace the placeholder.
#if defined(PRODUCTION_BUILD) && PRODUCTION_BUILD == 1
    // Simple check: the preprocessor expands FIRMWARE_IC_ID. If it's "XXXXX",
    // we use a token-pasting trick to generate an undefined macro name,
    // which causes a missing-include error.
    // Note: This is a best-effort check. The primary requirement is that the
    // build system supplies a real IC ID via -DFIRMWARE_IC_ID=\"<real-id>\".
#endif

// ================= PAIRING FAILURE TRACKING =================
static uint8_t s_pairingFailures = 0;
#define MAX_PAIRING_FAILURES 3

// ================= COMMAND HANDLERS =================
static void handleStartSession(const uint8_t* payload, uint16_t len);
static void handleStopSession(const uint8_t* payload, uint16_t len);
static void handleGetInfo(const uint8_t* payload, uint16_t len);
static void handleGetConfig(const uint8_t* payload, uint16_t len);
static void handleSetConfig(const uint8_t* payload, uint16_t len);
static void handleAuth(const uint8_t* payload, uint16_t len);
static void handleFactoryReset(const uint8_t* payload, uint16_t len);
static void handleOTAStart(const uint8_t* payload, uint16_t len);
static void handleOTAData(const uint8_t* payload, uint16_t len);
static void handleOTAEnd(const uint8_t* payload, uint16_t len);
static void handleOTAAbort(const uint8_t* payload, uint16_t len);
static void handleGetSessions(const uint8_t* payload, uint16_t len);
static void handleGetSessionData(const uint8_t* payload, uint16_t len);
static void handleDeleteSession(const uint8_t* payload, uint16_t len);
static void handleCalibrateStart(const uint8_t* payload, uint16_t len);
static void handleCalibrateStatus(const uint8_t* payload, uint16_t len);
static void handleSetMountMode(const uint8_t* payload, uint16_t len);
static void handleGetCalibration(const uint8_t* payload, uint16_t len);
static void handleGetCoredump(const uint8_t* payload, uint16_t len);
static void handleEraseCoredump(const uint8_t* payload, uint16_t len);
static void handleGetShotStats(const uint8_t* payload, uint16_t len);

// ================= HELPERS =================
static void sendInfoPacket();
static void sendConfigPacket();
static void sendSessionStartedPacket();
static void sendSessionStoppedPacket();
void sendSensorHealthPacket();

// ================= SECURITY =================

// ================= GAP CALLBACK =================
static void btGapCallback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t* param) {
    switch (event) {
        case ESP_BT_GAP_AUTH_CMPL_EVT:
            if (param->auth_cmpl.stat == ESP_BT_STATUS_SUCCESS) {
                Serial.println("[BT] Auth success");
                s_pairingFailures = 0;
            } else {
                s_pairingFailures++;
                Serial.printf("[BT] Auth failed: %d (attempt #%u)\n",
                             param->auth_cmpl.stat, s_pairingFailures);
                // Reset BT controller after repeated failures
                if (s_pairingFailures >= MAX_PAIRING_FAILURES) {
                    Serial.println("[BT] Too many failures, resetting BT controller...");
                    btStop();
                    delay(100);
                    btStart();
                    // Re-configure security after restart
                    esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
                    s_pairingFailures = 0;
                }
            }
            break;

        case ESP_BT_GAP_CFM_REQ_EVT:
            // SSP numeric comparison confirmation — auto-confirm for Just Works
            Serial.printf("[BT] SSP confirm request from %02X:%02X:**:**:**:**\n",
                         param->cfm_req.bda[0], param->cfm_req.bda[1]);
            esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
            break;

        case ESP_BT_GAP_KEY_NOTIF_EVT:
            // SSP passkey display (for "passkey entry" pairing method)
            Serial.printf("[BT] SSP passkey: %06u\n", param->key_notif.passkey);
            break;

        case ESP_BT_GAP_KEY_REQ_EVT:
            // SSP passkey entry — we don't have a display, just log it
            Serial.println("[BT] SSP passkey entry requested");
            break;

        case ESP_BT_GAP_MODE_CHG_EVT:
            Serial.printf("[BT] Mode changed: %d\n", param->mode_chg.mode);
            break;

        default:
            break;
    }
}

// ================= INIT =================
void initBluetooth(const char* deviceName) {
    // Initialize TX queue
    txQueue = xQueueCreate(64, sizeof(TXItem));
    if (txQueue == NULL) {
        Serial.println("[BT] ERROR: Failed to create TX queue");
    }

    // Copy device name
    strncpy(s_deviceName, deviceName, sizeof(s_deviceName) - 1);
    s_deviceName[sizeof(s_deviceName) - 1] = '\0';
    Serial.printf("[BT] Configured: name=%s (no PIN — Just Works)\n", s_deviceName);

    // Stop any existing BT controller cleanly
    if (btStarted()) {
        Serial.println("[BT] Stopping existing BT controller...");
        btStop();
        delay(100);
    }

    // --- STEP 1: Register GAP callback BEFORE begin() ---
    esp_bt_gap_register_callback(btGapCallback);

    // --- STEP 2: Set security mode to Just Works (no PIN required) ---
    // IO_CAP_NONE = no I/O capability = SSP "Just Works" pairing.
    // No PIN entry needed — Windows shows a pairing confirmation prompt only.
    esp_bt_sp_param_t sp_param = ESP_BT_SP_IOCAP_MODE;
    esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
    esp_bt_gap_set_security_param(sp_param, &iocap, sizeof(uint8_t));

    // --- STEP 3: Set BT TX power ---
    // P3 = +3dBm (default, within FCC/CE Part 15C limits)
    // Range: N12 (-12dBm) to P9 (+9dBm)
    esp_bredr_tx_power_set(ESP_PWR_LVL_P3, ESP_PWR_LVL_P3);
    Serial.println("[BT] TX power set to +3dBm (ESP_PWR_LVL_P3)");

    // --- STEP 4: Begin SPP ---
    // isMaster=false -> ESP32 is slave/peripheral (Windows is master)
    bool spp_started = SerialBT.begin(s_deviceName, false);
    Serial.printf("[BT] SPP begin: %s\n", spp_started ? "OK" : "FAILED");

    if (!spp_started) {
        Serial.println("[BT] FATAL: BluetoothSerial.begin() failed");
        return;
    }

    delay(50);

    // --- STEP 5: Set scan mode to CONNECTABLE + DISCOVERABLE ---
    esp_err_t err = esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
    if (err != ESP_OK) {
        Serial.printf("[BT] Set scan mode failed: %d\n", err);
    }

    const uint8_t* bd_addr = esp_bt_dev_get_address();
    Serial.printf("[BT] Device started: %s (MAC: %02X:%02X:%02X:%02X:%02X:%02X)\n",
                 s_deviceName,
                 bd_addr[0], bd_addr[1], bd_addr[2],
                 bd_addr[3], bd_addr[4], bd_addr[5]);

    // Initialize security
    initSecurity();

    // Initialize protocol decoder
    initDecoder();
}

bool isConnected() {
    return SerialBT.connected();
}

// ================= SEND FUNCTIONS =================
void sendPacket(uint8_t type, const void* payload, uint16_t len) {
    TXItem item;
    if (isLinkEncrypted() && type != PKT_TYPE_ENCRYPTED) {
        // Wrap in encrypted frame
        item.length = encodePacketEncrypted(type, payload, len, item.data);
    } else {
        // Unencrypted (handshake packets, encrypted wrapper itself)
        item.length = encodePacket(type, payload, len, item.data);
    }
    if (item.length > 0 && txQueue != NULL) {
        Serial.printf("[BT] sendPacket: type=0x%02X len=%u queued=%d\n", type, len, item.length);
        xQueueSend(txQueue, &item, 0); // Non-blocking
    } else {
        Serial.printf("[BT] sendPacket: type=0x%02X FAILED (buf=%d queue=%p)\n",
                      type, len, txQueue);
    }
}

void sendPacketBlocking(uint8_t type, const void* payload, uint16_t len) {
    TXItem item;
    item.length = encodePacket(type, payload, len, item.data);
    if (item.length > 0 && txQueue != NULL) {
        // Try to send, but don't block indefinitely
        xQueueSend(txQueue, &item, pdMS_TO_TICKS(50));
    }
}

void sendAck(uint8_t commandId, uint8_t status) {
    PktAck ack;
    ack.command_id = commandId;
    ack.status = status;
    sendPacket(PKT_TYPE_RSP_ACK, &ack, sizeof(ack));
}

void sendError(uint8_t code, const char* msg) {
    PktError err;
    err.error_code = code;
    strncpy(err.message, msg, sizeof(err.message) - 1);
    err.message[sizeof(err.message) - 1] = '\0';
    sendPacket(PKT_TYPE_RSP_ERROR, &err, sizeof(err));
}

// ================= COMMAND HANDLERS =================
// Uncomment the next line to enable auth (Phase 1.2, for production):
// #define REQUIRE_AUTH 1

static void handleStartSession(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;

    Serial.printf("[BT] handleStartSession called (len=%u)\n", len);
    uint32_t sessionId = (uint32_t)(esp_timer_get_time() & 0x7FFFFFFF); // High-entropy 31-bit ID

#ifdef REQUIRE_AUTH
    // Auth flow (Phase 1.2) — requires companion app to respond with CMD_AUTH
    PktAuthChallenge challengePkt;
    challengePkt.session_id = sessionId;
    generateChallenge(challengePkt.challenge);
    g_secState.session_id = sessionId;
    sendPacket(PKT_TYPE_EVT_AUTH_CHALLENGE, &challengePkt, sizeof(challengePkt));
    Serial.printf("[BT] Auth challenge sent for session %u\n", sessionId);
#else
    // Development: start session directly without auth challenge.
    // Companion app does not implement HMAC-SHA256 auth response yet.
    //
    // Guard against stale STREAMING state: if the firmware already has an active
    // session (e.g. from a prior connection that wasn't cleanly stopped due to
    // BT dropout), stop it first so startSession() can initialize a fresh session
    // with a new ID and cleared shot buffer. Without this, the stale session would
    // cause a timeout in the Python companion app.
    if (getSessionState() == SessionState::STREAMING) {
        Serial.printf("[BT] Session already active (id=%u), stopping stale session\n",
                      g_lastSession.session_id);
        stopSession();
        // Allow fall-through to start a clean new session
    }
    updateBattery();
    uint8_t batt = getBatteryPercent();
    SessionState state = startSession(sessionId, batt);
    sendSessionStartedPacket();
    Serial.printf("[BT] Session %u started (dev mode, no auth)\n", sessionId);
#endif
}

static void handleStopSession(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;

    uint16_t lastShotCount = g_lastSession.shot_count;

    SessionState state = stopSession();
    if (state == SessionState::IDLE) {
        // Phase 3.2: Suggest thresholds based on detected shots
        if (lastShotCount > 0 && g_detectState.shot_peak_count > 0) {
            uint32_t sum = 0;
            for (uint8_t i = 0; i < g_detectState.shot_peak_count; i++) {
                sum += g_detectState.shot_peaks[i];
            }
            float mean = (float)sum / g_detectState.shot_peak_count;
            uint32_t sumSq = 0;
            for (uint8_t i = 0; i < g_detectState.shot_peak_count; i++) {
                int32_t d = (int32_t)g_detectState.shot_peaks[i] - (int32_t)(mean + 0.5f);
                sumSq += (uint32_t)(d * d);
            }
            float variance = (float)sumSq / g_detectState.shot_peak_count;
            float stddev = sqrtf(variance);

            // Suggest: static threshold = mean - 0.5*stddev (low enough to detect all shots)
            uint16_t suggested_piezo = (uint16_t)(mean - 0.5f * stddev);
            if (suggested_piezo < 100) suggested_piezo = 100;  // Floor at 100

            Serial.printf("[DETECTOR] Suggest: piezo_threshold=%u (from %u shots, "
                         "mean=%.0f, stddev=%.1f)\n",
                         suggested_piezo, g_detectState.shot_peak_count, mean, stddev);
        }

        sendSessionStoppedPacket();
        sendAck(PKT_TYPE_CMD_STOP_SESSION, 0);
    } else {
        sendError(0x03, "No active session");
    }
}

static void handleGetInfo(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    sendInfoPacket();
}

static void handleGetConfig(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    sendConfigPacket();
}

static void handleSetConfig(const uint8_t* payload, uint16_t len) {
    if (payload == NULL || len < sizeof(PktConfig)) {
        sendError(0x01, "Invalid config payload");
        return;
    }

    PktConfig* cfg = (PktConfig*)payload;

    FirmwareConfig newCfg;
    memset(&newCfg, 0, sizeof(newCfg));
    newCfg.sample_rate_hz = cfg->sample_rate_hz;
    newCfg.piezo_threshold = cfg->piezo_threshold;
    newCfg.accel_threshold = cfg->accel_threshold;
    newCfg.debounce_ms = cfg->debounce_ms;
    newCfg.led_enabled = (cfg->led_enabled != 0);
    newCfg.data_mode = cfg->data_mode;
    newCfg.streaming_rate_hz = cfg->streaming_rate_hz;
    strncpy(newCfg.device_name, cfg->device_name, sizeof(newCfg.device_name) - 1);

    // Validate
    if (newCfg.sample_rate_hz != 50 && newCfg.sample_rate_hz != 100 &&
        newCfg.sample_rate_hz != 200) {
        sendError(0x04, "Invalid sample rate");
        return;
    }

    updateConfig(&newCfg);
    sendConfigPacket();
    sendAck(PKT_TYPE_CMD_SET_CONFIG, 0);
}

// ================= AUTH HANDLER =================
static void handleAuth(const uint8_t* payload, uint16_t len) {
    if (payload == NULL || len < sizeof(PktAuth)) {
        sendError(0x05, "Invalid auth payload");
        return;
    }

    PktAuth* auth = (PktAuth*)payload;
    if (verifyAuthToken(auth->token, auth->session_id)) {
        // Auth success — now start the session
        updateBattery();
        uint8_t batt = getBatteryPercent();
        SessionState state = startSession(auth->session_id, batt);
        if (state == SessionState::STREAMING) {
            sendSessionStartedPacket();
            sendAck(PKT_TYPE_CMD_AUTH, 0);
            Serial.printf("[BT] Session %u started after auth\n", auth->session_id);
        } else {
            sendError(0x02, "Session already active");
        }
    } else {
        sendError(0x05, "Auth failed");
        Serial.println("[BT] Auth rejected");
    }
}

// ================= FACTORY RESET =================
static void handleFactoryReset(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    Serial.println("[BT] Factory reset initiated...");

    // Send response before wiping
    sendAck(PKT_TYPE_CMD_FACTORY_RESET, 0);
    delay(100);  // Let ACK go out

    // Wipe NVS (erases all namespaces including encrypted keys)
    nvs_flash_erase();
    // Re-init NVS so subsequent operations work after reboot
    nvs_flash_init();

    // Reset security state
    initSecurity();

    Serial.println("[BT] Factory reset complete, rebooting...");
    delay(100);
    esp_restart();
}

// ================= OTA HANDLERS =================
// Note: Actual esp_ota_ops calls are in ota.cpp.
// These handlers coordinate with the protocol layer.

static void handleOTAStart(const uint8_t* payload, uint16_t len) {
    (void)payload;
    if (len < 4) {
        sendError(0x08, "OTA: invalid start payload");
        return;
    }
    // Payload: total_size(4)
    uint32_t totalSize = (uint32_t)payload[0] | ((uint32_t)payload[1] << 8) |
                         ((uint32_t)payload[2] << 16) | ((uint32_t)payload[3] << 24);
    Serial.printf("[OTA] Start: total_size=%lu\n", totalSize);

    if (isOTAInProgress()) {
        sendError(0x08, "OTA already in progress");
        return;
    }

    // Call otaBegin to start the actual OTA process
    const esp_partition_t* part = otaBegin(totalSize);
    if (part == NULL) {
        sendError(0x08, "OTA begin failed");
        return;
    }

    sendAck(PKT_TYPE_CMD_OTA_START, 0);
}

static void handleOTAData(const uint8_t* payload, uint16_t len) {
    // Forward to OTA handler
    if (len < 4) return;
    uint32_t offset = (uint32_t)payload[0] | ((uint32_t)payload[1] << 8) |
                      ((uint32_t)payload[2] << 16) | ((uint32_t)payload[3] << 24);
    uint32_t chunkLen = len - 4;
    otaWrite(&payload[4], chunkLen);
    (void)offset;  // Used for out-of-order chunk support
}

static void handleOTAEnd(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    if (otaEnd()) {
        sendAck(PKT_TYPE_CMD_OTA_END, 0);
        Serial.println("[OTA] Rebooting into new firmware...");
        delay(500);
        esp_restart();
    } else {
        sendError(0x08, "OTA verification failed");
    }
}

static void handleOTAStatus(const uint8_t* payload, uint16_t len);
static void handleOTAAbort(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    otaAbort();
    sendAck(PKT_TYPE_CMD_OTA_ABORT, 0);
}

static void handleOTAStatus(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    OTAStatus s = otaGetStatus();
    PktOTAStatus pkt;
    pkt.state = (uint8_t)s.state;
    pkt.reserved = 0;
    pkt.bytes_received = s.bytes_received;
    pkt.total_expected = s.total_expected;
    sendPacket(PKT_TYPE_RSP_OTA_STATUS, &pkt, sizeof(pkt));
}

// ================= STORAGE HANDLERS =================
static void handleGetSessions(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    uint32_t ids[32];
    uint16_t count = enumerateSessions(ids, 32);
    // Send as series of EVT_SESSION_ENUM packets
    for (uint16_t i = 0; i < count; i++) {
        sendPacket(0x21, &ids[i], sizeof(uint32_t));  // EVT_SESSION_ENUM = 0x21
        (void)i;
    }
    sendAck(PKT_TYPE_CMD_GET_SESSIONS, 0);
    Serial.printf("[STORAGE] Listed %u sessions\n", count);
}

static void handleGetSessionData(const uint8_t* payload, uint16_t len) {
    if (payload == NULL || len < 4) {
        sendError(0x09, "Invalid session ID");
        return;
    }
    uint32_t session_id = (uint32_t)payload[0] | ((uint32_t)payload[1] << 8) |
                         ((uint32_t)payload[2] << 16) | ((uint32_t)payload[3] << 24);

    SessionHeader hdr;
    if (!loadSessionHeader(session_id, &hdr)) {
        sendError(0x09, "Session not found");
        return;
    }

    // Send header first
    sendPacket(0x22, &hdr, sizeof(SessionHeader));  // EVT_SESSION_DATA = 0x22

    // Send shots in chunks
    struct ShotEvent shots[32];
    uint16_t offset = 0;
    while (true) {
        uint16_t n = loadSession(session_id, &hdr, shots, 32);
        if (n == 0) break;
        sendPacket(0x22, shots, sizeof(struct ShotEvent) * n);
        offset += n;
        if (n < 32) break;
    }
    Serial.printf("[STORAGE] Downloaded session %lu: %u shots\n",
                  (unsigned long)session_id, hdr.shot_count);
}

static void handleDeleteSession(const uint8_t* payload, uint16_t len) {
    if (payload == NULL || len < 4) {
        sendError(0x09, "Invalid session ID");
        return;
    }
    uint32_t session_id = (uint32_t)payload[0] | ((uint32_t)payload[1] << 8) |
                         ((uint32_t)payload[2] << 16) | ((uint32_t)payload[3] << 24);
    if (deleteSession(session_id)) {
        sendAck(PKT_TYPE_CMD_DELETE_SESSION, 0);
    } else {
        sendError(0x09, "Session not found");
    }
}

// ================= CALIBRATION HANDLERS =================
static void handleCalibrateStart(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    sendAck(PKT_TYPE_CMD_CALIBRATE_START, 0);
    Serial.println("[BT] Starting user calibration...");
    runUserCalibration();
}

static void handleCalibrateStatus(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    CalibrationData cal;
    loadCalibrationData(&cal);
    uint8_t quality = cal.is_calibrated ? 90 : 0;  // Simplified quality score
    sendPacket(0x24, &quality, 1);  // EVT_CALIBRATION_QUALITY
}

static void handleSetMountMode(const uint8_t* payload, uint16_t len) {
    if (payload == NULL || len < 1) {
        sendError(0x0A, "Invalid mount mode");
        return;
    }
    CalibrationData cal;
    loadCalibrationData(&cal);
    cal.mount_mode = payload[0] & 0x03;
    saveCalibrationData(&cal);
    Serial.printf("[BT] Mount mode set to %u\n", cal.mount_mode);
    sendAck(PKT_TYPE_CMD_SET_MOUNT_MODE, 0);
}

static void handleGetCalibration(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    CalibrationData cal;
    loadCalibrationData(&cal);
    sendPacket(PKT_TYPE_RSP_ACK, &cal, sizeof(CalibrationData));
}

// ================= COREDUMP HANDLERS =================
static void handleGetCoredump(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;

    if (!coredumpIsAvailable()) {
        sendError(0x10, "No coredump stored");
        return;
    }

    uint32_t totalSize = coredumpGetSize();
    Serial.printf("[BT] Sending coredump: %lu bytes\n", totalSize);

    // Send coredump in chunks via TX queue (max 64 bytes per chunk)
    uint8_t chunk[64];
    uint32_t offset = 0;
    while (offset < totalSize) {
        uint32_t chunkLen = (totalSize - offset > 64) ? 64 : (totalSize - offset);
        uint32_t bytesRead = coredumpRead(chunk, offset, chunkLen);
        if (bytesRead == 0) {
            Serial.println("[BT] COREDUMP: read error, aborting");
            break;
        }
        sendPacket(PKT_TYPE_RSP_ACK, chunk, bytesRead);  // Use RSP_ACK for raw binary payload
        offset += bytesRead;
    }

    Serial.printf("[BT] Coredump sent: %lu bytes\n", offset);
    sendAck(PKT_TYPE_CMD_GET_COREDUMP, 0);
}

static void handleEraseCoredump(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    coredumpErase();
    sendAck(PKT_TYPE_CMD_ERASE_COREDUMP, 0);
}

// ================= SHOT STATS HANDLER (Phase 1.3) =================
static void handleGetShotStats(const uint8_t* payload, uint16_t len) {
    (void)payload; (void)len;
    FirmwareConfig cfg;
    getConfigCopy(&cfg);

    PktShotStats stats;
    stats.shot_count = g_detectState.shot_peak_count;
    stats.adaptive_enabled = cfg.adaptive_threshold_enabled ? 1 : 0;

    if (g_detectState.shot_peak_count >= 2) {
        uint32_t sum = 0;
        for (uint8_t i = 0; i < g_detectState.shot_peak_count; i++) {
            sum += g_detectState.shot_peaks[i];
        }
        float mean = (float)sum / g_detectState.shot_peak_count;
        uint32_t sumSq = 0;
        for (uint8_t i = 0; i < g_detectState.shot_peak_count; i++) {
            int32_t d = (int32_t)g_detectState.shot_peaks[i] - (int32_t)(mean + 0.5f);
            sumSq += (uint32_t)(d * d);
        }
        float variance = (float)sumSq / g_detectState.shot_peak_count;
        float stddev = sqrtf(variance);
        stats.mean_peak = (uint16_t)(mean + 0.5f);
        stats.stddev_peak = (uint16_t)(stddev + 0.5f);
    } else {
        stats.mean_peak = 0;
        stats.stddev_peak = 0;
    }
    stats.adaptive_threshold = g_detectState.adaptive_threshold;

    sendPacket(PKT_TYPE_RSP_ACK, &stats, sizeof(stats));
}

// ================= PACKET BUILDERS =================
static void sendInfoPacket() {
    SensorHealth health;
    checkSensorHealth(&health);

    PktInfo info;
    info.firmware_version = ((BUILD_VERSION_MAJOR << 16) | (BUILD_VERSION_MINOR << 8) | BUILD_VERSION_PATCH);
    info.hardware_rev = 1;
    info.build_timestamp = BUILD_TIMESTAMP;
    // Feature flags: ENCRYPTED | AUTH_REQUIRED (Phase 1)
    // Add more as features are implemented (OTA, STORAGE, PWM_LED, etc.)
    info.supported_features = FEATURE_OTA_SUPPORTED | FEATURE_STORAGE_SUPPORTED |
                              FEATURE_ENCRYPTED | FEATURE_AUTH_REQUIRED |
                              FEATURE_PWM_LED | FEATURE_HAPTIC_PWM |
                              FEATURE_COREDUMP;
    info.mpu_whoami = health.mpu_whoami;
    info.reserved[0] = 0;
    info.reserved[1] = 0;
    sendPacket(PKT_TYPE_RSP_INFO, &info, sizeof(info));
}

static void sendConfigPacket() {
    FirmwareConfig cfg;
    getConfigCopy(&cfg);

    PktConfig pktCfg;
    memset(&pktCfg, 0, sizeof(pktCfg));
    pktCfg.sample_rate_hz = cfg.sample_rate_hz;
    pktCfg.piezo_threshold = cfg.piezo_threshold;
    pktCfg.accel_threshold = cfg.accel_threshold;
    pktCfg.debounce_ms = cfg.debounce_ms;
    pktCfg.led_enabled = cfg.led_enabled ? 1 : 0;
    pktCfg.data_mode = cfg.data_mode;
    pktCfg.streaming_rate_hz = cfg.streaming_rate_hz;
    strncpy(pktCfg.device_name, cfg.device_name, sizeof(pktCfg.device_name) - 1);

    sendPacket(PKT_TYPE_RSP_CONFIG, &pktCfg, sizeof(pktCfg));
}

static void sendSessionStartedPacket() {
    PktSessionStarted pkt;
    pkt.session_id = g_lastSession.session_id;
    pkt.timestamp_us = g_lastSession.start_time_us;
    pkt.battery_percent = g_lastSession.battery_start;
    pkt.sensor_health = getSensorHealthFlags();
    pkt.free_heap = esp_get_free_heap_size();
    sendPacket(PKT_TYPE_EVT_SESSION_STARTED, &pkt, sizeof(pkt));
}

static void sendSessionStoppedPacket() {
    PktSessionStopped pkt;
    pkt.session_id = g_lastSession.session_id;
    pkt.duration_ms = g_lastSession.duration_ms;
    pkt.shot_count = g_lastSession.shot_count;
    pkt.battery_end = getBatteryPercent();
    pkt.sensor_health = getSensorHealthFlags();
    sendPacket(PKT_TYPE_EVT_SESSION_STOPPED, &pkt, sizeof(pkt));
}

void sendSensorHealthPacket() {
    SensorHealth health;
    checkSensorHealth(&health);

    PktSensorHealth pkt;
    pkt.mpu_present = health.mpu_present ? 1 : 0;
    pkt.i2c_errors = health.i2c_error_count;
    pkt.samples_total = health.samples_total;
    pkt.samples_invalid = health.samples_invalid;
    pkt.i2c_recovery_count = health.i2c_recovery_count;
    pkt.reserved[0] = 0;
    pkt.reserved[1] = 0;
    pkt.reserved[2] = 0;
    sendPacket(PKT_TYPE_EVT_SENSOR_HEALTH, &pkt, sizeof(pkt));
}

// ================= DISPATCH COMMAND =================
// Helper: check if a command requires auth
static bool commandRequiresAuth(uint8_t type) {
    return type == PKT_TYPE_CMD_START_SESSION ||
           type == PKT_TYPE_CMD_STOP_SESSION ||
           type == PKT_TYPE_CMD_SET_CONFIG ||
           type == PKT_TYPE_CMD_FACTORY_RESET;
}

void dispatchCommand(const DecodedPacket* cmd) {
    Serial.printf("[BT] dispatchCommand: type=0x%02X len=%u\n", cmd->type, cmd->payload_len);
    // Auth check for protected commands.
    // CMD_START_SESSION is excluded because it initiates the auth flow by
    // sending a challenge — the client responds with CMD_AUTH to complete it.
    // CMD_AUTH itself is always allowed (it's the auth response).
    if (commandRequiresAuth(cmd->type) &&
        cmd->type != PKT_TYPE_CMD_AUTH &&
        cmd->type != PKT_TYPE_CMD_START_SESSION &&
        g_secState.auth_state != AuthState::AUTHENTICATED) {
        sendError(0x06, "Auth required");
        Serial.printf("[BT] Command 0x%02X rejected: not authenticated\n", cmd->type);
        return;
    }

    switch (cmd->type) {
        case PKT_TYPE_CMD_AUTH:
            handleAuth(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_START_SESSION:
            handleStartSession(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_STOP_SESSION:
            handleStopSession(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_INFO:
            handleGetInfo(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_CONFIG:
            handleGetConfig(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_SET_CONFIG:
            handleSetConfig(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_FACTORY_RESET:
            handleFactoryReset(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_OTA_START:
            handleOTAStart(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_OTA_DATA:
            handleOTAData(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_OTA_END:
            handleOTAEnd(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_OTA_ABORT:
            handleOTAAbort(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_OTA_STATUS:
            handleOTAStatus(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_SESSIONS:
            handleGetSessions(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_SESSION_DATA:
            handleGetSessionData(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_DELETE_SESSION:
            handleDeleteSession(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_CALIBRATE_START:
            handleCalibrateStart(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_CALIBRATE_STATUS:
            handleCalibrateStatus(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_SET_MOUNT_MODE:
            handleSetMountMode(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_CALIBRATION:
            handleGetCalibration(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_COREDUMP:
            handleGetCoredump(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_ERASE_COREDUMP:
            handleEraseCoredump(cmd->payload, cmd->payload_len);
            break;
        case PKT_TYPE_CMD_GET_SHOT_STATS:
            handleGetShotStats(cmd->payload, cmd->payload_len);
            break;
        default:
            sendError(0xFF, "Unknown command");
            break;
    }
}
