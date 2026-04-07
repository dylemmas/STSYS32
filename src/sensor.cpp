#include "sensor.h"
#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>
#include "config.h"
#include <Preferences.h>

// ================= I2C BUS SCAN =================
void scanI2CBus() {
    Serial.println("[SENSOR] Scanning I2C bus...");
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(400000);
    delay(50);
    uint8_t found = 0;
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        uint8_t err = Wire.endTransmission();
        if (err == 0) {
            // Read WHO_AM_I if it's a known sensor
            uint8_t whoami = 0xFF;
            if (addr == 0x68 || addr == 0x69) {
                Wire.beginTransmission(addr);
                Wire.write(0x75); // WHO_AM_I
                Wire.endTransmission(false);
                Wire.requestFrom(addr, (uint8_t)1);
                if (Wire.available()) whoami = Wire.read();
            }
            Serial.printf("  [SENSOR] I2C addr=0x%02X (%s) WHOAMI=0x%02X\n",
                          addr, (addr == 0x68 ? "MPU default" : addr == 0x69 ? "MPU alt" : "other"), whoami);
            found++;
        } else if (err == 4) {
            Serial.printf("  [SENSOR] I2C addr=0x%02X: unknown error\n", addr);
        }
    }
    Serial.printf("[SENSOR] Scan complete: %d device(s) found\n", found);
}

// ================= GLOBALS =================
SemaphoreHandle_t dataReadySem = NULL;
QueueHandle_t recoveryQueue = NULL;
volatile bool g_sensorDegraded = false;

// Recovery completion notification — RecoveryTask signals this after a
// successful reinit so sensorTask can update g_sensorDegraded.
SemaphoreHandle_t recoveryDoneSem = NULL;
static bool g_recoverySuccess = false;

// Health flags (bit 0 = degraded, bit 1 = recovery in progress)
static volatile uint8_t s_sensorStatusFlags = 0;
#define STATUS_DEGRADED       0x01
#define STATUS_RECOVERY_DONE  0x02

static SensorHealth s_health = {
    .mpu_present = false,
    .mpu_whoami = 0,
    .i2c_error_count = 0,
    .samples_total = 0,
    .samples_invalid = 0,
    .last_i2c_error_time = 0,
    .i2c_recovery_count = 0,
    .last_read_valid = false,
};

// I2C error tracking
uint8_t s_consecutiveErrors = 0;  // Non-static: accessed by recoveryTask in main.cpp
static uint8_t s_recoveryFailCount = 0;
static uint8_t s_consecutiveInvalidReads = 0;
static bool s_recoveryInProgress = false;

// Last valid reading (for graceful degradation)
static int16_t s_lastAccel[3] = {0, 0, 0};
static int16_t s_lastGyro[3] = {0, 0, 0};

// Calibration data
static CalibrationData s_calData = {
    .accel_bias_x = 0, .accel_bias_y = 0, .accel_bias_z = 0,
    .gyro_bias_x = 0, .gyro_bias_y = 0, .gyro_bias_z = 0,
    .temp_coeff = 0,
    .mount_mode = 0,
    .is_calibrated = false,
    .factory_calibrated = false,
};

// ================= MPU6050 REGISTER OPERATIONS =================
void writeMPURegister(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t readMPURegister(uint8_t reg) {
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1, (uint8_t)1);
    if (Wire.available()) return Wire.read();
    return 0xFF;
}

// ================= ISR =================
static volatile bool s_dataReadyFlag = false;

void IRAM_ATTR mpuISR() {
    BaseType_t higherPriorityTaskWoken = pdFALSE;
    if (dataReadySem != NULL) {
        xSemaphoreGiveFromISR(dataReadySem, &higherPriorityTaskWoken);
    }
    s_dataReadyFlag = true;
    if (higherPriorityTaskWoken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

// ================= MPU6050 INITIALIZATION =================
bool initMPU6050() {
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(400000); // 400kHz Fast I2C

    delay(50);

    // Check WHOAMI — accept both MPU6050 (0x68) and MPU6500 (0x70)
    uint8_t whoami = readMPURegister(MPU_REG_WHO_AM_I);
    s_health.mpu_whoami = whoami;
    s_health.mpu_present = (whoami == 0x68 || whoami == 0x70);
    if (!s_health.mpu_present) {
        Serial.printf("[SENSOR] MPU6050/6500 not found (WHOAMI=0x%02X)\n", whoami);
        return false;
    }
    if (whoami == 0x68) {
        Serial.printf("[SENSOR] MPU6050 found (WHOAMI=0x%02X)\n", whoami);
    } else {
        Serial.printf("[SENSOR] MPU6500 found (WHOAMI=0x%02X)\n", whoami);
    }

    // Wake up MPU6050
    writeMPURegister(MPU_REG_PWR_MGMT_1, 0x00);  // Clear sleep bit
    delay(50);

    // Disable I2C master mode (use I2C bypass for aux sensor access)
    writeMPURegister(MPU_REG_PWR_MGMT_1, 0x01);  // Clock = PLL with X gyro ref
    delay(50);

    // Accelerometer: 4G range (0x08)
    writeMPURegister(MPU_REG_ACCEL_CONFIG, 0x08);

    // Gyroscope: 500 dps range (0x08)
    writeMPURegister(MPU_REG_GYRO_CONFIG, 0x08);

    // DLPF: 188Hz bandwidth -> ~1kHz output rate (0x03)
    // DLPF 188Hz gives 1kHz sample rate with 8MHz internal clock
    writeMPURegister(MPU_REG_CONFIG, 0x03);

    // Sample rate divider: 0 -> 1kHz
    writeMPURegister(MPU_REG_SMPLRT_DIV, 0x00);

    // Enable data-ready interrupt
    writeMPURegister(MPU_REG_INT_ENABLE, 0x01);  // DATA_RDY_EN

    // Configure INT pin as push-pull, high until data ready
    writeMPURegister(0x37, 0x00);  // INT pin config (active low, push-pull)

    // Create binary semaphore for ISR
    if (dataReadySem == NULL) {
        dataReadySem = xSemaphoreCreateBinary();
        if (dataReadySem == NULL) {
            Serial.println("[SENSOR] ERROR: Failed to create data ready semaphore");
            return false;
        }
    }

    // Create recovery queue
    if (recoveryQueue == NULL) {
        recoveryQueue = xQueueCreate(1, sizeof(bool));
    }

    // Create recovery completion semaphore
    if (recoveryDoneSem == NULL) {
        recoveryDoneSem = xSemaphoreCreateBinary();
        // Take it initially so the first wait returns only on recovery completion
        xSemaphoreTake(recoveryDoneSem, 0);
    }

    // Attach interrupt to MPU INT pin
    pinMode(MPU_INT_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(MPU_INT_PIN), mpuISR, RISING);

    // Load calibration data
    loadCalibrationData(&s_calData);
    Serial.printf("[SENSOR] Calibrated: %s, accel_bias=(%d,%d,%d)\n",
                 s_calData.is_calibrated ? "yes" : "no",
                 s_calData.accel_bias_x, s_calData.accel_bias_y, s_calData.accel_bias_z);

    Serial.println("[SENSOR] MPU6050 initialized: 4G / 500dps / DLPF_188Hz / INT enabled");
    return true;
}

// ================= BURST READ =================
// Forward declaration (defined later in this file)
static void applyMountRotation(int16_t* ax, int16_t* ay, int16_t* az);

bool readSensorBurst(SensorSample* sample) {
    if (sample == NULL) return false;

    // Declare all variables up front so goto invalid_read is valid
    int16_t raw_ax = 0, raw_ay = 0, raw_az = 0;
    int16_t raw_temp = 0, raw_gx = 0, raw_gy = 0, raw_gz = 0;
    int16_t cal_ax = 0, cal_ay = 0, cal_az = 0;
    int16_t cal_gx = 0, cal_gy = 0, cal_gz = 0;

    Wire.beginTransmission(MPU_ADDR);
    Wire.write(MPU_REG_ACCEL_XOUT_H);
    uint8_t err = Wire.endTransmission(false);
    if (err != 0) {
        s_health.i2c_error_count++;
        s_health.last_i2c_error_time = millis();
        s_consecutiveErrors++;
        s_health.samples_invalid++;

        // Signal async recovery task (non-blocking) after 5 consecutive errors.
        // RecoveryTask clears s_consecutiveErrors and g_sensorDegraded on success.
        if (s_consecutiveErrors > 5 && recoveryQueue != NULL && !s_recoveryInProgress) {
            bool signal = true;
            xQueueSend(recoveryQueue, &signal, 0);
            s_recoveryInProgress = true;
            Serial.println("[SENSOR] 5+ consecutive I2C errors — signaling RecoveryTask");
        }
        goto invalid_read;
    }

    s_consecutiveErrors = 0;

    Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14, (uint8_t)1);
    if (Wire.available() < 14) {
        s_health.i2c_error_count++;
        s_health.samples_invalid++;
        goto invalid_read;
    }

    // Burst read: ACCEL_XOUT -> TEMP -> GYRO
    raw_ax = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_ay = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_az = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_temp = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_gx = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_gy = (int16_t)(Wire.read() << 8 | Wire.read());
    raw_gz = (int16_t)(Wire.read() << 8 | Wire.read());

    // Apply calibration: bias subtraction
    cal_ax = raw_ax - s_calData.accel_bias_x;
    cal_ay = raw_ay - s_calData.accel_bias_y;
    cal_az = raw_az - s_calData.accel_bias_z;
    cal_gx = raw_gx - s_calData.gyro_bias_x;
    cal_gy = raw_gy - s_calData.gyro_bias_y;
    cal_gz = raw_gz - s_calData.gyro_bias_z;

    // Apply temperature compensation to gyro biases.
    // MPU6050 temp sensor: 340 LSB/°C, offset at 36.53°C = 0.
    // Convert raw_temp to Celsius and correct gyro readings.
    if (s_calData.temp_coeff != 0) {
        float tempC = (raw_temp / 340.0f) + 36.53f;
        float deltaT = tempC - 25.0f;  // Reference temperature: 25°C
        int16_t correction = (int16_t)(deltaT * (s_calData.temp_coeff / 1000.0f));
        cal_gx += correction;
        cal_gy += (int16_t)(correction * 9 / 10);  // Gyro Y slightly less sensitive
        cal_gz += (int16_t)(correction * 8 / 10);  // Gyro Z slightly less sensitive
    }

    // Apply mount rotation
    applyMountRotation(&cal_ax, &cal_ay, &cal_az);

    sample->accel_x = cal_ax;
    sample->accel_y = cal_ay;
    sample->accel_z = cal_az;
    sample->temperature = raw_temp;
    sample->gyro_x = cal_gx;
    sample->gyro_y = cal_gy;
    sample->gyro_z = cal_gz;

    sample->timestamp_us = esp_timer_get_time();
    sample->piezo = analogRead(PIEZO_PIN);
    sample->piezo_raw = sample->piezo;
    sample->valid = true;

    s_health.samples_total++;
    s_health.last_read_valid = true;
    s_consecutiveInvalidReads = 0;

    // Clear degraded mode on successful read (fix: also clear after recovery completes)
    if (g_sensorDegraded) {
        g_sensorDegraded = false;
        s_sensorStatusFlags &= ~STATUS_DEGRADED;
        Serial.println("[SENSOR] MPU6050 recovered from degraded mode");
    }

    // Check for recovery completion signal from RecoveryTask
    if (xSemaphoreTake(recoveryDoneSem, 0) == pdTRUE) {
        // RecoveryTask completed — update degraded flag based on outcome
        g_sensorDegraded = !g_recoverySuccess;
        if (g_recoverySuccess) {
            g_sensorDegraded = false;
            s_sensorStatusFlags &= ~STATUS_DEGRADED;
            Serial.println("[SENSOR] RecoveryTask completed: sensor healthy");
        } else {
            g_sensorDegraded = true;
            s_sensorStatusFlags |= STATUS_DEGRADED;
            Serial.println("[SENSOR] RecoveryTask completed: PERMANENTLY DEGRADED");
        }
        s_recoveryInProgress = false;
    }

    // Update last valid values for graceful degradation
    s_lastAccel[0] = sample->accel_x;
    s_lastAccel[1] = sample->accel_y;
    s_lastAccel[2] = sample->accel_z;
    s_lastGyro[0] = sample->gyro_x;
    s_lastGyro[1] = sample->gyro_y;
    s_lastGyro[2] = sample->gyro_z;

    return true;

invalid_read:
    // Count consecutive invalid reads for degraded mode.
    // Only enter degraded mode if recovery is NOT in progress — let
    // RecoveryTask attempt recovery first before suppressing streaming.
    if (!s_recoveryInProgress && ++s_consecutiveInvalidReads >= 5) {
        if (!g_sensorDegraded) {
            g_sensorDegraded = true;
            s_sensorStatusFlags |= STATUS_DEGRADED;
            Serial.println("[SENSOR] Entering degraded mode (5 consecutive invalid reads)");
        }
    }
    sample->valid = false;
    return false;
}

// ================= I2C RECOVERY =================
void recoverI2CBus() {
    Serial.println("[SENSOR] I2C recovery: resetting bus");

    // End current I2C
    Wire.end();

    // Toggle SDA line manually to clear stuck slave
    pinMode(I2C_SDA, OUTPUT_OPEN_DRAIN);
    pinMode(I2C_SCL, OUTPUT_OPEN_DRAIN);

    for (int i = 0; i < 9; i++) {
        digitalWrite(I2C_SCL, LOW);
        delayMicroseconds(5);
        digitalWrite(I2C_SDA, HIGH);
        delayMicroseconds(5);
        digitalWrite(I2C_SCL, HIGH);
        delayMicroseconds(5);
    }

    digitalWrite(I2C_SCL, LOW);
    delayMicroseconds(5);
    digitalWrite(I2C_SDA, LOW);
    delayMicroseconds(5);
    digitalWrite(I2C_SDA, HIGH);
    delayMicroseconds(5);

    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, INPUT_PULLUP);

    // Reinitialize I2C and sensor
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(400000);

    delay(50);

    bool reinit = initMPU6050();
    if (reinit) {
        s_recoveryFailCount = 0;
        s_consecutiveErrors = 0;  // Reset error counter on successful recovery
        g_recoverySuccess = true;
        Serial.println("[SENSOR] I2C recovery successful — sensor operational");
    } else {
        s_recoveryFailCount++;
        g_recoverySuccess = false;
        Serial.printf("[SENSOR] I2C recovery failed (attempt %d)\n", s_recoveryFailCount);
        if (s_recoveryFailCount >= 3) {
            Serial.println("[SENSOR] FATAL: MPU6050 recovery failed 3 times — permanently degraded");
        }
    }

    // Signal completion so sensorTask can update g_sensorDegraded
    if (recoveryDoneSem != NULL) {
        xSemaphoreGive(recoveryDoneSem);
    }

    s_health.i2c_recovery_count++;
}

// ================= HEALTH CHECK =================
void checkSensorHealth(SensorHealth* health) {
    if (health == NULL) return;
    // Copy current health snapshot (simple read, no locking needed for this use)
    *health = s_health;
}

uint8_t getSensorHealthFlags() {
    uint8_t flags = 0;
    if (g_sensorDegraded) flags |= 0x01;  // HEALTH_MPU_FAULT
    if (!s_calData.is_calibrated && !s_calData.factory_calibrated) flags |= 0x02;  // HEALTH_CAL_NEEDED
    if (s_health.i2c_error_count > 10) flags |= 0x04;  // HEALTH_I2C_ERRORS
    if (s_recoveryInProgress) flags |= 0x08;           // HEALTH_RECOVERY_IN_PROGRESS
    return flags;
}

void getLastAccelGyro(int16_t* accel, int16_t* gyro) {
    if (accel) {
        accel[0] = s_lastAccel[0];
        accel[1] = s_lastAccel[1];
        accel[2] = s_lastAccel[2];
    }
    if (gyro) {
        gyro[0] = s_lastGyro[0];
        gyro[1] = s_lastGyro[1];
        gyro[2] = s_lastGyro[2];
    }
}

bool isSensorDegraded() {
    return g_sensorDegraded;
}

bool isRecoveryInProgress() {
    return s_recoveryInProgress;
}

// ================= CALIBRATION =================

// Apply mount rotation matrix to sensor data
// MPU6050 axes: X=right, Y=forward (toward target), Z=up (against gravity)
//   Gravity: az=+1g when device is upright (Z up)
// Mount modes 0-3: device upright, rotated around Z (yaw)
// Mount modes 4-5: device barrel-under (device Z along barrel), pitch around X
// Mount mode 6: device side-mounted, roll around Y
static void applyMountRotation(int16_t* ax, int16_t* ay, int16_t* az) {
    int16_t bx = *ax, by = *ay, bz = *az;
    switch (s_calData.mount_mode) {
        case 0: // Standard: no rotation (device upright, Z up)
            break;
        case 1: // Rotated 90° clockwise around Z (yaw +90°)
            *ax = by; *ay = -bx; *az = bz;
            break;
        case 2: // Inverted 180° around Z (yaw +180°)
            *ax = -bx; *ay = -by; *az = bz;
            break;
        case 3: // Rotated 270° around Z (yaw +270°)
            *ax = -by; *ay = bx; *az = bz;
            break;
        case 4: // Barrel-under: 90° pitch forward (device on its side, Z points toward target)
            // Rotate around X by +90°: Y→Z, Z→-X, X→Y
            *ax = by; *ay = -bz; *az = bx;
            break;
        case 5: // Barrel-under inverted: 270° pitch (device on its side, Z points toward target, upside-down)
            // Rotate around X by -90°: Y→-Z, Z→X, X→Y
            *ax = by; *ay = bz; *az = -bx;
            break;
        case 6: // Side mount: 90° roll (device on its side, X points toward target)
            // Rotate around Y by +90°: Z→X, X→-Z, Y→Y
            *ax = bz; *ay = by; *az = -bx;
            break;
    }
}

void loadCalibrationData(CalibrationData* out) {
    Preferences cal;
    if (cal.begin("calib", true)) {
        out->accel_bias_x = cal.getShort("ax", 0);
        out->accel_bias_y = cal.getShort("ay", 0);
        out->accel_bias_z = cal.getShort("az", 0);
        out->gyro_bias_x = cal.getShort("gx", 0);
        out->gyro_bias_y = cal.getShort("gy", 0);
        out->gyro_bias_z = cal.getShort("gz", 0);
        out->temp_coeff = cal.getShort("tc", 0);
        out->mount_mode = cal.getUChar("mm", 0);
        out->is_calibrated = cal.getBool("cal", false);
        out->factory_calibrated = cal.getBool("fcal", false);
        cal.end();
    } else {
        memset(out, 0, sizeof(CalibrationData));
    }
}

void saveCalibrationData(const CalibrationData* data) {
    Preferences cal;
    if (cal.begin("calib", false)) {
        cal.putShort("ax", data->accel_bias_x);
        cal.putShort("ay", data->accel_bias_y);
        cal.putShort("az", data->accel_bias_z);
        cal.putShort("gx", data->gyro_bias_x);
        cal.putShort("gy", data->gyro_bias_y);
        cal.putShort("gz", data->gyro_bias_z);
        cal.putShort("tc", data->temp_coeff);
        cal.putUChar("mm", data->mount_mode);
        cal.putBool("cal", data->is_calibrated);
        cal.putBool("fcal", data->factory_calibrated);
        cal.end();
    }
    s_calData = *data;
    Serial.printf("[CAL] Saved: accel_bias=(%d,%d,%d) gyro_bias=(%d,%d,%d)\n",
                 data->accel_bias_x, data->accel_bias_y, data->accel_bias_z,
                 data->gyro_bias_x, data->gyro_bias_y, data->gyro_bias_z);
}

void runFactoryCalibration() {
    Serial.println("[CAL] Running factory calibration (500 samples)...");

    // Wait for device to stabilize
    delay(500);

    int32_t axSum = 0, aySum = 0, azSum = 0;
    int32_t gxSum = 0, gySum = 0, gzSum = 0;

    for (int i = 0; i < 500; i++) {
        SensorSample s;
        if (readSensorBurst(&s) && s.valid) {
            axSum += s.accel_x;
            aySum += s.accel_y;
            azSum += s.accel_z - 8192;  // Remove gravity (~1g)
            gxSum += s.gyro_x;
            gySum += s.gyro_y;
            gzSum += s.gyro_z;
        }
        delay(10);
    }

    CalibrationData cal;
    loadCalibrationData(&cal);
    cal.accel_bias_x = (int16_t)(axSum / 500);
    cal.accel_bias_y = (int16_t)(aySum / 500);
    cal.accel_bias_z = (int16_t)(azSum / 500);
    cal.gyro_bias_x = (int16_t)(gxSum / 500);
    cal.gyro_bias_y = (int16_t)(gySum / 500);
    cal.gyro_bias_z = (int16_t)(gzSum / 500);
    cal.is_calibrated = true;
    cal.factory_calibrated = true;
    saveCalibrationData(&cal);

    Serial.printf("[CAL] Factory calibration complete\n");
}

void runUserCalibration() {
    Serial.println("[CAL] Running user calibration (300 samples)...");

    delay(500);

    int32_t axSum = 0, aySum = 0, azSum = 0;
    int32_t gxSum = 0, gySum = 0, gzSum = 0;
    int32_t axSq = 0, aySq = 0, azSq = 0;
    int valid = 0;

    for (int i = 0; i < 300; i++) {
        SensorSample s;
        if (readSensorBurst(&s) && s.valid) {
            axSum += s.accel_x;
            aySum += s.accel_y;
            azSum += s.accel_z - 8192;
            gxSum += s.gyro_x;
            gySum += s.gyro_y;
            gzSum += s.gyro_z;
            axSq += (int32_t)s.accel_x * s.accel_x;
            aySq += (int32_t)s.accel_y * s.accel_y;
            valid++;
        }
        delay(10);
    }

    if (valid < 100) {
        Serial.println("[CAL] User calibration failed: too few samples");
        return;
    }

    CalibrationData cal;
    loadCalibrationData(&cal);
    cal.accel_bias_x = (int16_t)(axSum / valid);
    cal.accel_bias_y = (int16_t)(aySum / valid);
    cal.accel_bias_z = (int16_t)(azSum / valid);
    cal.gyro_bias_x = (int16_t)(gxSum / valid);
    cal.gyro_bias_y = (int16_t)(gySum / valid);
    cal.gyro_bias_z = (int16_t)(gzSum / valid);
    cal.is_calibrated = true;
    saveCalibrationData(&cal);

    // Compute quality score (0-100): lower variance = higher quality
    int32_t varX = axSq / valid - (int32_t)(axSum / valid) * (int32_t)(axSum / valid);
    int32_t varY = aySq / valid - (int32_t)(aySum / valid) * (int32_t)(aySum / valid);
    int variance = (int)sqrtf(varX + varY);
    uint8_t quality = (uint8_t)constrain(100 - variance / 100, 0, 100);
    Serial.printf("[CAL] User calibration done, quality=%u\n", quality);
}