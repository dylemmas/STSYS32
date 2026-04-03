#include "security.h"
#include <Arduino.h>
#include <string.h>
#include <esp_err.h>
#include <esp_efuse.h>
#include <esp_efuse_table.h>
#include <esp_system.h>
#include <esp_wifi.h>
#include <driver/uart.h>
#include <mbedtls/ccm.h>
#include <mbedtls/md.h>

// ================= FORWARD DECLARATIONS =================
static void hmac_sha256(const uint8_t* key, size_t key_len,
                        const uint8_t* data, size_t data_len,
                        uint8_t* out);

// ================= GLOBALS =================
SecurityState g_secState = {
    .auth_state = AuthState::UNAUTHENTICATED,
    .link_encrypted = false,
    .session_id = 0,
};

// ================= DEVICE SECRET =================
// Efuse BLOCK1 (SECURE_BOOT_KEY) is the production key slot for device secrets.
// BLOCK2 is reserved, BLOCK3 (FLASH_CRYPT_KEY) for flash encryption.
// On blank/dev units, fallback to MAC-derivation so development still works.

static bool getChipMAC(uint8_t* outMAC) {
    esp_err_t err = esp_base_mac_addr_get(outMAC);
    if (err != ESP_OK) {
        Serial.printf("[SEC] Failed to read MAC: %d\n", err);
        return false;
    }
    return true;
}

// Check if efuse BLOCK1 has been written (not all 0xFF)
static bool isEfuseBlock1Written() {
    uint8_t buf[32] = {0};
    esp_err_t err = esp_efuse_read_block(EFUSE_BLK_SECURE_BOOT, buf, 0, 256);
    if (err != ESP_OK) {
        Serial.printf("[SEC] efuse read error: %d\n", err);
        return false;
    }
    // Check if any byte is != 0xFF
    for (int i = 0; i < 32; i++) {
        if (buf[i] != 0xFF) return true;
    }
    return false;
}

bool isDeviceSecretProvisioned() {
    // Return true if real efuse key is present, false for pure MAC fallback
    return isEfuseBlock1Written();
}

bool getDeviceSecret(uint8_t* outSecret, size_t* outLen) {
    // Try efuse BLOCK1 first (production)
    if (isEfuseBlock1Written()) {
        esp_err_t err = esp_efuse_read_block(EFUSE_BLK_SECURE_BOOT, outSecret, 0, 128);
        if (err == ESP_OK) {
            *outLen = 16;  // Use first 16 bytes for AES-128
            Serial.println("[SEC] Device secret from efuse BLOCK1 (SECURE_BOOT)");
            return true;
        }
        Serial.printf("[SEC] efuse BLOCK1 read failed: %d, falling back\n", err);
    }

    // Fallback: derive from MAC for development units
    uint8_t mac[6] = {0};
    if (!getChipMAC(mac)) {
        uint32_t chip_rev = REG_READ(0x3FF5A000 + 0x14);
        for (int i = 0; i < 6; i++) mac[i] = (chip_rev >> (i * 4)) & 0xFF;
        Serial.println("[SEC] WARNING: MAC read failed, using chip revision as seed");
    }

    static const uint8_t dev_key[] = "STASYS-DEVICE-KEY";
    uint8_t hash[32] = {0};
    hmac_sha256(dev_key, sizeof(dev_key) - 1, mac, 6, hash);
    memcpy(outSecret, hash, 16);
    *outLen = 16;

    Serial.printf("[SEC] Device secret derived from chip MAC (dev mode, "
                   "%02X:%02X:%02X:%02X:%02X:%02X)\n",
                   mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return true;
}

// ================= FACTORY PROVISIONING =================
// Writes a 16-byte device secret to efuse BLOCK1.
// This is a ONE-TIME operation — efuses cannot be reverted.
// Returns ESP_OK on success, or an esp_err_t on failure.
esp_err_t provisionDeviceSecret(const uint8_t* secret16) {
    if (isEfuseBlock1Written()) {
        Serial.println("[SEC] Efuse BLOCK1 already programmed — cannot overwrite");
        return ESP_FAIL;
    }

    esp_err_t err = esp_efuse_write_block(EFUSE_BLK_SECURE_BOOT, secret16, 0, 128);
    if (err != ESP_OK) {
        Serial.printf("[SEC] Provisioning failed: %d\n", err);
        return err;
    }

    Serial.println("[SEC] Device secret provisioned to efuse BLOCK1");
    Serial.println("[SEC] WARNING: Efuses are permanent — keep backup of secret!");
    return ESP_OK;
}

// ================= HKDF-SHA256 (simplified, embedded-safe) =================
// HKDF-Extract(secret, pin) = HMAC-SHA256(pin, secret)
// HKDF-Expand(prk, info, L) = HMAC-SHA256(prk, info || counter) truncated to L

static void hmac_sha256(const uint8_t* key, size_t key_len,
                        const uint8_t* data, size_t data_len,
                        uint8_t* out) {
    mbedtls_md_context_t ctx;
    mbedtls_md_init(&ctx);
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_setup(&ctx, info, 1);  // HMAC mode
    mbedtls_md_hmac_starts(&ctx, key, key_len);
    mbedtls_md_hmac_update(&ctx, data, data_len);
    mbedtls_md_hmac_finish(&ctx, out);
    mbedtls_md_free(&ctx);
}

void deriveSessionKey(const uint8_t* secret, size_t secret_len,
                      const uint8_t* pin, size_t pin_len,
                      uint8_t* outKey) {
    // Extract: prk = HMAC-SHA256(pin, secret)
    uint8_t prk[32];
    hmac_sha256(pin, pin_len, secret, secret_len, prk);

    // Expand: HMAC-SHA256(prk, "STASYS-SESSION-KEY-v1")
    static const char* info = "STASYS-SESSION-KEY-v1";
    uint8_t info_data[32 + 32];  // prk + info
    memcpy(info_data, prk, 32);
    memcpy(info_data + 32, info, strlen(info));

    uint8_t expanded[32];
    hmac_sha256(prk, 32, info_data, 32 + strlen(info), expanded);
    memcpy(outKey, expanded, 16);  // Use first 16 bytes as AES-128 key
}

// ================= AUTH =================
void initSecurity() {
    memset(&g_secState, 0, sizeof(g_secState));
    g_secState.auth_state = AuthState::UNAUTHENTICATED;
    g_secState.link_encrypted = false;
    Serial.println("[SEC] Security initialized");
}

bool isLinkEncrypted() {
    return g_secState.link_encrypted;
}

void generateChallenge(uint8_t* outChallenge) {
    for (int i = 0; i < 16; i += 4) {
        uint32_t r = esp_random();
        outChallenge[i]     = (r >> 0)  & 0xFF;
        outChallenge[i + 1] = (r >> 8)  & 0xFF;
        outChallenge[i + 2] = (r >> 16) & 0xFF;
        outChallenge[i + 3] = (r >> 24) & 0xFF;
    }
    memcpy(g_secState.challenge, outChallenge, 16);
    g_secState.auth_state = AuthState::CHALLENGE_SENT;
    Serial.printf("[SEC] Challenge generated (state=CHALLENGE_SENT)\n");
}

bool verifyAuthToken(const uint8_t* token, uint32_t session_id) {
    if (g_secState.auth_state != AuthState::CHALLENGE_SENT) {
        Serial.println("[SEC] AUTH FAIL: no challenge sent");
        return false;
    }

    uint8_t secret[16];
    size_t secret_len = 16;
    if (!getDeviceSecret(secret, &secret_len)) {
        Serial.println("[SEC] AUTH FAIL: no device secret");
        return false;
    }

    // Build message: session_id (4 bytes) + challenge (16 bytes) = 20 bytes
    uint8_t message[20];
    message[0] = (session_id >> 0) & 0xFF;
    message[1] = (session_id >> 8) & 0xFF;
    message[2] = (session_id >> 16) & 0xFF;
    message[3] = (session_id >> 24) & 0xFF;
    memcpy(&message[4], g_secState.challenge, 16);

    // Compute HMAC-SHA256: HMAC(secret, session_id || challenge)
    uint8_t computed[32];
    hmac_sha256(secret, secret_len, message, sizeof(message), computed);

    // Constant-time comparison
    volatile uint8_t diff = 0;
    for (int i = 0; i < 32; i++) {
        diff |= computed[i] ^ token[i];
    }

    if (diff != 0) {
        Serial.println("[SEC] AUTH FAIL: token mismatch");
        g_secState.auth_state = AuthState::UNAUTHENTICATED;
        return false;
    }

    // Auth success: derive session key
    uint8_t pin[4] = {'1', '2', '3', '4'};
    deriveSessionKey(secret, secret_len, pin, 4, g_secState.session_key);
    g_secState.session_id = session_id;
    g_secState.link_encrypted = true;
    g_secState.auth_state = AuthState::AUTHENTICATED;
    memset(g_secState.nonce, 0, 8);
    memset(g_secState.rx_nonce, 0, 8);
    Serial.printf("[SEC] AUTH SUCCESS, link encrypted, session_id=%u\n", session_id);
    return true;
}

void setSessionAuthenticated(uint32_t session_id) {
    g_secState.auth_state = AuthState::AUTHENTICATED;
    g_secState.session_id = session_id;
    g_secState.link_encrypted = true;
    memset(g_secState.nonce, 0, 8);
    memset(g_secState.rx_nonce, 0, 8);
}

// ================= AES-128 CCM ENCRYPTION =================
bool encryptCCM(const uint8_t* key, const uint8_t* nonce,
                const uint8_t* aad, uint16_t aad_len,
                const uint8_t* plaintext, uint16_t pt_len,
                uint8_t* outCiphertext, uint8_t* outTag) {
    if (pt_len > 240) return false;

    mbedtls_ccm_context ctx;
    mbedtls_ccm_init(&ctx);

    int err = mbedtls_ccm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, 128);
    if (err != 0) {
        mbedtls_ccm_free(&ctx);
        Serial.printf("[SEC] CCM setkey failed: %d\n", err);
        return false;
    }

    err = mbedtls_ccm_encrypt_and_tag(&ctx, pt_len,
                                        nonce, 8,
                                        aad, aad_len,
                                        plaintext, outCiphertext,
                                        outTag, 8);

    mbedtls_ccm_free(&ctx);

    if (err != 0) {
        Serial.printf("[SEC] CCM encrypt failed: %d\n", err);
        return false;
    }
    return true;
}

bool decryptCCM(const uint8_t* key, const uint8_t* nonce,
                const uint8_t* aad, uint16_t aad_len,
                const uint8_t* ciphertext, uint16_t ct_len,
                const uint8_t* tag,
                uint8_t* outPlaintext) {
    if (ct_len > 240) return false;

    mbedtls_ccm_context ctx;
    mbedtls_ccm_init(&ctx);

    int err = mbedtls_ccm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, 128);
    if (err != 0) {
        mbedtls_ccm_free(&ctx);
        return false;
    }

    // mbedtls_ccm_auth_decrypt is the correct API for decrypt + verify tag
    err = mbedtls_ccm_auth_decrypt(&ctx, ct_len,
                                     nonce, 8,
                                     aad, aad_len,
                                     ciphertext, outPlaintext,
                                     tag, 8);

    mbedtls_ccm_free(&ctx);

    if (err != 0) {
        Serial.printf("[SEC] CCM decrypt/tag fail: %d\n", err);
        return false;
    }
    return true;
}
