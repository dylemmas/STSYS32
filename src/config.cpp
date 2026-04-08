#include "config.h"
#include <Preferences.h>
#include <string.h>
#include <Arduino.h>

// ================= GLOBALS =================
SemaphoreHandle_t configMutex = NULL;
FirmwareConfig g_config;

// ================= STATIC =================
static Preferences s_nvsPrefs;
static Preferences s_nvsCalib;  // For calibration + adaptive threshold

// ================= INIT =================
void initConfig() {
    configMutex = xSemaphoreCreateMutex();
    if (configMutex == NULL) {
        Serial.println("[CONFIG] ERROR: Failed to create config mutex");
        return;
    }

    FirmwareConfig defaults;
    memset(&defaults, 0, sizeof(defaults));
    defaults.sample_rate_hz    = DEFAULT_SAMPLE_RATE;
    defaults.piezo_threshold   = DEFAULT_PIEZO_THRESHOLD;
    defaults.accel_threshold   = DEFAULT_ACCEL_THRESHOLD;
    defaults.debounce_ms       = DEFAULT_DEBOUNCE_MS;
    defaults.led_enabled       = DEFAULT_LED_ENABLED;
    defaults.data_mode         = DEFAULT_DATA_MODE;
    defaults.streaming_rate_hz = DEFAULT_STREAMING_RATE;
    strncpy(defaults.device_name, DEFAULT_DEVICE_NAME, sizeof(defaults.device_name) - 1);

    FirmwareConfig loaded;
    memset(&loaded, 0, sizeof(loaded));

    // Open NVS namespace (read-write)
    bool nvsOpen = s_nvsPrefs.begin("stasys", false);
    if (!nvsOpen) {
        Serial.println("[CONFIG] NVS open failed, using defaults");
        g_config = defaults;
        return;
    }

    // Load each value with default fallback
    loaded.sample_rate_hz = s_nvsPrefs.getUChar("sr", defaults.sample_rate_hz);
    if (loaded.sample_rate_hz == 0) loaded.sample_rate_hz = defaults.sample_rate_hz;

    loaded.piezo_threshold = s_nvsPrefs.getUShort("pz_th", defaults.piezo_threshold);
    if (loaded.piezo_threshold == 0) loaded.piezo_threshold = defaults.piezo_threshold;

    loaded.accel_threshold = s_nvsPrefs.getUShort("ax_th", defaults.accel_threshold);
    if (loaded.accel_threshold == 0) loaded.accel_threshold = defaults.accel_threshold;

    loaded.debounce_ms = s_nvsPrefs.getUShort("db_ms", defaults.debounce_ms);
    if (loaded.debounce_ms == 0) loaded.debounce_ms = defaults.debounce_ms;

    loaded.led_enabled = s_nvsPrefs.getBool("led", defaults.led_enabled);

    loaded.data_mode = s_nvsPrefs.getUChar("dm", defaults.data_mode);
    if (loaded.data_mode == 0 && defaults.data_mode != 0)
        loaded.data_mode = s_nvsPrefs.getUChar("dm", defaults.data_mode);

    loaded.streaming_rate_hz = s_nvsPrefs.getUShort("strm", defaults.streaming_rate_hz);
    if (loaded.streaming_rate_hz == 0) loaded.streaming_rate_hz = defaults.streaming_rate_hz;

    String nameStr = s_nvsPrefs.getString("name", DEFAULT_DEVICE_NAME);
    strncpy(loaded.device_name, nameStr.c_str(), sizeof(loaded.device_name) - 1);
    loaded.device_name[sizeof(loaded.device_name) - 1] = '\0';

    s_nvsPrefs.end();

    g_config = loaded;

    Serial.printf("[CONFIG] Loaded: SR=%d STRM=%d PZ=%d AX=%d DB=%d LED=%d DM=%d\n",
                  g_config.sample_rate_hz, g_config.streaming_rate_hz,
                  g_config.piezo_threshold, g_config.accel_threshold,
                  g_config.debounce_ms, g_config.led_enabled, g_config.data_mode);
}

void loadConfig(FirmwareConfig* cfg) {
    if (cfg == NULL) return;
    if (xSemaphoreTake(configMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        *cfg = g_config;
        xSemaphoreGive(configMutex);
    } else {
        memset(cfg, 0, sizeof(FirmwareConfig));
    }
}

bool saveConfig(const FirmwareConfig* cfg) {
    if (cfg == NULL) return false;

    bool ok = s_nvsPrefs.begin("stasys", false);
    if (!ok) return false;

    s_nvsPrefs.putUChar("sr", cfg->sample_rate_hz);
    s_nvsPrefs.putUShort("pz_th", cfg->piezo_threshold);
    s_nvsPrefs.putUShort("ax_th", cfg->accel_threshold);
    s_nvsPrefs.putUShort("db_ms", cfg->debounce_ms);
    s_nvsPrefs.putBool("led", cfg->led_enabled);
    s_nvsPrefs.putUChar("dm", cfg->data_mode);
    s_nvsPrefs.putUShort("strm", cfg->streaming_rate_hz);
    s_nvsPrefs.putString("name", cfg->device_name);

    s_nvsPrefs.end();
    Serial.println("[CONFIG] Saved to NVS");
    return true;
}

void updateConfig(const FirmwareConfig* newCfg) {
    if (newCfg == NULL) return;
    if (xSemaphoreTake(configMutex, portMAX_DELAY) == pdTRUE) {
        g_config = *newCfg;
        xSemaphoreGive(configMutex);
        // Deferred NVS save handled by caller
        saveConfig(newCfg);
    }
}

void getConfigCopy(FirmwareConfig* outCfg) {
    loadConfig(outCfg);
}

// ================= ADAPTIVE THRESHOLD PERSISTENCE =================
bool saveAdaptiveThreshold(const struct AdaptiveThresholdState* state) {
    if (state == NULL) return false;

    bool ok = s_nvsCalib.begin("calib", false);
    if (!ok) {
        // Try creating if it doesn't exist
        ok = s_nvsCalib.begin("calib", true);
        if (!ok) return false;
    }

    // Save as raw blob for simplicity
    size_t written = s_nvsCalib.putBytes("adapt", state, sizeof(struct AdaptiveThresholdState));
    s_nvsCalib.end();

    if (written == sizeof(struct AdaptiveThresholdState)) {
        Serial.printf("[CONFIG] Saved adaptive threshold: n=%u thr=%lu\n",
                      state->shot_peak_count, state->adaptive_threshold);
        return true;
    }
    Serial.println("[CONFIG] Failed to save adaptive threshold");
    return false;
}

bool loadAdaptiveThreshold(struct AdaptiveThresholdState* out) {
    if (out == NULL) return false;

    bool ok = s_nvsCalib.begin("calib", true);
    if (!ok) {
        memset(out, 0, sizeof(struct AdaptiveThresholdState));
        return false;
    }

    size_t len = s_nvsCalib.getBytes("adapt", out, sizeof(struct AdaptiveThresholdState));
    s_nvsCalib.end();

    if (len == sizeof(struct AdaptiveThresholdState)) {
        Serial.printf("[CONFIG] Loaded adaptive threshold: n=%u thr=%lu\n",
                      out->shot_peak_count, out->adaptive_threshold);
        return true;
    }

    // No saved state — initialize to zeros
    memset(out, 0, sizeof(struct AdaptiveThresholdState));
    return false;
}
