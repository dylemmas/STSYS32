#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>

// Force 1-byte packing for all packet structs below
#pragma pack(push, 1)

// ================= PACKET TYPES =================
#define PKT_TYPE_CMD_START_SESSION    0x01
#define PKT_TYPE_CMD_STOP_SESSION     0x02
#define PKT_TYPE_CMD_GET_INFO         0x03
#define PKT_TYPE_CMD_GET_CONFIG       0x04
#define PKT_TYPE_CMD_SET_CONFIG       0x05
#define PKT_TYPE_CMD_AUTH             0x06    // Auth token (Phase 1.2)
#define PKT_TYPE_CMD_FACTORY_RESET   0x0B    // Factory reset
#define PKT_TYPE_CMD_OTA_START       0x0C    // Begin OTA update
#define PKT_TYPE_CMD_OTA_DATA        0x0D    // OTA firmware chunk
#define PKT_TYPE_CMD_OTA_END         0x0E    // Finalize OTA
#define PKT_TYPE_CMD_OTA_ABORT       0x0F    // Abort OTA
#define PKT_TYPE_CMD_OTA_STATUS      0x11    // Get OTA progress/status
#define PKT_TYPE_CMD_GET_SESSIONS    0x20    // Enumerate stored sessions
#define PKT_TYPE_CMD_GET_SESSION_DATA 0x21    // Download session data
#define PKT_TYPE_CMD_DELETE_SESSION  0x22    // Delete a session
#define PKT_TYPE_CMD_CALIBRATE_START  0x23    // Start user calibration
#define PKT_TYPE_CMD_CALIBRATE_STATUS 0x24    // Get calibration quality
#define PKT_TYPE_CMD_SET_MOUNT_MODE   0x25    // Set mount orientation
#define PKT_TYPE_CMD_GET_CALIBRATION  0x26    // Get calibration data
#define PKT_TYPE_CMD_GET_COREDUMP    0x41    // Download coredump from flash
#define PKT_TYPE_CMD_ERASE_COREDUMP  0x42    // Erase stored coredump
#define PKT_TYPE_CMD_GET_SHOT_STATS  0x43    // Get adaptive threshold stats

// Security: encrypted frame (payload contains IV+encrypted_body+tag)
#define PKT_TYPE_ENCRYPTED            0xF0    // Encrypted packet wrapper

#define PKT_TYPE_EVT_SESSION_STARTED   0x10
#define PKT_TYPE_EVT_SESSION_STOPPED  0x11
#define PKT_TYPE_EVT_SHOT_DETECTED    0x12
#define PKT_TYPE_EVT_SENSOR_HEALTH     0x13
#define PKT_TYPE_EVT_AUTH_CHALLENGE    0x14    // Auth challenge from server (Phase 1.2)

#define PKT_TYPE_DATA_RAW_SAMPLE       0x20

#define PKT_TYPE_RSP_ERROR            0x80
#define PKT_TYPE_RSP_INFO             0x81
#define PKT_TYPE_RSP_CONFIG          0x82
#define PKT_TYPE_RSP_ACK             0x83
#define PKT_TYPE_RSP_OTA_STATUS      0x84    // OTA progress response

// ================= PACKET HEADERS =================
#define SYNC_BYTE_0     0xAA
#define SYNC_BYTE_1     0x55
#define HEADER_SIZE     6   // SYNC(2) + TYPE(1) + LEN(2) + CRC(2)

// Max payload size (DATA_RAW_SAMPLE = 26 bytes, fits easily)
#define MAX_PAYLOAD_SIZE    64
#define MAX_PACKET_SIZE     (HEADER_SIZE + MAX_PAYLOAD_SIZE)

// ================= ENCRYPTED PACKET LAYOUT =================
// When PKT_TYPE_ENCRYPTED is received, the payload is:
// [IV(8)] [CIPHERTEXT(n)] [TAG(8)]
// Where CIPHERTEXT contains: [TYPE(1)] [LEN_LO(1)] [LEN_HI(1)] [PAYLOAD(n)]
// CRC is computed over the CIPHERTEXT + TAG bytes
#define ENCRYPTED_IV_SIZE     8
#define ENCRYPTED_TAG_SIZE    8
#define ENCRYPTED_HEADER_SIZE (ENCRYPTED_IV_SIZE + ENCRYPTED_TAG_SIZE)

// CMD_AUTH payload: session_id(4) + token(32)
struct PktAuth {
    uint32_t session_id;
    uint8_t  token[32];       // HMAC-SHA256 response
};

// EVT_AUTH_CHALLENGE payload: session_id(4) + challenge(16) = 20 bytes
// The client MUST use the session_id from this packet (not derive from challenge bytes)
struct PktAuthChallenge {
    uint32_t session_id;
    uint8_t  challenge[16];
};

// ================= FEATURE FLAGS =================
#define FEATURE_OTA_SUPPORTED    0x0001
#define FEATURE_STORAGE_SUPPORTED 0x0002
#define FEATURE_ENCRYPTED        0x0004
#define FEATURE_AUTH_REQUIRED    0x0008
#define FEATURE_PWM_LED          0x0010
#define FEATURE_HAPTIC_PWM      0x0020
#define FEATURE_DEGRADED_MODE   0x0040
#define FEATURE_CALIBRATED       0x0080
#define FEATURE_COREDUMP         0x0100

// ================= SENSOR HEALTH FLAGS =================
#define HEALTH_MPU_FAULT      0x01   // MPU6050 not responding
#define HEALTH_CAL_NEEDED     0x02   // Calibration required
#define HEALTH_I2C_ERRORS     0x04   // High I2C error rate

// ================= DECODER STATES =================
enum class DecoderState {
    WAIT_SYNC0,
    WAIT_SYNC1,
    READ_TYPE,
    READ_LEN_LO,
    READ_LEN_HI,
    READ_PAYLOAD,
    READ_CRC_LO,
    READ_CRC_HI,
    PACKET_READY,
    READ_ENC_PAYLOAD,
    READ_ENC_TAG
};

// ================= PACKET STRUCTURES =================

// EVT_SESSION_STARTED
struct PktSessionStarted {
    uint32_t session_id;
    uint32_t timestamp_us;
    uint8_t  battery_percent;
    uint8_t  sensor_health;
    uint32_t free_heap;
} __attribute__((packed));

// EVT_SESSION_STOPPED
struct PktSessionStopped {
    uint32_t session_id;
    uint32_t duration_ms;
    uint16_t shot_count;
    uint8_t  battery_end;
    uint8_t  sensor_health;
} __attribute__((packed));

// EVT_SHOT_DETECTED (26 bytes total, no padding with __attribute__((packed)))
struct PktShotDetected {
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
} __attribute__((packed));

// EVT_SENSOR_HEALTH
struct PktSensorHealth {
    uint8_t  mpu_present;
    uint8_t  i2c_errors;
    uint16_t samples_total;
    uint16_t samples_invalid;
    uint8_t  i2c_recovery_count;
    uint8_t  reserved[4];
} __attribute__((packed));

// DATA_RAW_SAMPLE
struct PktRawSample {
    uint32_t sample_counter;
    uint32_t timestamp_us;
    int16_t  accel_x;
    int16_t  accel_y;
    int16_t  accel_z;
    int16_t  gyro_x;
    int16_t  gyro_y;
    int16_t  gyro_z;
    uint16_t piezo;
    int16_t  temperature;     // 0.01 deg C units
} __attribute__((packed));

// RSP_INFO
struct PktInfo {
    uint32_t firmware_version;   // e.g. 0x010000 = v1.0.0
    uint8_t  hardware_rev;
    uint32_t build_timestamp;
    uint16_t supported_features;
    uint8_t  mpu_whoami;
    uint8_t  reserved[2];
} __attribute__((packed));

// RSP_CONFIG
struct PktConfig {
    uint8_t  sample_rate_hz;
    uint16_t piezo_threshold;
    uint16_t accel_threshold;
    uint16_t debounce_ms;
    uint8_t  led_enabled;
    uint8_t  data_mode;          // 0=both, 1=raw-only, 2=events-only
    uint16_t streaming_rate_hz;
    char     device_name[20];
    uint8_t  reserved[19];       // padded to 50 bytes for protocol spec
} __attribute__((packed));

// RSP_ERROR
struct PktError {
    uint8_t  error_code;
    char     message[32];
};

// RSP_ACK
struct PktAck {
    uint8_t  command_id;
    uint8_t  status;
};

// RSP_OTA_STATUS
struct PktOTAStatus {
    uint8_t  state;         // 0=IDLE,1=RECEIVING,2=VERIFYING,3=COMPLETE,4=ERROR
    uint8_t  reserved;
    uint32_t bytes_received;
    uint32_t total_expected;
} __attribute__((packed));

// RSP_SHOT_STATS (Phase 1.3)
struct PktShotStats {
    uint16_t shot_count;
    uint16_t mean_peak;
    uint16_t stddev_peak;
    uint32_t adaptive_threshold;
    uint8_t  adaptive_enabled;
} __attribute__((packed));

// Restore default packing
#pragma pack(pop)

// ================= DECODED PACKET =================
struct DecodedPacket {
    uint8_t  type;
    uint16_t payload_len;
    uint8_t  payload[MAX_PAYLOAD_SIZE];
};

// ================= TX ITEM =================
struct TXItem {
    uint8_t  data[MAX_PACKET_SIZE];
    uint16_t length;
};

// ================= CRC =================
uint16_t crc16_ccitt(const uint8_t* data, uint16_t len);

// ================= ENCODER =================
uint16_t encodePacket(uint8_t type, const void* payload, uint16_t len,
                      uint8_t* outBuffer);
uint16_t encodePacketEncrypted(uint8_t type, const void* payload, uint16_t len,
                               uint8_t* outBuffer);

// ================= DECRYPTION =================
bool decryptPacketPayload(const uint8_t* encPayload, uint16_t encLen,
                          uint8_t* outType, void* outPayload,
                          uint16_t* outPayloadLen);

// ================= DECODER =================
void     initDecoder();
bool     decodeByte(uint8_t byte, DecodedPacket* outPkt);

#endif // PROTOCOL_H
