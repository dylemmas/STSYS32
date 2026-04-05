#include "protocol.h"
#include <security.h>
#include <string.h>
#include <Arduino.h>
#include <esp_random.h>

// ================= CRC-16/CCITT =================
uint16_t crc16_ccitt(const uint8_t* data, uint16_t len) {
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

// ================= ENCRYPTED PACKET WRAPPER =================
// Wraps any packet in an encrypted frame.
// Format: SYNC(2) + TYPE(1) + LEN(2) + IV(8) + CIPHERTEXT + TAG(8) + CRC(2)
// CIPHERTEXT = AES-128-CCM(plaintext_body = TYPE+LEN+PAYLOAD, nonce=IV)
// CRC computed over (IV + CIPHERTEXT + TAG)

static uint8_t s_encNonce[8];

uint16_t encodePacketEncrypted(uint8_t type, const void* payload, uint16_t len,
                               uint8_t* outBuffer) {
    if (!isLinkEncrypted()) {
        // Fall back to unencrypted if not in secure mode
        return encodePacket(type, payload, len, outBuffer);
    }

    // --- Build plaintext body ---
    uint8_t body[3 + MAX_PAYLOAD_SIZE];
    body[0] = type;
    body[1] = (uint8_t)(len & 0xFF);
    body[2] = (uint8_t)((len >> 8) & 0xFF);
    if (payload && len > 0) {
        memcpy(&body[3], payload, len);
    }

    // --- Generate random IV/nonce ---
    for (int i = 0; i < 8; i += 4) {
        uint32_t r = esp_random();
        s_encNonce[i]     = (r >> 0) & 0xFF;
        s_encNonce[i + 1] = (r >> 8) & 0xFF;
        s_encNonce[i + 2] = (r >> 16) & 0xFF;
        s_encNonce[i + 3] = (r >> 24) & 0xFF;
    }

    // --- Encrypt body with CCM ---
    uint8_t ciphertext[MAX_PAYLOAD_SIZE + 3];
    uint8_t tag[8];
    bool ok = encryptCCM(g_secState.session_key, s_encNonce,
                          NULL, 0,  // no AAD
                          body, 3 + len,
                          ciphertext, tag);
    if (!ok) {
        Serial.println("[PROT] Encrypt failed");
        return 0;
    }

    // --- Increment TX nonce for next packet ---
    for (int i = 7; i >= 0; i--) {
        if (++s_encNonce[i] != 0) break;
    }

    // --- Build packet ---
    uint16_t idx = 0;
    outBuffer[idx++] = SYNC_BYTE_0;
    outBuffer[idx++] = SYNC_BYTE_1;
    outBuffer[idx++] = PKT_TYPE_ENCRYPTED;

    uint16_t encLen = (3 + len) + 8; // ciphertext + tag
    outBuffer[idx++] = (uint8_t)(encLen & 0xFF);
    outBuffer[idx++] = (uint8_t)((encLen >> 8) & 0xFF);

    // IV
    memcpy(&outBuffer[idx], s_encNonce, 8);
    idx += 8;

    // Ciphertext
    memcpy(&outBuffer[idx], ciphertext, encLen - 8);
    idx += encLen - 8;

    // Tag
    memcpy(&outBuffer[idx], tag, 8);
    idx += 8;

    // CRC over (IV + ciphertext + tag)
    uint16_t crc = crc16_ccitt(&outBuffer[5], 8 + encLen);
    outBuffer[idx++] = (uint8_t)(crc & 0xFF);
    outBuffer[idx++] = (uint8_t)((crc >> 8) & 0xFF);

    return idx;
}

// Decrypt an encrypted payload in-place. Returns true on success.
// On success, sets outType and copies decrypted payload to outPayload.
bool decryptPacketPayload(const uint8_t* encPayload, uint16_t encLen,
                          uint8_t* outType, void* outPayload,
                          uint16_t* outPayloadLen) {
    if (encLen < ENCRYPTED_HEADER_SIZE) return false;

    const uint8_t* iv = encPayload;
    const uint8_t* ciphertext = encPayload + ENCRYPTED_IV_SIZE;
    uint16_t ctLen = encLen - ENCRYPTED_IV_SIZE - ENCRYPTED_TAG_SIZE;
    const uint8_t* tag = encPayload + encLen - ENCRYPTED_TAG_SIZE;

    // Build nonce from IV + RX nonce counter
    uint8_t nonce[8];
    memcpy(nonce, iv, 8);
    nonce[7] ^= g_secState.rx_nonce[7]; // Mix in counter

    // Increment RX nonce counter
    for (int i = 7; i >= 0; i--) {
        if (++g_secState.rx_nonce[i] != 0) break;
    }

    uint8_t plaintext[3 + MAX_PAYLOAD_SIZE];
    bool ok = decryptCCM(g_secState.session_key, nonce,
                           NULL, 0,
                           ciphertext, ctLen, tag,
                           plaintext);
    if (!ok) {
        Serial.println("[PROT] Decrypt failed");
        return false;
    }

    *outType = plaintext[0];
    *outPayloadLen = (uint16_t)plaintext[1] | ((uint16_t)plaintext[2] << 8);
    if (*outPayloadLen > MAX_PAYLOAD_SIZE) return false;
    if (*outPayloadLen > 0 && outPayload) {
        memcpy(outPayload, &plaintext[3], *outPayloadLen);
    }
    return true;
}

// ================= ENCODER =================
uint16_t encodePacket(uint8_t type, const void* payload, uint16_t len,
                      uint8_t* outBuffer) {
    if (len > MAX_PAYLOAD_SIZE) return 0;

    uint16_t idx = 0;

    // Sync bytes
    outBuffer[idx++] = SYNC_BYTE_0;
    outBuffer[idx++] = SYNC_BYTE_1;

    // Type
    outBuffer[idx++] = type;

    // Length (little-endian)
    outBuffer[idx++] = (uint8_t)(len & 0xFF);
    outBuffer[idx++] = (uint8_t)((len >> 8) & 0xFF);

    // Payload
    if (payload && len > 0) {
        memcpy(&outBuffer[idx], payload, len);
        idx += len;
    }

    // CRC of (TYPE + LEN + PAYLOAD)
    uint16_t crc = crc16_ccitt(&outBuffer[2], 3 + len);
    outBuffer[idx++] = (uint8_t)(crc & 0xFF);
    outBuffer[idx++] = (uint8_t)((crc >> 8) & 0xFF);

    return idx; // Total packet size
}

// ================= DECODER =================

// Decoder instance (static state)
static struct {
    DecoderState state;
    uint16_t payloadLen;
    uint16_t payloadIdx;
    uint16_t crcComputed;
    uint8_t  type;
    uint8_t  payload[MAX_PAYLOAD_SIZE];
    uint16_t crcReceived;
    // For encrypted packets: encrypted payload buffer
    uint16_t encPayloadLen;
    uint16_t encPayloadIdx;
} g_decoder;

// Secondary decoder for decrypted inner packets
static struct {
    DecoderState state;
    uint16_t payloadLen;
    uint16_t payloadIdx;
    uint16_t crcComputed;
    uint8_t  type;
    uint8_t  payload[MAX_PAYLOAD_SIZE];
    uint16_t crcReceived;
} s_innerDecoder;

static DecodedPacket s_innerPkt;
static bool s_innerPktReady = false;
static uint8_t s_innerPktIdx = 0;

void initDecoder() {
    memset(&g_decoder, 0, sizeof(g_decoder));
    g_decoder.state = DecoderState::WAIT_SYNC0;
    memset(&s_innerDecoder, 0, sizeof(s_innerDecoder));
    s_innerDecoder.state = DecoderState::WAIT_SYNC0;
    s_innerPktReady = false;
}

// Helper: feed decrypted bytes to inner decoder
static void feedInnerDecoder(const uint8_t* data, uint16_t len) {
    for (uint16_t i = 0; i < len; i++) {
        if (s_innerPktReady) continue;

        uint8_t byte = data[i];
        switch (s_innerDecoder.state) {
            case DecoderState::WAIT_SYNC0:
                if (byte == SYNC_BYTE_0) s_innerDecoder.state = DecoderState::WAIT_SYNC1;
                break;
            case DecoderState::WAIT_SYNC1:
                if (byte == SYNC_BYTE_1) s_innerDecoder.state = DecoderState::READ_TYPE;
                else if (byte == SYNC_BYTE_0) {} // stay
                else s_innerDecoder.state = DecoderState::WAIT_SYNC0;
                break;
            case DecoderState::READ_TYPE:
                s_innerDecoder.type = byte;
                s_innerDecoder.crcComputed = crc16_ccitt(&s_innerDecoder.type, 1);
                s_innerDecoder.state = DecoderState::READ_LEN_LO;
                break;
            case DecoderState::READ_LEN_LO:
                s_innerDecoder.payloadLen = byte;
                // Accumulate CRC over TYPE + LEN_LO as a continuous sequence.
                {
                    uint8_t hdr[2] = { s_innerDecoder.type, byte };
                    s_innerDecoder.crcComputed = crc16_ccitt(hdr, 2);
                }
                s_innerDecoder.state = DecoderState::READ_LEN_HI;
                break;
            case DecoderState::READ_LEN_HI: {
                s_innerDecoder.payloadLen |= ((uint16_t)byte << 8);
                // Accumulate CRC over TYPE + LEN_LO + LEN_HI as a continuous sequence.
                {
                    uint8_t hdr[3] = { s_innerDecoder.type,
                                       (uint8_t)(s_innerDecoder.payloadLen & 0xFF),
                                       byte };
                    s_innerDecoder.crcComputed = crc16_ccitt(hdr, 3);
                }
                if (s_innerDecoder.payloadLen > MAX_PAYLOAD_SIZE) {
                    s_innerDecoder.state = DecoderState::WAIT_SYNC0;
                    continue;
                }
                s_innerDecoder.payloadIdx = 0;
                s_innerDecoder.state = (s_innerDecoder.payloadLen == 0)
                    ? DecoderState::READ_CRC_LO
                    : DecoderState::READ_PAYLOAD;
                break;
            }
            case DecoderState::READ_PAYLOAD:
                s_innerDecoder.payload[s_innerDecoder.payloadIdx++] = byte;
                if (s_innerDecoder.payloadIdx >= s_innerDecoder.payloadLen) {
                    s_innerDecoder.state = DecoderState::READ_CRC_LO;
                }
                break;
            case DecoderState::READ_CRC_LO:
                s_innerDecoder.crcReceived = byte;
                s_innerDecoder.state = DecoderState::READ_CRC_HI;
                break;
            case DecoderState::READ_CRC_HI:
                s_innerDecoder.crcReceived |= ((uint16_t)byte << 8);
                s_innerDecoder.state = DecoderState::PACKET_READY;
                break;
            case DecoderState::PACKET_READY: {
                uint8_t allData[3 + MAX_PAYLOAD_SIZE];
                allData[0] = s_innerDecoder.type;
                allData[1] = (uint8_t)(s_innerDecoder.payloadLen & 0xFF);
                allData[2] = (uint8_t)((s_innerDecoder.payloadLen >> 8) & 0xFF);
                memcpy(&allData[3], s_innerDecoder.payload, s_innerDecoder.payloadLen);
                uint16_t crcCheck = crc16_ccitt(allData, 3 + s_innerDecoder.payloadLen);
                if (crcCheck == s_innerDecoder.crcReceived) {
                    s_innerPkt.type = s_innerDecoder.type;
                    s_innerPkt.payload_len = s_innerDecoder.payloadLen;
                    memcpy(s_innerPkt.payload, s_innerDecoder.payload, s_innerDecoder.payloadLen);
                    s_innerPktReady = true;
                }
                s_innerDecoder.state = DecoderState::WAIT_SYNC0;
                break;
            }
            default:
                break;
        }
    }
}

bool decodeByte(uint8_t byte, DecodedPacket* outPkt) {
    // Serve inner decoded packet if available
    if (s_innerPktReady) {
        memcpy(outPkt, &s_innerPkt, sizeof(DecodedPacket));
        s_innerPktReady = false;
        memset(&s_innerDecoder, 0, sizeof(s_innerDecoder));
        s_innerDecoder.state = DecoderState::WAIT_SYNC0;
        return true;
    }

    switch (g_decoder.state) {
        case DecoderState::WAIT_SYNC0:
            if (byte == SYNC_BYTE_0)
                g_decoder.state = DecoderState::WAIT_SYNC1;
            break;

        case DecoderState::WAIT_SYNC1:
            if (byte == SYNC_BYTE_1)
                g_decoder.state = DecoderState::READ_TYPE;
            else if (byte == SYNC_BYTE_0)
                g_decoder.state = DecoderState::WAIT_SYNC1; // stay
            else
                g_decoder.state = DecoderState::WAIT_SYNC0;
            break;

        case DecoderState::READ_TYPE:
            g_decoder.type = byte;
            g_decoder.crcComputed = crc16_ccitt(&g_decoder.type, 1);
            g_decoder.state = DecoderState::READ_LEN_LO;
            break;

        case DecoderState::READ_LEN_LO:
            g_decoder.payloadLen = byte;
            // Accumulate CRC over TYPE + LEN_LO as a continuous byte sequence.
            // CRC-16/CCITT must process bytes in order — XORing separate CRCs
            // gives a different result. Fix: build a temp buffer and call once.
            {
                uint8_t hdr[2] = { g_decoder.type, byte };
                g_decoder.crcComputed = crc16_ccitt(hdr, 2);
            }
            g_decoder.state = DecoderState::READ_LEN_HI;
            break;

        case DecoderState::READ_LEN_HI: {
            g_decoder.payloadLen |= ((uint16_t)byte << 8);
            // Accumulate CRC over TYPE + LEN_LO + LEN_HI as a continuous sequence.
            {
                uint8_t hdr[3] = { g_decoder.type,
                                   (uint8_t)(g_decoder.payloadLen & 0xFF),
                                   byte };
                g_decoder.crcComputed = crc16_ccitt(hdr, 3);
            }
            if (g_decoder.payloadLen > MAX_PACKET_SIZE) {
                g_decoder.state = DecoderState::WAIT_SYNC0;
                return false;
            }
            g_decoder.payloadIdx = 0;

            // For encrypted packets (TYPE=0xF0), reserve space for IV+tag
            if (g_decoder.type == PKT_TYPE_ENCRYPTED) {
                if (g_decoder.payloadLen < ENCRYPTED_HEADER_SIZE) {
                    g_decoder.state = DecoderState::WAIT_SYNC0;
                    return false;
                }
                g_decoder.encPayloadLen = g_decoder.payloadLen;
                g_decoder.encPayloadIdx = 0;
                g_decoder.state = DecoderState::READ_ENC_PAYLOAD;
            } else if (g_decoder.payloadLen == 0) {
                g_decoder.state = DecoderState::READ_CRC_LO;
            } else {
                g_decoder.state = DecoderState::READ_PAYLOAD;
            }
            }  // end READ_LEN_HI
            break;

        case DecoderState::READ_PAYLOAD:
            g_decoder.payload[g_decoder.payloadIdx++] = byte;
            if (g_decoder.payloadIdx >= g_decoder.payloadLen) {
                g_decoder.state = DecoderState::READ_CRC_LO;
            }
            break;

        case DecoderState::READ_ENC_PAYLOAD: {
            g_decoder.payload[g_decoder.encPayloadIdx++] = byte;
            if (g_decoder.encPayloadIdx >= g_decoder.encPayloadLen) {
                g_decoder.state = DecoderState::READ_CRC_LO;
            }
            break;
        }

        case DecoderState::READ_CRC_LO:
            g_decoder.crcReceived = byte;
            g_decoder.state = DecoderState::READ_CRC_HI;
            break;

        case DecoderState::READ_CRC_HI:
            g_decoder.crcReceived |= ((uint16_t)byte << 8);
            g_decoder.state = DecoderState::PACKET_READY;
            break;

        case DecoderState::PACKET_READY:
            // Should not happen — caller must consume PACKET_READY first
            g_decoder.state = DecoderState::WAIT_SYNC0;
            break;
    }

    if (g_decoder.state == DecoderState::PACKET_READY) {
        // Verify CRC over outer frame
        uint8_t headerAndPayload[6 + MAX_PACKET_SIZE];
        headerAndPayload[0] = g_decoder.type;
        headerAndPayload[1] = (uint8_t)(g_decoder.payloadLen & 0xFF);
        headerAndPayload[2] = (uint8_t)((g_decoder.payloadLen >> 8) & 0xFF);
        memcpy(&headerAndPayload[3], g_decoder.payload, g_decoder.payloadLen);
        uint16_t crcCheck = crc16_ccitt(headerAndPayload, 3 + g_decoder.payloadLen);

        g_decoder.state = DecoderState::WAIT_SYNC0;

        if (crcCheck != g_decoder.crcReceived) {
            Serial.printf("[PROT] CRC mismatch: got 0x%04X, expected 0x%04X\n",
                         g_decoder.crcReceived, crcCheck);
            return false;
        }

        // Handle encrypted packets: decrypt inner payload
        if (g_decoder.type == PKT_TYPE_ENCRYPTED) {
            uint8_t innerType = 0;
            uint16_t innerLen = 0;
            uint8_t innerPayload[MAX_PAYLOAD_SIZE];

            if (!decryptPacketPayload(g_decoder.payload, g_decoder.payloadLen,
                                       &innerType, innerPayload, &innerLen)) {
                Serial.println("[PROT] Decrypt failed, dropping packet");
                return false;
            }

            // Feed decrypted TYPE+LEN+PAYLOAD through inner decoder
            uint8_t innerData[3 + MAX_PAYLOAD_SIZE];
            innerData[0] = innerType;
            innerData[1] = (uint8_t)(innerLen & 0xFF);
            innerData[2] = (uint8_t)((innerLen >> 8) & 0xFF);
            memcpy(&innerData[3], innerPayload, innerLen);
            feedInnerDecoder(innerData, 3 + innerLen);

            // Return the inner decoded packet
            if (s_innerPktReady) {
                memcpy(outPkt, &s_innerPkt, sizeof(DecodedPacket));
                s_innerPktReady = false;
                memset(&s_innerDecoder, 0, sizeof(s_innerDecoder));
                s_innerDecoder.state = DecoderState::WAIT_SYNC0;
                return true;
            }
            return false;
        }

        // Regular unencrypted packet
        outPkt->type = g_decoder.type;
        outPkt->payload_len = g_decoder.payloadLen;
        memcpy(outPkt->payload, g_decoder.payload, g_decoder.payloadLen);
        return true;
    }

    return false;
}
