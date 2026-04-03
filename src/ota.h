#ifndef OTA_H
#define OTA_H

#include <stdint.h>
#include <stdbool.h>
#include <esp_partition.h>
#include <esp_ota_ops.h>

// ================= OTA STATES =================
enum class OTAState {
    IDLE,
    RECEIVING,
    VERIFYING,
    COMPLETE,
    ERROR
};

// ================= EXTERNALS =================
extern OTAState g_otaState;
extern uint32_t g_otaBytesReceived;
extern uint32_t g_otaTotalSize;
extern const esp_partition_t* g_otaPartition;
extern esp_ota_handle_t g_otaHandle;

// ================= OTA STATUS =================
struct OTAStatus {
    OTAState state;
    uint32_t bytes_received;
    uint32_t total_expected;
    uint32_t partition_address;
};

// ================= FUNCTIONS =================

// Start OTA update: returns partition handle to write to, or NULL on error
const esp_partition_t* otaBegin(uint32_t totalSize);

// Write a chunk of firmware data
bool otaWrite(const uint8_t* data, uint32_t len);

// Finalize OTA: verify and mark boot partition
bool otaEnd();

// Abort ongoing OTA
bool otaAbort();

// Get current OTA state as string
const char* otaStateString();

// Is an OTA update currently in progress?
bool isOTAInProgress();

// Get OTA status for progress reporting
OTAStatus otaGetStatus();

#endif // OTA_H
