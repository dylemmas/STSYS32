#include "coredump.h"
#include <Arduino.h>
#include <esp_partition.h>

// ================= STATIC =================
static bool s_coredumpChecked = false;
static bool s_coredumpAvailable = false;
static uint32_t s_coredumpSize = 0;
static const esp_partition_t* s_coredumpPart = NULL;

static void initCoredumpPartition() {
    if (s_coredumpChecked) return;
    s_coredumpChecked = true;

    s_coredumpPart = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_COREDUMP, NULL);

    if (s_coredumpPart != NULL && s_coredumpPart->size > 0) {
        s_coredumpAvailable = true;
        s_coredumpSize = s_coredumpPart->size;
        Serial.printf("[COREDUMP] Partition found: %lu bytes\n", s_coredumpSize);
    } else {
        s_coredumpAvailable = false;
        s_coredumpSize = 0;
    }
}

bool coredumpIsAvailable() {
    initCoredumpPartition();
    return s_coredumpAvailable;
}

uint32_t coredumpGetSize() {
    initCoredumpPartition();
    return s_coredumpSize;
}

uint32_t coredumpRead(uint8_t* buffer, uint32_t offset, uint32_t maxLen) {
    if (!s_coredumpAvailable || s_coredumpPart == NULL) return 0;

    uint32_t end = offset + maxLen;
    if (end > s_coredumpSize) {
        maxLen = s_coredumpSize - offset;
        if (offset >= s_coredumpSize) return 0;
    }

    esp_err_t err = esp_partition_read(s_coredumpPart, offset, buffer, maxLen);
    if (err != ESP_OK) {
        Serial.printf("[COREDUMP] Read failed: %d\n", err);
        return 0;
    }
    return maxLen;
}

void coredumpErase() {
    if (!s_coredumpAvailable || s_coredumpPart == NULL) return;

    esp_err_t err = esp_partition_erase_range(s_coredumpPart, 0, s_coredumpPart->size);
    if (err == ESP_OK) {
        Serial.println("[COREDUMP] Erased");
    } else {
        Serial.printf("[COREDUMP] Erase failed: %d\n", err);
    }
}
