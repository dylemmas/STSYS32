#ifndef SENSOR_H
#define SENSOR_H

#include <stdint.h>
#include <stdbool.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// ================= PINS =================
#define PIEZO_PIN        35   // ADC1_CH7 — safe with BT
#define MPU_INT_PIN       4    // GPIO for MPU6050 data-ready interrupt

// ================= I2C =================
#define I2C_SDA          21
#define I2C_SCL          22
#define MPU_ADDR         0x68

// ================= MPU6050 REGISTERS =================
#define MPU_REG_PWR_MGMT_1   0x6B
#define MPU_REG_PWR_MGMT_2   0x6C
#define MPU_REG_ACCEL_CONFIG 0x1C
#define MPU_REG_GYRO_CONFIG  0x1B
#define MPU_REG_CONFIG       0x1A    // DLPF
#define MPU_REG_INT_ENABLE   0x38
#define MPU_REG_INT_STATUS   0x3A
#define MPU_REG_WHO_AM_I     0x75
#define MPU_REG_SMPLRT_DIV    0x19
#define MPU_REG_ACCEL_XOUT_H 0x3B

// ================= RANGES =================
#define ACCEL_4G_LSB    8192.0   // LSB per g
#define GYRO_500DPS_LSB 65.5     // LSB per deg/s

// ================= SENSOR SAMPLE =================
struct SensorSample {
    uint32_t timestamp_us;
    int16_t  accel_x;
    int16_t  accel_y;
    int16_t  accel_z;
    int16_t  gyro_x;
    int16_t  gyro_y;
    int16_t  gyro_z;
    int16_t  temperature;   // raw register value
    uint16_t piezo;
    uint16_t piezo_raw;     // unfiltered raw ADC
    bool     valid;
};

// ================= SENSOR HEALTH =================
struct SensorHealth {
    bool     mpu_present;
    uint8_t  mpu_whoami;
    uint8_t  i2c_error_count;
    uint16_t samples_total;
    uint16_t samples_invalid;
    uint32_t last_i2c_error_time;
    uint8_t  i2c_recovery_count;
    bool     last_read_valid;
};

// ================= EXTERNALS =================
extern SemaphoreHandle_t dataReadySem;
extern QueueHandle_t recoveryQueue;   // Signal recovery task
extern SemaphoreHandle_t recoveryDoneSem;  // Recovery completion signal to sensorTask
extern volatile bool g_sensorDegraded; // True when MPU is not responding
extern uint8_t s_consecutiveErrors;  // I2C error counter (declared static in sensor.cpp)

// ================= FUNCTIONS =================
void     scanI2CBus();  // Diagnostic: print all responding I2C addresses
bool     initMPU6050();
bool     readSensorBurst(SensorSample* sample);
void     checkSensorHealth(SensorHealth* health);
void     recoverI2CBus();
void     getLastAccelGyro(int16_t* accel, int16_t* gyro);
void     runFactoryCalibration();
void     runUserCalibration();
void     loadCalibrationData(struct CalibrationData* out);
void     saveCalibrationData(const struct CalibrationData* data);

// Build sensor health flags bitmask (matches HEALTH_* in protocol.h)
uint8_t  getSensorHealthFlags();

// Query current sensor state (for health packet encoding)
bool     isSensorDegraded();  // True if MPU permanently failed
bool     isRecoveryInProgress();  // True while RecoveryTask is attempting recovery

// Low-level I2C
void     writeMPURegister(uint8_t reg, uint8_t val);
uint8_t  readMPURegister(uint8_t reg);

#endif // SENSOR_H
