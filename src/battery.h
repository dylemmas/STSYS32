#ifndef BATTERY_H
#define BATTERY_H

#include <stdint.h>
#include <stdbool.h>

// ================= PINS =================
#define BATTERY_PIN       39   // ADC1_CH3
#define TP4056_STAT_PIN   34   // GPIO34 — TP4056 status output

// ================= BATTERY CONFIG =================
#define VOLTAGE_DIVIDER_RATIO  2.0
#define ADC_RESOLUTION         4095   // 12-bit ADC

// ================= BATTERY THRESHOLDS =================
#define BATTERY_CRITICAL_PCT  5    // Force deep sleep below this
#define BATTERY_LOW_PCT       15   // Warning LED below this
#define BATTERY_MAX_VOLTAGE   4.20 // 100% LiPo
#define BATTERY_MIN_VOLTAGE   3.00 // 0% LiPo (cutoff)
#define MAX_CHARGE_CYCLES     500  // Flag "replace" after this

// ================= BATTERY STATUS =================
struct BatteryStatus {
    uint8_t  percentage;     // 0-100
    bool     isCharging;
    bool     isLow;           // < 15%
    bool     isCritical;      // < 5%
    bool     needsReplacement; // > 500 cycles or voltage sag
    float    voltage;
    uint16_t chargeCycles;
};

// ================= EXTERNALS =================
extern volatile uint8_t g_batteryPercent;

// ================= FUNCTIONS =================
void     initBattery();
BatteryStatus readBattery();
void     updateBattery();
uint8_t  getBatteryPercent();
bool     isBatteryCharging();   // Fast check of charging status

// Force deep sleep (called when battery is critical)
void     batteryCriticalShutdown();

// Increment cycle count (call when charging completes)
void     batteryIncrementCycleCount();

#endif // BATTERY_H
