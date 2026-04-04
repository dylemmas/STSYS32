#include "session.h"
#include <Arduino.h>
#include <esp_timer.h>
#include "storage.h"
#include "shot_detector.h"
#include "config.h"

// ================= GLOBALS =================
SessionState g_sessionState = SessionState::IDLE;
SessionSummary g_lastSession;
struct ShotBuffer g_shotBuffer;

static uint32_t s_sessionStartTime = 0;
static uint8_t  s_batteryAtStart = 0;

// ================= SESSION MANAGEMENT =================
SessionState startSession(uint32_t session_id, uint8_t battery_pct) {
    if (g_sessionState == SessionState::STREAMING) {
        return g_sessionState; // Already streaming
    }

    g_sessionState = SessionState::STREAMING;
    s_sessionStartTime = esp_timer_get_time();
    s_batteryAtStart = battery_pct;

    // Clear shot buffer for new session
    g_shotBuffer.count = 0;

    g_lastSession.session_id = session_id;
    g_lastSession.start_time_us = s_sessionStartTime;
    g_lastSession.duration_ms = 0;
    g_lastSession.shot_count = 0;
    g_lastSession.battery_start = s_batteryAtStart;
    g_lastSession.battery_end = battery_pct;
    g_lastSession.sensor_health_flags = 0;

    Serial.printf("[SESSION] Started: id=%u batt=%d%%\n", session_id, battery_pct);
    return g_sessionState;
}

SessionState stopSession() {
    if (g_sessionState == SessionState::IDLE) {
        return g_sessionState;
    }

    g_sessionState = SessionState::STOPPING;

    uint32_t endTime = esp_timer_get_time();
    g_lastSession.duration_ms = (endTime - s_sessionStartTime) / 1000;

    Serial.printf("[SESSION] Stopped: shots=%d dur=%lums\n",
                  g_lastSession.shot_count, g_lastSession.duration_ms);

    // Persist adaptive threshold state to NVS
    saveAdaptiveThresholdState();

    // Auto-save session to SPIFFS with actual shot data
    saveSession(&g_lastSession, g_shotBuffer.count,
                g_shotBuffer.count > 0 ? g_shotBuffer.events : NULL);

    g_sessionState = SessionState::IDLE;
    return g_sessionState;
}

SessionState getSessionState() {
    return g_sessionState;
}

void addShotToSession(const ShotEvent* event) {
    if (event == NULL) return;
    g_lastSession.shot_count++;
    if (g_shotBuffer.count < SESSION_MAX_SHOTS) {
        g_shotBuffer.events[g_shotBuffer.count++] = *event;
    } else {
        Serial.printf("[SESSION] WARNING: shot buffer full at %d, dropping shot %u\n",
                      SESSION_MAX_SHOTS, g_lastSession.shot_count);
    }
}

void clearShotBuffer() {
    g_shotBuffer.count = 0;
}

SessionSummary getSessionSummary() {
    return g_lastSession;
}
