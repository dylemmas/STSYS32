#include <Arduino.h>
#include "BluetoothSerial.h"
#include <Wire.h>
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_bt_api.h"
#include "mbedtls/sha256.h"

// ================= CONFIGURATION =================
#define BATTERY_PIN 39          // ADC1_CH3
#define PIEZO_PIN   35          // ADC1_CH7 (D35) - Safe for Bluetooth use
#define I2C_SDA 21              
#define I2C_SCL 22              
#define MPU_ADDR 0x68

#define VOLTAGE_DIVIDER_RATIO 2.0 
#define BATTERY_MAX_VOLTAGE 4.2 
#define BATTERY_MIN_VOLTAGE 3.0 

// --- TIMING CONFIGURATION ---
// We send to Python at 100Hz (10ms)
// BUT we read the sensor at 1000Hz (1ms) to catch the click
#define SEND_RATE_MS 10       
#define OVERSAMPLE_LOOPS 10   // Read 10 times per packet
#define CPU_FREQ_MHZ 240      // Max speed for high sample rate

const char* SECRET_KEY = "12ebaf10h12fa9123z21sti";

// =================================================

BluetoothSerial SerialBT;

volatile bool isAuthenticated = false;
volatile int batteryPercentage = 0;

// --- BINARY PACKET STRUCTURE ---
// Added 'piezo' field to transmit shockwave data
struct __attribute__((packed)) DataPacket {
  uint8_t header[2]; // 0xAA, 0xBB
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
  uint16_t piezo;    // Raw ADC value of the piezo shock
  uint8_t battery;
  uint8_t checksum;
};

// --- Helper Functions ---

void writeMPURegister(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

float readBatteryVoltage() {
  // Simple avg reading
  long sum = 0;
  for(int i=0; i<16; i++) sum += analogRead(BATTERY_PIN);
  return ((sum / 16.0) / 4095.0) * 3.3 * VOLTAGE_DIVIDER_RATIO;
}

int calculateBatteryPercentage(float voltage) {
  if (voltage >= BATTERY_MAX_VOLTAGE) return 100;
  if (voltage <= BATTERY_MIN_VOLTAGE) return 0;
  float percentage = ((voltage - BATTERY_MIN_VOLTAGE) / (BATTERY_MAX_VOLTAGE - BATTERY_MIN_VOLTAGE)) * 100.0;
  return constrain((int)percentage, 0, 100);
}

void handleAuthentication() {
  isAuthenticated = false;
  delay(500); 
  SerialBT.println("READY");

  String challenge = "";
  unsigned long startTime = millis();
  SerialBT.setTimeout(500); 

  while (millis() - startTime < 5000) {
    if (SerialBT.available()) {
      challenge = SerialBT.readStringUntil('\n');
      challenge.trim();
      if (challenge.length() > 0) break;
    }
    delay(10);
  }

  if (challenge.length() == 0) {
    SerialBT.disconnect(); 
    return;
  }

  String toHash = challenge + String(SECRET_KEY);
  byte hashResult[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  mbedtls_sha256_update(&ctx, (const unsigned char*)toHash.c_str(), toHash.length());
  mbedtls_sha256_finish(&ctx, hashResult);
  mbedtls_sha256_free(&ctx);

  char hexHash[65];
  for (int i = 0; i < 32; i++) sprintf(hexHash + (i * 2), "%02x", hashResult[i]);
  SerialBT.println(hexHash); 
  delay(200); 
  isAuthenticated = true; 
}

// --- MANTISX-STYLE SENSOR TASK ---
// This task runs strictly on Core 1 to ensure timing stability
void sensorTask(void *parameter) {
  
  // RAW variables
  int16_t rax, ray, raz, rgx, rgy, rgz;
  
  // Variables to hold the "Peak" sample found in the loop
  float peak_ax, peak_ay, peak_az;
  float avg_gx, avg_gy, avg_gz;
  uint16_t max_piezo; // Track peak piezo shock in the window
  
  // Tracking acceleration changes (Jerk)
  float prev_ax_val = 0, prev_ay_val = 0, prev_az_val = 0;

  for (;;) {
    if (SerialBT.connected()) {
      if (!isAuthenticated) handleAuthentication();
      
      if (isAuthenticated) {
        
        float max_shock_energy = -1.0;
        long gyro_sum_x = 0, gyro_sum_y = 0, gyro_sum_z = 0;
        max_piezo = 0; // Reset peak for this packet window

        // --- OVERSAMPLING LOOP (1kHz) ---
        // We read the sensor multiple times but only send ONE packet.
        // We pick the reading with the highest "Shock" value.
        for(int i=0; i<OVERSAMPLE_LOOPS; i++) {
            
            // 1. Fast I2C Read (Raw Registers)
            Wire.beginTransmission(MPU_ADDR);
            Wire.write(0x3B); 
            Wire.endTransmission(false);
            Wire.requestFrom(MPU_ADDR, 14, true);

            if (Wire.available() >= 14) {
                rax = Wire.read() << 8 | Wire.read();
                ray = Wire.read() << 8 | Wire.read();
                raz = Wire.read() << 8 | Wire.read();
                Wire.read(); Wire.read(); // Temp
                rgx = Wire.read() << 8 | Wire.read();
                rgy = Wire.read() << 8 | Wire.read();
                rgz = Wire.read() << 8 | Wire.read();
            }

            // 2. Read Piezo
            // We use analogRead inside the fast loop to catch the spike
            uint16_t current_piezo = analogRead(PIEZO_PIN);
            if (current_piezo > max_piezo) {
                max_piezo = current_piezo;
            }

            // 3. Convert Accel (4G Range = 8192 LSB)
            float c_ax = (rax / 8192.0) * 9.81;
            float c_ay = (ray / 8192.0) * 9.81;
            float c_az = (raz / 8192.0) * 9.81;

            // 4. Calculate "Shock Energy" (Absolute Jerk from Accel)
            // This detects the CLICK even if it's super short
            float jerk = abs(c_ax - prev_ax_val) + abs(c_ay - prev_ay_val) + abs(c_az - prev_az_val);
            
            // If this specific ms has the highest shock, save it as the "Peak"
            if (jerk > max_shock_energy) {
                max_shock_energy = jerk;
                peak_ax = c_ax;
                peak_ay = c_ay;
                peak_az = c_az;
            }

            // Accumulate Gyro for smoothing (Trace needs smoothness, not peaks)
            gyro_sum_x += rgx;
            gyro_sum_y += rgy;
            gyro_sum_z += rgz;

            // Update history
            prev_ax_val = c_ax;
            prev_ay_val = c_ay;
            prev_az_val = c_az;

            // Wait ~1ms to hit 1kHz sampling
            // I2C takes ~0.4ms, analogRead takes ~0.1ms. 
            // Reduced delay slightly to accommodate extra ADC read time.
            delayMicroseconds(400); 
        }

        // --- PREPARE PACKET ---
        // Use the PEAK Accel found (for Trigger Detect)
        // Use the PEAK Piezo found (for Shock Detect)
        // Use the AVERAGE Gyro (for clean Trace)
        
        DataPacket pkt;
        pkt.header[0] = 0xAA;
        pkt.header[1] = 0xBB;
        pkt.ax = peak_ax;
        pkt.ay = peak_ay;
        pkt.az = peak_az;
        
        // Convert Gyro Avg (500dps = 65.5 LSB)
        pkt.gx = ((gyro_sum_x / OVERSAMPLE_LOOPS) / 65.5) * 0.0174533;
        pkt.gy = ((gyro_sum_y / OVERSAMPLE_LOOPS) / 65.5) * 0.0174533;
        pkt.gz = ((gyro_sum_z / OVERSAMPLE_LOOPS) / 65.5) * 0.0174533;
        
        pkt.piezo = max_piezo; // Send the highest piezo value seen in this 10ms
        pkt.battery = (uint8_t)batteryPercentage;
        
        // XOR Checksum
        uint8_t* ptr = (uint8_t*)&pkt;
        pkt.checksum = 0;
        for(int i=2; i<sizeof(DataPacket)-1; i++) {
          pkt.checksum ^= ptr[i];
        }

        SerialBT.write((const uint8_t*)&pkt, sizeof(DataPacket));
        
      } else {
        delay(1000);
      }
    } else {
      isAuthenticated = false;
      delay(500);
    }
  }
}

void batteryMonitorTask(void *parameter) {
  int lastReportedVal = -1;
  for (;;) {
    float batteryVoltage = readBatteryVoltage();
    int newPercentage = calculateBatteryPercentage(batteryVoltage);
    if (lastReportedVal == -1 || abs(newPercentage - lastReportedVal) >= 2) {
      batteryPercentage = newPercentage;
      lastReportedVal = newPercentage;
    }
    vTaskDelay(2000 / portTICK_PERIOD_MS); 
  }
}

void setup() {
  setCpuFrequencyMhz(CPU_FREQ_MHZ); 
  Serial.begin(115200);
  
  uint64_t chipid = ESP.getEfuseMac(); 
  char uniqueName[30];
  sprintf(uniqueName, "STASYS-%04X", (uint16_t)(chipid >> 32));
  
  SerialBT.begin(uniqueName);
  Serial.printf("Device Started: %s\n", uniqueName);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  
  // Set Piezo pin to input (ADC1 is safe to use with BT)
  pinMode(PIEZO_PIN, INPUT);

  esp_bredr_tx_power_set(ESP_PWR_LVL_N0, ESP_PWR_LVL_P3);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000); // 400kHz Fast I2C
  delay(100);

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); 
  Wire.write(0x00); // Wake
  Wire.endTransmission();
  delay(50);

  // --- MANUAL REGISTER CONFIG FOR DRY FIRE ---
  // 1. Accel Config (0x1C): 4G Range (0x08)
  writeMPURegister(0x1C, 0x08); 
  
  // 2. Gyro Config (0x1B): 500dps Range (0x08)
  writeMPURegister(0x1B, 0x08); 
  
  // 3. DLPF Config (0x1A): Bandwidth 260Hz (0x00) -> CRITICAL for clicks
  // We want NO smoothing on the hardware side, we do it in software
  writeMPURegister(0x1A, 0x00);

  Serial.println("Sensor Configured: 4G / 500dps / 260Hz / 1kHz Polling / Piezo Active");
  
  xTaskCreatePinnedToCore(sensorTask, "SensorTask", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(batteryMonitorTask, "BatMonitor", 2048, NULL, 1, NULL, 0);
}

void loop() {
  vTaskDelay(1000 / portTICK_PERIOD_MS);
}