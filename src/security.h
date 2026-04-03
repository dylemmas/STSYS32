#ifndef SECURITY_H
#define SECURITY_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <esp_err.h>

// ================= SECURITY STATE =================
enum class AuthState {
    UNAUTHENTICATED,
    CHALLENGE_SENT,
    AUTHENTICATED
};

struct SecurityState {
    AuthState auth_state;
    bool      link_encrypted;
    uint8_t   session_key[16];     // AES-128 key derived per-session
    uint8_t   nonce[8];            // 64-bit nonce for CCM (increments per TX packet)
    uint8_t   rx_nonce[8];        // Nonce for RX (increments per RX packet)
    uint32_t  session_id;         // Current session ID for auth
    uint8_t   challenge[16];       // Server challenge for HMAC auth
};

// ================= EXTERNALS =================
extern SecurityState g_secState;

#ifndef __cplusplus
extern struct SecurityState g_secState;
#endif

// ================= FUNCTIONS =================

// --- Init ---
void     initSecurity();
bool     isLinkEncrypted();

// --- Auth ---
void     generateChallenge(uint8_t* outChallenge);
bool     verifyAuthToken(const uint8_t* token, uint32_t session_id);
void     setSessionAuthenticated(uint32_t session_id);

// --- Encryption ---
// Encrypt plaintext using AES-128 CCM with AAD
// outTag must be 8 bytes. Returns true on success.
bool     encryptCCM(const uint8_t* key, const uint8_t* nonce,
                    const uint8_t* aad, uint16_t aad_len,
                    const uint8_t* plaintext, uint16_t pt_len,
                    uint8_t* outCiphertext, uint8_t* outTag);

// Decrypt ciphertext using AES-128 CCM with AAD
// Returns true on success (data written to outPlaintext)
bool     decryptCCM(const uint8_t* key, const uint8_t* nonce,
                    const uint8_t* aad, uint16_t aad_len,
                    const uint8_t* ciphertext, uint16_t ct_len,
                    const uint8_t* tag,
                    uint8_t* outPlaintext);

// Derive session key from device secret + PIN via HKDF-SHA256
void     deriveSessionKey(const uint8_t* secret, size_t secret_len,
                          const uint8_t* pin, size_t pin_len,
                          uint8_t* outKey);

// --- Device Secret (efuse) ---
// Get device unique secret from efuse (or MAC fallback on dev units).
bool     getDeviceSecret(uint8_t* outSecret, size_t* outLen);
bool     isDeviceSecretProvisioned();

// Factory provisioning — writes 16-byte secret to efuse BLOCK1 (ONE-TIME, permanent)
// Returns ESP_OK on success. Only call this during manufacturing provisioning!
esp_err_t provisionDeviceSecret(const uint8_t* secret16);

#endif // SECURITY_H
