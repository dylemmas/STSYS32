#include "led.h"
#include <Arduino.h>
#include <driver/ledc.h>

// ================= GLOBALS =================
QueueHandle_t ledQueue = NULL;

static LEDMode s_currentMode = LEDMode::OFF;
static uint32_t s_lastPatternTime = 0;
static uint8_t  s_patternStep = 0;
static bool     s_hapticActive = false;
static uint32_t s_hapticEndTime = 0;
static uint8_t  s_ledBrightness = 255; // Max brightness
static uint8_t  s_hapticIntensity = 200; // Default haptic intensity

void initLED() {
    // Configure LED PWM (LEDC)
    ledcSetup(LEDC_LED_CHANNEL, LEDC_FREQUENCY, LEDC_RESOLUTION);
    ledcAttachPin(LED_PIN, LEDC_LED_CHANNEL);
    ledcWrite(LED_PIN, 0);  // Start off

    // Configure Haptic PWM
    ledcSetup(LEDC_HAPTIC_CHANNEL, LEDC_HAPTIC_FREQ, LEDC_RESOLUTION);
    ledcAttachPin(HAPTIC_PIN, LEDC_HAPTIC_CHANNEL);
    ledcWrite(HAPTIC_PIN, 0);  // Start off

    ledQueue = xQueueCreate(8, sizeof(LEDMode));
    if (ledQueue == NULL) {
        Serial.println("[LED] ERROR: Failed to create LED queue");
    }

    setLEDMode(LEDMode::BOOTING);
    Serial.println("[LED] Initialized with LEDC PWM");
}

void setLEDIntensity(uint8_t duty) {
    s_ledBrightness = duty;
}

void setLEDMode(LEDMode mode) {
    if (ledQueue != NULL) {
        LEDMode m = mode;
        xQueueSend(ledQueue, &m, 0);
    }
    s_currentMode = mode;
    s_patternStep = 0;
    s_lastPatternTime = millis();
}

void triggerShotFeedback() {
    // Flash LED 3x rapidly via LEDC
    setLEDMode(LEDMode::SHOT);

    // Trigger haptic pulse: ramp up → sustain → ramp down
    s_hapticActive = true;
    s_hapticEndTime = millis() + 200;
    ledcWrite(HAPTIC_PIN, s_hapticIntensity);
}

void ledTask(void* param) {
    (void)param;

    for (;;) {
        LEDMode mode;
        if (xQueueReceive(ledQueue, &mode, pdMS_TO_TICKS(10)) == pdTRUE) {
            s_currentMode = mode;
            s_patternStep = 0;
            s_lastPatternTime = millis();
        }

        uint32_t now = millis();
        uint32_t elapsed = now - s_lastPatternTime;
        uint8_t duty = 0;

        switch (s_currentMode) {
            case LEDMode::OFF:
                duty = 0;
                break;

            case LEDMode::BOOTING:
                // Slow blink: 500ms on, 500ms off (1Hz)
                duty = ((elapsed % 1000) < 500) ? s_ledBrightness : 0;
                break;

            case LEDMode::IDLE:
                // Double blink every 3s
                {
                    uint32_t cycle = elapsed % 3000;
                    duty = (cycle < 100) || (cycle >= 200 && cycle < 300) ? s_ledBrightness : 0;
                }
                break;

            case LEDMode::CONNECTED:
                // Solid on at brightness level
                duty = s_ledBrightness;
                break;

            case LEDMode::STREAMING: {
                // Smooth sine breathing: 1s cycle
                uint32_t cycle = elapsed % 1000;
                // Sine from 0 to PI: brightness = sin(cycle/1000 * PI) * max
                float sine = sinf(cycle * 3.14159265f / 1000.0f);
                duty = (uint8_t)((sine * 0.5f + 0.5f) * s_ledBrightness);
                break;
            }

            case LEDMode::SHOT: {
                // 3 rapid flashes: 50ms each, 50ms gap, total 300ms
                uint32_t cycle = elapsed % 300;
                bool flash = (cycle < 50) || (cycle >= 100 && cycle < 150) || (cycle >= 200 && cycle < 250);
                duty = flash ? s_ledBrightness : 0;
                if (elapsed > 500) {
                    s_currentMode = LEDMode::CONNECTED;
                    s_patternStep = 0;
                    s_lastPatternTime = now;
                }
                break;
            }

            case LEDMode::LOW_BATTERY:
                // Slow pulse: 1s on, 1s off
                duty = ((elapsed % 2000) < 1000) ? (s_ledBrightness / 2) : 0;
                break;

            case LEDMode::ERROR: {
                // SOS: short-short-short, long-long-long, short-short-short
                uint32_t cycle = elapsed % 2000;
                if (cycle < 300) {
                    duty = (cycle % 100) < 50 ? s_ledBrightness : 0;           // S: dit dit dit
                } else if (cycle < 600) {
                    duty = 0;
                } else if (cycle < 900) {
                    duty = (cycle % 300) < 150 ? s_ledBrightness : 0;        // O: daw daw daw
                } else if (cycle < 1200) {
                    duty = 0;
                } else if (cycle < 1500) {
                    duty = (cycle % 100) < 50 ? s_ledBrightness : 0;           // S: dit dit dit
                } else {
                    duty = 0;
                }
                break;
            }
        }

        ledcWrite(LED_PIN, duty);

        // Haptic timeout
        if (s_hapticActive && now >= s_hapticEndTime) {
            s_hapticActive = false;
            ledcWrite(HAPTIC_PIN, 0);
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
