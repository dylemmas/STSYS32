#ifndef STORAGE_H
#define STORAGE_H

#include <stdint.h>
#include <stdbool.h>
#include "session.h"

// ================= SESSION FILE FORMAT =================
// File: /sessions/<session_id>.bin
// Header: SessionHeader (24 bytes)
// Followed by: ShotEvent[] (one per shot)
struct SessionHeader {
    uint32_t session_id;
    uint32_t start_time_us;
    uint32_t duration_ms;
    uint16_t shot_count;
    uint8_t  battery_start;
    uint8_t  battery_end;
    uint8_t  sensor_health_flags;
    uint8_t  reserved[3];
};

// ================= FUNCTIONS =================

// Initialize SPIFFS filesystem
bool initStorage();

// Save a completed session to SPIFFS
bool saveSession(const SessionSummary* summary, uint16_t shotCount, const struct ShotEvent* shots);

// Load session metadata (header only)
bool loadSessionHeader(uint32_t session_id, SessionHeader* outHeader);

// Load full session data
uint16_t loadSession(uint32_t session_id, SessionHeader* outHeader,
                      struct ShotEvent* outShots, uint16_t maxShots);

// Get list of all stored sessions (returns count)
uint16_t enumerateSessions(uint32_t* outSessionIds, uint16_t maxIds);

// Delete a session from storage
bool deleteSession(uint32_t session_id);

// Get total storage used (bytes)
uint32_t getStorageUsed();

// Get free storage space (bytes)
uint32_t getStorageFree();

// Is storage available?
bool isStorageAvailable();

#endif // STORAGE_H
