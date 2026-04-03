#ifndef LED_H
#define LED_H

#include <stdint.h>
#include <stdbool.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

// ================= PINS =================
#define LED_PIN           2    // On-board LED (most ESP32 devkits have this)
#define HAPTIC_PIN        32   // PWM output for haptic motor

// ================= LEDC CONFIG =================
#define LEDC_LED_CHANNEL  0    // LED channel (0-7)
#define LEDC_HAPTIC_CHANNEL 1 // Haptic channel
#define LEDC_RESOLUTION   8   // 8-bit: 0-255
#define LEDC_FREQUENCY    1000 // 1kHz LED PWM
#define LEDC_HAPTIC_FREQ  150  // 150Hz for typical ERM/LRA motors

// ================= LED MODES =================
enum class LEDMode {
    OFF,
    BOOTING,        // Slow blink (1Hz)
    IDLE,           // Double blink every 3s
    CONNECTED,      // Solid on
    STREAMING,      // Breathing pulse (1s cycle)
    SHOT,           // 3x rapid flash
    LOW_BATTERY,    // Slow pulse
    ERROR           // SOS pattern
};

// ================= EXTERNALS =================
extern QueueHandle_t ledQueue;

// ================= FUNCTIONS =================
void  initLED();
void  setLEDMode(LEDMode mode);
void  setLEDIntensity(uint8_t duty);   // 0-255 PWM duty cycle
void  triggerShotFeedback();         // Flash pattern + haptic pulse
void  ledTask(void* param);          // LED task function (FreeRTOS task)

#endif // LED_H
