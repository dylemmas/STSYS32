#ifndef SHOT_DETECTOR_H
#define SHOT_DETECTOR_H

#include <stdint.h>
#include <stdbool.h>
#include "sensor.h"
#include "config.h"

// ================= SHOT EVENT =================
struct ShotEvent {
    uint32_t session_id;
    uint32_t timestamp_us;
    uint16_t shot_number;
    uint16_t piezo_peak;
    int16_t  accel_peak_x;
    int16_t  accel_peak_y;
    int16_t  accel_peak_z;
    int16_t  gyro_peak_x;
    int16_t  gyro_peak_y;
    int16_t  gyro_peak_z;
    int8_t   recoil_axis;     // 0=X, 1=Y, 2=Z
    int8_t   recoil_sign;     // +1 or -1
};

// ================= DETECTION STATE =================
#define RING_BUFFER_SIZE  10   // 10ms at 1kHz
#define DETECTION_WINDOW   5   // 5 samples

struct DetectionState {
    SensorSample ringBuffer[RING_BUFFER_SIZE];
    uint8_t ringHead;
    uint32_t lastShotTime_us;
    uint16_t shotCount;
    uint16_t maxPiezo;
    int16_t  maxAccelX, maxAccelY, maxAccelZ;
    int16_t  maxGyroX, maxGyroY, maxGyroZ;
    bool     inWindow;
    uint8_t  windowCount;
    uint32_t windowStartTime_us;
    // Phase 1.3: Adaptive threshold tracking
    uint16_t shot_peaks[10];         // Ring buffer of last 10 piezo peaks
    uint8_t  shot_peak_count;       // Number of shots in ring buffer
    uint32_t adaptive_threshold;    // Current adaptive threshold (mean + 2*stddev)
};

// ================= EXTERNALS =================
extern DetectionState g_detectState;

// ================= FUNCTIONS =================
void  initShotDetector();
void  updateShotDetectorConfig(const FirmwareConfig* cfg);
bool  processSample(const SensorSample* sample, ShotEvent* eventOut,
                    uint32_t session_id);

#endif // SHOT_DETECTOR_H
