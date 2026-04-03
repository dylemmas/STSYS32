#include "storage.h"
#include <Arduino.h>
#include <SPIFFS.h>
#include "shot_detector.h"
#include <freertos/FreeRTOS.h>

static bool s_storageInitialized = false;
static bool s_storageAvailable = false;

// ================= HELPERS =================
static void makeSessionPath(uint32_t session_id, char* outPath, size_t maxLen) {
    snprintf(outPath, maxLen, "/sessions/%lu.bin", (unsigned long)session_id);
}

// ================= INIT =================
bool initStorage() {
    if (s_storageInitialized) return s_storageAvailable;

    Serial.println("[STORAGE] Initializing SPIFFS...");
    if (!SPIFFS.begin(true)) {
        Serial.println("[STORAGE] SPIFFS mount failed");
        s_storageInitialized = true;
        s_storageAvailable = false;
        return false;
    }

    // Create sessions directory
    if (!SPIFFS.exists("/sessions")) {
        if (!SPIFFS.mkdir("/sessions")) {
            Serial.println("[STORAGE] Failed to create sessions directory");
            s_storageInitialized = true;
            s_storageAvailable = false;
            return false;
        }
    }

    s_storageInitialized = true;
    s_storageAvailable = true;
    uint32_t used = getStorageUsed();
    uint32_t free = getStorageFree();
    Serial.printf("[STORAGE] OK — used: %lu KB, free: %lu KB\n", used / 1024, free / 1024);
    return true;
}

bool isStorageAvailable() {
    if (!s_storageInitialized) initStorage();
    return s_storageAvailable;
}

// ================= SAVE =================
bool saveSession(const SessionSummary* summary, uint16_t shotCount,
                 const struct ShotEvent* shots) {
    if (!isStorageAvailable()) return false;

    char path[64];
    makeSessionPath(summary->session_id, path, sizeof(path));

    File f = SPIFFS.open(path, FILE_WRITE);
    if (!f) {
        Serial.printf("[STORAGE] Failed to open %s for write\n", path);
        return false;
    }

    SessionHeader hdr;
    hdr.session_id = summary->session_id;
    hdr.start_time_us = summary->start_time_us;
    hdr.duration_ms = summary->duration_ms;
    hdr.shot_count = shotCount;
    hdr.battery_start = summary->battery_start;
    hdr.battery_end = summary->battery_end;
    hdr.sensor_health_flags = summary->sensor_health_flags;
    hdr.reserved[0] = hdr.reserved[1] = hdr.reserved[2] = 0;

    size_t written = f.write((const uint8_t*)&hdr, sizeof(hdr));
    if (written != sizeof(hdr)) {
        f.close();
        Serial.printf("[STORAGE] Failed to write header for session %lu\n",
                     (unsigned long)summary->session_id);
        return false;
    }

    if (shotCount > 0 && shots != NULL) {
        written = f.write((const uint8_t*)shots, sizeof(struct ShotEvent) * shotCount);
        if (written != sizeof(struct ShotEvent) * shotCount) {
            f.close();
            Serial.printf("[STORAGE] Failed to write shots for session %lu\n",
                         (unsigned long)summary->session_id);
            return false;
        }
    }

    f.close();
    Serial.printf("[STORAGE] Saved session %lu: %u shots, %lu bytes\n",
                 (unsigned long)summary->session_id, shotCount, written);
    return true;
}

// ================= LOAD =================
bool loadSessionHeader(uint32_t session_id, SessionHeader* outHeader) {
    if (!isStorageAvailable()) return false;

    char path[64];
    makeSessionPath(session_id, path, sizeof(path));

    File f = SPIFFS.open(path, FILE_READ);
    if (!f) return false;

    size_t r = f.read((uint8_t*)outHeader, sizeof(SessionHeader));
    f.close();
    return r == sizeof(SessionHeader);
}

uint16_t loadSession(uint32_t session_id, SessionHeader* outHeader,
                    struct ShotEvent* outShots, uint16_t maxShots) {
    if (!isStorageAvailable()) return 0;

    char path[64];
    makeSessionPath(session_id, path, sizeof(path));

    File f = SPIFFS.open(path, FILE_READ);
    if (!f) return 0;

    size_t r = f.read((uint8_t*)outHeader, sizeof(SessionHeader));
    if (r != sizeof(SessionHeader)) {
        f.close();
        return 0;
    }

    uint16_t shotsToRead = min(maxShots, (uint16_t)outHeader->shot_count);
    uint16_t bytesToRead = sizeof(struct ShotEvent) * shotsToRead;
    r = f.read((uint8_t*)outShots, bytesToRead);
    f.close();
    return (uint16_t)(r / sizeof(struct ShotEvent));
}

// ================= ENUMERATE =================
uint16_t enumerateSessions(uint32_t* outSessionIds, uint16_t maxIds) {
    if (!isStorageAvailable() || outSessionIds == NULL) return 0;

    File root = SPIFFS.open("/sessions");
    if (!root) return 0;

    uint16_t count = 0;
    File f;
    while ((f = root.openNextFile()) && count < maxIds) {
        if (!f.isDirectory()) {
            // Parse session ID from filename: <id>.bin
            uint32_t id = strtoul(f.name(), NULL, 10);
            if (id > 0) {
                outSessionIds[count++] = id;
            }
        }
        f.close();
    }
    root.close();
    return count;
}

// ================= DELETE =================
bool deleteSession(uint32_t session_id) {
    if (!isStorageAvailable()) return false;

    char path[64];
    makeSessionPath(session_id, path, sizeof(path));

    if (!SPIFFS.exists(path)) return false;
    bool ok = SPIFFS.remove(path);
    Serial.printf("[STORAGE] Delete session %lu: %s\n",
                 (unsigned long)session_id, ok ? "OK" : "FAIL");
    return ok;
}

// ================= STORAGE INFO =================
uint32_t getStorageUsed() {
    if (!isStorageAvailable()) return 0;
    return SPIFFS.usedBytes();
}

uint32_t getStorageFree() {
    if (!isStorageAvailable()) return 0;
    return SPIFFS.totalBytes() - SPIFFS.usedBytes();
}
