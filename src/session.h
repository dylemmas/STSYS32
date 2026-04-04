#ifndef SESSION_H
#define SESSION_H

#include <stdint.h>
#include <stdbool.h>
#include "shot_detector.h"

// ================= SESSION STATES =================
enum class SessionState {
    IDLE,
    STREAMING,
    STOPPING
};

// ================= SESSION SUMMARY =================
struct SessionSummary {
    uint32_t session_id;
    uint32_t start_time_us;
    uint32_t duration_ms;
    uint16_t shot_count;
    uint8_t  battery_start;
    uint8_t  battery_end;
    uint8_t  sensor_health_flags;
};

// ================= SHOT BUFFER =================
#define SESSION_MAX_SHOTS  256   // Max shots per session (fits in 8KB heap allocation)

struct ShotBuffer {
    struct ShotEvent events[SESSION_MAX_SHOTS];
    uint16_t count;
};

// ================= EXTERNALS =================
extern SessionState g_sessionState;
extern SessionSummary g_lastSession;
extern struct ShotBuffer g_shotBuffer;

// ================= FUNCTIONS =================
SessionState  startSession(uint32_t session_id, uint8_t battery_pct);
SessionState  stopSession();
SessionState  getSessionState();
void          addShotToSession(const struct ShotEvent* event);
SessionSummary getSessionSummary();
void          clearShotBuffer();

#endif // SESSION_H
