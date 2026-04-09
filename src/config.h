#ifndef CONFIG_H
#define CONFIG_H

#include <stdint.h>
#include <stdbool.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// ================= CONFIG STRUCT =================
struct FirmwareConfig {
    uint8_t  sample_rate_hz;     // 50, 100, 200
    uint16_t piezo_threshold;    // ADC threshold (default 800)
    uint16_t accel_threshold;    // Jerk threshold raw LSB (default 300)
    uint16_t debounce_ms;        // Min ms between shots (default 200)
    bool     led_enabled;        // LED feedback
    uint8_t  data_mode;         // 0=both, 1=raw-only, 2=events-only
    uint16_t streaming_rate_hz;  // Raw stream rate
    char     device_name[20];   // BT device name (matches PktConfig protocol)
    bool     adaptive_threshold_enabled;  // Enable adaptive threshold (Phase 1.3)
};

// ================= CALIBRATION DATA =================
struct CalibrationData {
    int16_t  accel_bias_x;
    int16_t  accel_bias_y;
    int16_t  accel_bias_z;
    int16_t  gyro_bias_x;
    int16_t  gyro_bias_y;
    int16_t  gyro_bias_z;
    int16_t  temp_coeff;        // degC offset per degC from 25C reference
    uint8_t  mount_mode;        // 0=standard, 1=rotated_90, 2=inverted, 3=rotated_270
                                 // 4=barrel_under (90° pitch, Z along barrel)
                                 // 5=barrel_under_inv (270° pitch)
                                 // 6=side_mount (90° roll)
    bool     is_calibrated;
    bool     factory_calibrated;
};

// ================= ADAPTIVE THRESHOLD STATE =================
// Persisted to NVS "calib" namespace on session stop, restored on boot.
struct AdaptiveThresholdState {
    uint16_t shot_peaks[10];         // Ring buffer of last 10 piezo peaks
    uint8_t  shot_peak_count;        // Number of shots in ring buffer (0-10)
    uint32_t adaptive_threshold;     // Current adaptive threshold (mean + 2*stddev)
    uint8_t  adaptive_enabled;        // Whether adaptive threshold is enabled
};

// ================= EXTERNALS =================
extern SemaphoreHandle_t configMutex;
extern FirmwareConfig g_config;

// ================= DEFAULTS =================
#define DEFAULT_SAMPLE_RATE       100
#define DEFAULT_PIEZO_THRESHOLD   800
#define DEFAULT_ACCEL_THRESHOLD   300
#define DEFAULT_DEBOUNCE_MS       200
#define DEFAULT_LED_ENABLED       true
#define DEFAULT_DATA_MODE         0    // both
#define DEFAULT_STREAMING_RATE    100
#define DEFAULT_DEVICE_NAME       "STASYS"

// ================= FUNCTIONS =================
void  initConfig();
void  loadConfig(FirmwareConfig* cfg);
bool  saveConfig(const FirmwareConfig* cfg);
void  updateConfig(const FirmwareConfig* newCfg);
void  getConfigCopy(FirmwareConfig* outCfg);  // Thread-safe copy

// Adaptive threshold persistence (NVS "calib" namespace)
bool  saveAdaptiveThreshold(const struct AdaptiveThresholdState* state);
bool  loadAdaptiveThreshold(struct AdaptiveThresholdState* out);

#endif // CONFIG_H
