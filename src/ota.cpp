#include "ota.h"
#include <Arduino.h>

// ================= GLOBALS =================
OTAState g_otaState = OTAState::IDLE;
uint32_t g_otaBytesReceived = 0;
uint32_t g_otaTotalSize = 0;
const esp_partition_t* g_otaPartition = NULL;
const esp_partition_t* g_updatePartition = NULL;
esp_ota_handle_t g_otaHandle = 0;

const char* otaStateString() {
    switch (g_otaState) {
        case OTAState::IDLE:       return "IDLE";
        case OTAState::RECEIVING:  return "RECEIVING";
        case OTAState::VERIFYING:  return "VERIFYING";
        case OTAState::COMPLETE:   return "COMPLETE";
        case OTAState::ERROR:      return "ERROR";
    }
    return "UNKNOWN";
}

bool isOTAInProgress() {
    return g_otaState == OTAState::RECEIVING;
}

const esp_partition_t* otaBegin(uint32_t totalSize) {
    if (g_otaState != OTAState::IDLE) {
        Serial.printf("[OTA] Begin failed: state=%s\n", otaStateString());
        return NULL;
    }

    // Find the running partition and the next OTA partition
    g_otaPartition = esp_ota_get_running_partition();
    g_updatePartition = esp_ota_get_next_update_partition(NULL);

    if (g_updatePartition == NULL) {
        Serial.println("[OTA] ERROR: No OTA partition found");
        g_otaState = OTAState::ERROR;
        return NULL;
    }

    Serial.printf("[OTA] Running: %s at 0x%lx\n", g_otaPartition->label, g_otaPartition->address);
    Serial.printf("[OTA] Target: %s at 0x%lx, size=%lu\n",
                  g_updatePartition->label, g_updatePartition->address, totalSize);

    // Begin OTA update — use OTA_SIZE_UNKNOWN so we don't need to know total size upfront
    esp_err_t err = esp_ota_begin(g_updatePartition, OTA_SIZE_UNKNOWN, &g_otaHandle);
    if (err != ESP_OK) {
        Serial.printf("[OTA] ERROR: esp_ota_begin failed: %d (%s)\n", err, esp_err_to_name(err));
        g_otaState = OTAState::ERROR;
        return NULL;
    }

    g_otaTotalSize = totalSize;
    g_otaBytesReceived = 0;
    g_otaState = OTAState::RECEIVING;
    Serial.println("[OTA] esp_ota_begin succeeded");

    return g_updatePartition;
}

bool otaWrite(const uint8_t* data, uint32_t len) {
    if (g_otaState != OTAState::RECEIVING) {
        Serial.printf("[OTA] Write failed: state=%s\n", otaStateString());
        return false;
    }

    esp_err_t err = esp_ota_write(g_otaHandle, data, len);
    if (err != ESP_OK) {
        Serial.printf("[OTA] ERROR: esp_ota_write failed: %d (%s)\n", err, esp_err_to_name(err));
        g_otaState = OTAState::ERROR;
        return false;
    }

    g_otaBytesReceived += len;

    // Log progress every 64KB
    if ((g_otaBytesReceived & 0xFFFF) == 0 || g_otaBytesReceived >= g_otaTotalSize) {
        float pct = (g_otaTotalSize > 0)
            ? (100.0f * g_otaBytesReceived / g_otaTotalSize)
            : 0.0f;
        Serial.printf("[OTA] Progress: %lu / %lu bytes (%.1f%%)\n",
                     g_otaBytesReceived, g_otaTotalSize, pct);
    }

    return true;
}

bool otaEnd() {
    if (g_otaState != OTAState::RECEIVING && g_otaState != OTAState::VERIFYING) {
        Serial.printf("[OTA] End failed: state=%s\n", otaStateString());
        return false;
    }

    g_otaState = OTAState::VERIFYING;
    Serial.printf("[OTA] Verifying %lu bytes...\n", g_otaBytesReceived);

    // esp_ota_end validates the image and frees the handle
    esp_err_t err = esp_ota_end(g_otaHandle);
    if (err != ESP_OK) {
        Serial.printf("[OTA] ERROR: esp_ota_end failed: %d (%s)\n", err, esp_err_to_name(err));
        g_otaState = OTAState::ERROR;
        return false;
    }
    Serial.println("[OTA] esp_ota_end succeeded (image validated)");

    // Mark the new partition as bootable
    err = esp_ota_set_boot_partition(g_updatePartition);
    if (err != ESP_OK) {
        Serial.printf("[OTA] ERROR: esp_ota_set_boot_partition failed: %d (%s)\n",
                     err, esp_err_to_name(err));
        g_otaState = OTAState::ERROR;
        return false;
    }
    Serial.printf("[OTA] Boot partition set to: %s\n", g_updatePartition->label);

    g_otaState = OTAState::COMPLETE;
    return true;
}

bool otaAbort() {
    if (g_otaState == OTAState::RECEIVING || g_otaState == OTAState::VERIFYING) {
        esp_err_t err = esp_ota_abort(g_otaHandle);
        if (err != ESP_OK) {
            Serial.printf("[OTA] Abort warning: esp_ota_abort: %d\n", err);
        }
    }
    Serial.println("[OTA] Aborted");
    g_otaState = OTAState::IDLE;
    g_otaBytesReceived = 0;
    g_otaTotalSize = 0;
    g_otaHandle = 0;
    g_updatePartition = NULL;
    return true;
}

// Bonus: get OTA status for CMD_OTA_STATUS handler
OTAStatus otaGetStatus() {
    OTAStatus s;
    s.state = g_otaState;
    s.bytes_received = g_otaBytesReceived;
    s.total_expected = g_otaTotalSize;
    s.partition_address = (g_updatePartition != NULL) ? g_updatePartition->address : 0;
    return s;
}
