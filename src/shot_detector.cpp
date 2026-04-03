#include "shot_detector.h"
#include <Arduino.h>
#include <math.h>

// ================= GLOBALS =================
DetectionState g_detectState;
FirmwareConfig g_detectConfig;

// ================= LOCAL COPY (updated from ISR-safe config) =================
static FirmwareConfig s_localCfg;

// ================= PIEZO FILTERING =================
// IIR low-pass filter for piezo: filtered = filtered * 0.9 + raw * 0.1
static float s_piezoFilter = 0.0f;

// Previous accel values for jerk computation
static int16_t s_prevAccelX = 0;
static int16_t s_prevAccelY = 0;
static int16_t s_prevAccelZ = 0;

// ================= INIT =================
void initShotDetector() {
    memset(&g_detectState, 0, sizeof(g_detectState));
    g_detectState.ringHead = 0;
    g_detectState.lastShotTime_us = 0;
    g_detectState.shotCount = 0;
    s_piezoFilter = 0.0f;
    s_prevAccelX = s_prevAccelY = s_prevAccelZ = 0;

    // Load default config
    memcpy(&s_localCfg, &g_detectConfig, sizeof(FirmwareConfig));
}

void updateShotDetectorConfig(const FirmwareConfig* cfg) {
    if (cfg == NULL) return;
    memcpy(&s_localCfg, cfg, sizeof(FirmwareConfig));
}

// ================= ESTIMATE RECOIL AXIS =================
static void estimateRecoilAxis(const DetectionState* state, int8_t* outAxis, int8_t* outSign) {
    int32_t ax = abs(state->maxAccelX);
    int32_t ay = abs(state->maxAccelY);
    int32_t az = abs(state->maxAccelZ);

    if (ax >= ay && ax >= az) {
        *outAxis = 0; // X
        *outSign = (state->maxAccelX >= 0) ? 1 : -1;
    } else if (ay >= ax && ay >= az) {
        *outAxis = 1; // Y
        *outSign = (state->maxAccelY >= 0) ? 1 : -1;
    } else {
        *outAxis = 2; // Z
        *outSign = (state->maxAccelZ >= 0) ? 1 : -1;
    }
}

// ================= PROCESS SAMPLE =================
bool processSample(const SensorSample* sample, ShotEvent* eventOut,
                   uint32_t session_id) {
    if (sample == NULL || !sample->valid) return false;

    // --- Filter Piezo ---
    s_piezoFilter = s_piezoFilter * 0.9f + (float)sample->piezo_raw * 0.1f;
    uint16_t filteredPiezo = (uint16_t)s_piezoFilter;

    // --- Compute Jerk ---
    int16_t jerkX = abs(sample->accel_x - s_prevAccelX);
    int16_t jerkY = abs(sample->accel_y - s_prevAccelY);
    int16_t jerkZ = abs(sample->accel_z - s_prevAccelZ);
    int16_t jerkMag = jerkX + jerkY + jerkZ;

    s_prevAccelX = sample->accel_x;
    s_prevAccelY = sample->accel_y;
    s_prevAccelZ = sample->accel_z;

    // --- Ring Buffer Push ---
    uint8_t head = g_detectState.ringHead;
    g_detectState.ringBuffer[head] = *sample;
    g_detectState.ringHead = (head + 1) % RING_BUFFER_SIZE;

    // --- Detection: Both Thresholds Must Pass ---
    bool piezoPass = (filteredPiezo >= s_localCfg.piezo_threshold);
    bool accelPass = (jerkMag >= s_localCfg.accel_threshold);

    if (piezoPass && accelPass) {
        // Count passing samples in the detection window
        uint8_t passCount = 0;
        for (uint8_t i = 0; i < DETECTION_WINDOW; i++) {
            uint8_t idx = (head + RING_BUFFER_SIZE - i) % RING_BUFFER_SIZE;
            // Quick check: this is a simplification. Full impl would recompute.
            passCount++;
        }

        if (!g_detectState.inWindow) {
            // Start detection window
            g_detectState.inWindow = true;
            g_detectState.windowCount = 0;
            g_detectState.windowStartTime_us = sample->timestamp_us;
            g_detectState.maxPiezo = sample->piezo;
            g_detectState.maxAccelX = sample->accel_x;
            g_detectState.maxAccelY = sample->accel_y;
            g_detectState.maxAccelZ = sample->accel_z;
            g_detectState.maxGyroX = sample->gyro_x;
            g_detectState.maxGyroY = sample->gyro_y;
            g_detectState.maxGyroZ = sample->gyro_z;
        }
        g_detectState.windowCount++;

        // Update peak values
        if (sample->piezo > g_detectState.maxPiezo) {
            g_detectState.maxPiezo = sample->piezo;
        }

        int16_t accelMag = (int16_t)sqrtf(
            (float)sample->accel_x * sample->accel_x +
            (float)sample->accel_y * sample->accel_y +
            (float)sample->accel_z * sample->accel_z);

        int16_t prevMag = (int16_t)sqrtf(
            (float)g_detectState.maxAccelX * g_detectState.maxAccelX +
            (float)g_detectState.maxAccelY * g_detectState.maxAccelY +
            (float)g_detectState.maxAccelZ * g_detectState.maxAccelZ);

        if (accelMag > prevMag) {
            g_detectState.maxAccelX = sample->accel_x;
            g_detectState.maxAccelY = sample->accel_y;
            g_detectState.maxAccelZ = sample->accel_z;
        }

        // Peak gyro
        int16_t gyroMag = (int16_t)sqrtf(
            (float)sample->gyro_x * sample->gyro_x +
            (float)sample->gyro_y * sample->gyro_y +
            (float)sample->gyro_z * sample->gyro_z);

        int16_t prevGyroMag = (int16_t)sqrtf(
            (float)g_detectState.maxGyroX * g_detectState.maxGyroX +
            (float)g_detectState.maxGyroY * g_detectState.maxGyroY +
            (float)g_detectState.maxGyroZ * g_detectState.maxGyroZ);

        if (gyroMag > prevGyroMag) {
            g_detectState.maxGyroX = sample->gyro_x;
            g_detectState.maxGyroY = sample->gyro_y;
            g_detectState.maxGyroZ = sample->gyro_z;
        }
    }

    // --- Window Close ---
    bool shotFired = false;
    if (g_detectState.inWindow) {
        uint32_t windowDuration = sample->timestamp_us - g_detectState.windowStartTime_us;

        // Close window after 5ms or if debounce check passes
        if (windowDuration >= 5000 || g_detectState.windowCount >= DETECTION_WINDOW * 2) {
            // Confirm: at least 2 samples passed both thresholds
            // (windowCount approximates this in the simplified version)
            if (g_detectState.windowCount >= 2) {
                uint32_t debounceUs = (uint32_t)s_localCfg.debounce_ms * 1000;
                if (sample->timestamp_us - g_detectState.lastShotTime_us >= debounceUs) {
                    g_detectState.lastShotTime_us = sample->timestamp_us;
                    g_detectState.shotCount++;

                    if (eventOut != NULL) {
                        eventOut->session_id = session_id;
                        eventOut->timestamp_us = sample->timestamp_us;
                        eventOut->shot_number = g_detectState.shotCount;
                        eventOut->piezo_peak = g_detectState.maxPiezo;
                        eventOut->accel_peak_x = g_detectState.maxAccelX;
                        eventOut->accel_peak_y = g_detectState.maxAccelY;
                        eventOut->accel_peak_z = g_detectState.maxAccelZ;
                        eventOut->gyro_peak_x = g_detectState.maxGyroX;
                        eventOut->gyro_peak_y = g_detectState.maxGyroY;
                        eventOut->gyro_peak_z = g_detectState.maxGyroZ;
                        estimateRecoilAxis(&g_detectState,
                                           &eventOut->recoil_axis,
                                           &eventOut->recoil_sign);
                    }
                    shotFired = true;

                    // --- Update adaptive threshold ring buffer (Phase 1.3) ---
                    uint8_t idx = g_detectState.shot_peak_count % 10;
                    g_detectState.shot_peaks[idx] = g_detectState.maxPiezo;
                    if (g_detectState.shot_peak_count < 10) {
                        g_detectState.shot_peak_count++;
                    }

                    // Compute running mean and variance when we have enough shots
                    if (g_detectState.shot_peak_count >= 5 && s_localCfg.adaptive_threshold_enabled) {
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

                        // Adaptive threshold = mean + 2*stddev (catches outliers)
                        g_detectState.adaptive_threshold = (uint32_t)(mean + 2.0f * stddev + 0.5f);

                        Serial.printf("[DETECTOR] Adaptive: n=%u mean=%.0f stddev=%.1f threshold=%lu\n",
                                     g_detectState.shot_peak_count, mean, stddev,
                                     g_detectState.adaptive_threshold);
                    }
                }
            }

            g_detectState.inWindow = false;
            g_detectState.windowCount = 0;
        }
    }

    return shotFired;
}
