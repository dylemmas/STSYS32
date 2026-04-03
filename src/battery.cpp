#include "battery.h"
#include <Arduino.h>
#include <Preferences.h>
#include <esp_sleep.h>

// ================= GLOBALS =================
volatile uint8_t g_batteryPercent = 0;

// ================= STATIC =================
static Preferences s_nvsBattery;
static BatteryStatus s_lastStatus = {
    .percentage = 0,
    .isCharging = false,
    .isLow = false,
    .isCritical = false,
    .needsReplacement = false,
    .voltage = 0.0f,
    .chargeCycles = 0,
};
static float s_voltageSmooth = 3.7f;
static bool s_wasCharging = false;

// ================= LiPo DISCHARGE CURVE =================
// Voltage → capacity lookup table (10 points)
// Based on typical 3.7V LiPo discharge characteristics
// Format: { voltage, capacity_percent }
static const struct { float voltage; uint8_t pct; } s_voltageCurve[] = {
    { 4.20f, 100 },
    { 4.10f,  98 },
    { 4.00f,  95 },
    { 3.92f,  90 },
    { 3.80f,  80 },
    { 3.70f,  65 },
    { 3.60f,  50 },
    { 3.50f,  30 },
    { 3.35f,  15 },
    { 3.00f,   0 },
};
#define CURVE_SIZE (sizeof(s_voltageCurve) / sizeof(s_voltageCurve[0]))

// Linear interpolation lookup
static uint8_t voltageToPercent(float voltage) {
    if (voltage >= BATTERY_MAX_VOLTAGE) return 100;
    if (voltage <= BATTERY_MIN_VOLTAGE) return 0;

    // Find surrounding points in curve
    for (int i = 0; i < (int)CURVE_SIZE - 1; i++) {
        if (voltage <= s_voltageCurve[i].voltage &&
            voltage >= s_voltageCurve[i + 1].voltage) {
            float v0 = s_voltageCurve[i + 1].voltage;
            float v1 = s_voltageCurve[i].voltage;
            float t = (voltage - v0) / (v1 - v0);
            return (uint8_t)(s_voltageCurve[i + 1].pct +
                             t * (s_voltageCurve[i].pct - s_voltageCurve[i + 1].pct));
        }
    }
    return 0;
}

// ================= NVS CYCLE COUNT =================
static void loadCycleCount() {
    if (!s_nvsBattery.begin("battery", false)) return;
    s_lastStatus.chargeCycles = s_nvsBattery.getUShort("cycles", 0);
    s_nvsBattery.end();
}

void batteryIncrementCycleCount() {
    if (!s_nvsBattery.begin("battery", false)) return;
    uint16_t cycles = s_nvsBattery.getUShort("cycles", 0);
    cycles++;
    s_nvsBattery.putUShort("cycles", cycles);
    s_lastStatus.chargeCycles = cycles;
    Serial.printf("[BATT] Charge cycle count: %u\n", cycles);
    s_nvsBattery.end();
}

// ================= INIT =================
void initBattery() {
    pinMode(BATTERY_PIN, ANALOG);
    pinMode(TP4056_STAT_PIN, INPUT_PULLUP);

    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    loadCycleCount();
    updateBattery();

    Serial.printf("[BATT] Init: %d%%, cycles=%u\n",
                  getBatteryPercent(), s_lastStatus.chargeCycles);
}

// ================= READ =================
BatteryStatus readBattery() {
    // Read voltage (average of 16 samples for stability)
    int32_t sum = 0;
    for (int i = 0; i < 16; i++) {
        sum += analogRead(BATTERY_PIN);
        delayMicroseconds(100);
    }
    float voltage = ((sum / 16.0f) / ADC_RESOLUTION) * 3.3f * VOLTAGE_DIVIDER_RATIO;

    // IIR low-pass filter (smooths out transient fluctuations)
    s_voltageSmooth = s_voltageSmooth * 0.95f + voltage * 0.05f;

    // Check charging status (active LOW on TP4056)
    bool charging = (digitalRead(TP4056_STAT_PIN) == LOW);

    // Detect charge completion → increment cycle count
    if (s_wasCharging && !charging) {
        batteryIncrementCycleCount();
    }
    s_wasCharging = charging;

    // Convert voltage to percentage using discharge curve
    int pct = voltageToPercent(s_voltageSmooth);
    pct = constrain(pct, 0, 100);

    // Update status
    s_lastStatus.voltage = s_voltageSmooth;
    s_lastStatus.percentage = (uint8_t)pct;
    s_lastStatus.isCharging = charging;
    s_lastStatus.isLow = (pct < BATTERY_LOW_PCT && !charging);
    s_lastStatus.isCritical = (pct < BATTERY_CRITICAL_PCT);
    s_lastStatus.needsReplacement = (s_lastStatus.chargeCycles > MAX_CHARGE_CYCLES);

    return s_lastStatus;
}

void updateBattery() {
    BatteryStatus st = readBattery();
    g_batteryPercent = st.percentage;

    // Critical battery: force deep sleep
    if (st.isCritical && !st.isCharging) {
        batteryCriticalShutdown();
    }
}

uint8_t getBatteryPercent() {
    return g_batteryPercent;
}

bool isBatteryCharging() {
    return s_lastStatus.isCharging;
}

void batteryCriticalShutdown() {
    Serial.println("[BATT] CRITICAL: Battery below 5%, entering deep sleep");
    Serial.flush();

    // Save current state to NVS before sleep
    // Wake sources: any GPIO interrupt or timer
    esp_sleep_enable_timer_wakeup(3600 * 1000000ULL); // Wake in 1 hour for retry
    esp_deep_sleep_start();
}
