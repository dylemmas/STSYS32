#ifndef SESSION_H
#define SESSION_H

#include <stdint.h>
#include <stdbool.h>

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

// ================= EXTERNALS =================
extern SessionState g_sessionState;
extern SessionSummary g_lastSession;

// ================= FUNCTIONS =================
SessionState  startSession(uint32_t session_id, uint8_t battery_pct);
SessionState  stopSession();
SessionState  getSessionState();
void          addShotToSession(const struct ShotEvent* event);
SessionSummary getSessionSummary();

#endif // SESSION_H
