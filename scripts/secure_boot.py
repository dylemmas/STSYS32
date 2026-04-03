# secure_boot.py — ESP32 Secure Boot + Flash Encryption post-build signing
#
# Usage (in platformio.ini):
#   extra_scripts = post:scripts/secure_boot.py
#   ; Or for flash encryption variant:
#   extra_scripts = post:scripts/secure_boot.py:encrypt
#
# What this script does:
#   1. Generates a 256-bit RSA-3071 signing key (secure_boot_signing_key.pem)
#      if one doesn't already exist. KEEP THIS KEY SECURE — it's the root of trust.
#
#   2. Signs the compiled firmware with espsecure.py sign_app
#      This embeds the signature into the app partition so the ROM bootloader
#      can verify it on every boot.
#
#   3. For "encrypt" variant: also encrypts the flash image so that code/data
#      can't be extracted from the physical flash chip.
#
#   4. Prints the esptool commands you need to run ONCE to burn efuses.
#      (PlatformIO can't burn efuses — that requires esptool with esptool.py
#       connected to the chip physically.)
#
# Efuse flags burned (one-time per device):
#   --set-flash-scheme ESP_BOOTLOADER_FLASH_BOOT   : secure boot v1 (ESP32 legacy)
#   --secure-boot-enable                           : enable secure boot
#   --secure-boot-key-reflash-dest EMBEDED_INDICATOR: allow reflashing with same key
#   --secure-boot-revoke-efc                       : revoke no keys (default)
#   --jtag-disable                                 : disable JTAG (critical!)
#   --spi-boot-crypt-dec                           : enable flash encryption
#   --spiffs-crypt-dest                            : encrypt SPIFFS
#   --spiffs-crypt-src                             : encrypt SPIFFS source
#
# IMPORTANT: Burn efuses LAST, after everything works. Efuses are ONE-TIME.
#   Always back up your signing key before burning efuses:
#     cp secure_boot_signing_key.pem secure_boot_signing_key.pem.backup
#
# Flash encryption: When flash encryption is enabled, ALL subsequent flashes
#   must be encrypted. Keep the --flash-encrypt flag in esptool for every flash.
#
# For ESP32-S2/S3/C3 (RISC-V), secure boot v2 uses ECDSA keys instead of RSA.
#   This script targets ESP32 (legacy secure boot v1 with RSA-3071).

import os
import sys
import subprocess
import hashlib

Import("env")

ESPTOOL   = env.subst("${PIOPACKAGES}/tool-esptoolpy/esptool.py")
ESPSECURE = env.subst("${PIOPACKAGES}/tool-esptoolpy/espsecure.py")

KEY_FILE    = "secure_boot_signing_key.pem"
FLASH_KEY   = "flash_encryption_key.bin"
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR  = os.path.dirname(SCRIPT_DIR)
KEY_PATH     = os.path.join(PROJECT_DIR, KEY_FILE)
FLASH_KEY_PATH = os.path.join(PROJECT_DIR, FLASH_KEY)
SIGN_MODE    = "encrypt" in sys.argv  # post:secure_boot.py:encrypt → flash encryption

BOARD_MCU    = env.get("BOARD_MCU", "")
BUILD_DIR    = env["BUILD_DIR"]
FIRMWARE_IN  = os.path.join(BUILD_DIR, "firmware.bin")
FLASH_SIZE   = env.get("BOARD_FLASH_SIZE", "4MB")

# ------------------------------------------------------------------
# 1. Find the compiled firmware .bin
# ------------------------------------------------------------------
def find_firmware_bin():
    """Look for the firmware binary in the build directory."""
    candidates = [
        os.path.join(BUILD_DIR, "firmware.bin"),
        os.path.join(BUILD_DIR, " bootloader", "bootloader.bin"),
    ]
    for f in candidates:
        if os.path.exists(f):
            return f
    # Try to find any .bin that isn't the bootloader
    try:
        for f in os.listdir(BUILD_DIR):
            if f.endswith(".bin") and "bootloader" not in f.lower():
                return os.path.join(BUILD_DIR, f)
    except:
        pass
    print(f"[secure_boot] WARNING: Could not find firmware binary in {BUILD_DIR}")
    return None

# ------------------------------------------------------------------
# 2. Key generation (only if missing)
# ------------------------------------------------------------------
def ensure_signing_key():
    """Generate RSA-3071 signing key if not present."""
    if os.path.exists(KEY_PATH):
        print(f"[secure_boot] Using existing signing key: {KEY_FILE}")
        return True

    print(f"[secure_boot] Generating RSA-3071 signing key: {KEY_FILE}")
    try:
        # Generate 3072-bit RSA key (3071-bit is the actual security level)
        subprocess.run(
            ["openssl", "genrsa", "-out", KEY_PATH, "3072"],
            check=True,
            capture_output=True,
            text=True,
        )
        os.chmod(KEY_PATH, 0o600)  # Restrict permissions
        print(f"[secure_boot] Signing key generated: {KEY_FILE}")
        print(f"[secure_boot] *** BACKUP THIS KEY! ***")
        print(f"[secure_boot] *** Loss of this key = device is bricked ***")
        return True
    except FileNotFoundError:
        print("[secure_boot] ERROR: openssl not found. Install OpenSSL or generate the key manually:")
        print(f"[secure_boot]   openssl genrsa -out {KEY_FILE} 3072")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[secure_boot] Key generation failed: {e.stderr}")
        return False

# ------------------------------------------------------------------
# 3. Generate flash encryption key (for encrypt variant)
# ------------------------------------------------------------------
def ensure_flash_key():
    """Generate random 256-bit flash encryption key if not present."""
    if os.path.exists(FLASH_KEY_PATH):
        print(f"[secure_boot] Using existing flash encryption key: {FLASH_KEY}")
        return True

    print(f"[secure_boot] Generating flash encryption key: {FLASH_KEY}")
    try:
        key = os.urandom(32)
        with open(FLASH_KEY_PATH, "wb") as f:
            f.write(key)
        os.chmod(FLASH_KEY_PATH, 0o600)
        print(f"[secure_boot] Flash encryption key generated: {FLASH_KEY}")
        return True
    except Exception as e:
        print(f"[secure_boot] Flash key generation failed: {e}")
        return False

# ------------------------------------------------------------------
# 4. Sign the firmware
# ------------------------------------------------------------------
def sign_firmware(bin_path):
    """Sign the firmware binary with espsecure.py."""
    if not os.path.exists(bin_path):
        print(f"[secure_boot] SKIP: firmware not found: {bin_path}")
        return False

    # Determine key digest for display
    try:
        result = subprocess.run(
            ["openssl", "rsa", "-in", KEY_PATH, "-outform", "pem",
             "-pubout", "-out", "/dev/null"],
            check=True,
            capture_output=True,
            text=True,
        )
        digest = hashlib.sha256(result.stdout.encode()).hexdigest()[:16]
        print(f"[secure_boot] Signing key SHA256 (first 16 hex): {digest}...")
    except:
        pass

    try:
        print(f"[secure_boot] Signing firmware: {bin_path}")
        subprocess.run(
            [sys.executable, ESPSECURE, "sign_app", "--keyfile", KEY_PATH,
             "--output", bin_path, bin_path],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"[secure_boot] Firmware signed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[secure_boot] Signing failed: {e.stderr.decode(errors='replace')}")
        return False

# ------------------------------------------------------------------
# 5. Encrypt the firmware (for encrypt variant)
# ------------------------------------------------------------------
def encrypt_firmware(bin_path):
    """Encrypt the firmware binary for flash encryption."""
    if not os.path.exists(bin_path):
        print(f"[secure_boot] SKIP: firmware not found: {bin_path}")
        return False

    try:
        print(f"[secure_boot] Encrypting firmware for flash encryption...")
        subprocess.run(
            [sys.executable, ESPSECURE, "encrypt_flash", "--keyfile", FLASH_KEY_PATH,
             "--offset", "0x1000", "--output", bin_path, bin_path],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"[secure_boot] Firmware encrypted successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[secure_boot] Encryption failed: {e.stderr.decode(errors='replace')}")
        return False

# ------------------------------------------------------------------
# 6. Print efuse burning instructions
# ------------------------------------------------------------------
def print_efuse_commands():
    """Print the esptool commands needed to burn efuses (one-time)."""
    key_digest = ""
    try:
        result = subprocess.run(
            ["openssl", "rsa", "-in", KEY_PATH, "-outform", "der",
             "-pubout", "-out", "/dev/stdout"],
            check=True,
            capture_output=True,
        )
        key_digest = hashlib.sha256(result.stdout).hexdigest()[:16]
    except:
        key_digest = "<run-openssl-rsa-pubout-to-get-digest>"

    print("\n" + "=" * 70)
    print("  SECURE BOOT EFUSE BURNING — READ CAREFULLY")
    print("=" * 70)
    print("  These commands are ONE-TIME. Burn efuses LAST after confirming")
    print("  firmware works in development mode.")
    print()
    print("  STEP 1 — Backup your signing key (before burning anything):")
    print(f"    cp {KEY_FILE} {KEY_FILE}.backup")
    print()
    print("  STEP 2 — Flash the signed firmware first (normal flash, no encryption):")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 erase_flash")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 write_flash 0x1000 firmware.bin")
    print()
    print("  STEP 3 — Verify the device boots correctly")
    print()
    print("  STEP 4 — Burn efuses (PERMANENT, ONE-TIME):")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
          "write_flash_encryption aes_256bit "
          f"0x0000 {FLASH_KEY}")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
          "efuse Burning keys with RSA-3071 signature...")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
          "efuse burn_key pk {KEY_FILE} RSA-3071")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
          "efuse burn_key secure_boot_signing_key {KEY_FILE} RSA-3071")
    print()
    print("  STEP 5 — Enable secure boot (reboot and flash encrypted image):")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
          "secure_boot_v1 activate")
    print()
    if SIGN_MODE:
        print("  FLASH ENCRYPTION enabled:")
        print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
              "write_flash_encryption aes_256bit "
              f"0x0000 {FLASH_KEY}")
        print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
              "efuse burn_efuse FLASH_CRYPT_CONFIG 0xf")
        print()
        print("  IMPORTANT: After enabling flash encryption, ALL subsequent")
        print("  flashes MUST use --encrypt flag:")
        print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 "
              "write_flash --encrypt 0x1000 new_firmware.bin")
    print()
    print("  Signing key digest (verify after backup):")
    print(f"    SHA256: {key_digest}...")
    print()
    print("  JTAG DISABLE (burn separately if skipping secure boot):")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 efuse burn_efuse JTAG_SEL 0")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 efuse burn_efuse DIS_PAD_JTAG 1")
    print(f"    {sys.executable} {ESPTOOL} --chip esp32 --port COM5 efuse burn_efuse DIS_JTAG 1")
    print()
    print("=" * 70)
    print()

# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
def main():
    print(f"[secure_boot] Secure Boot post-build script starting...")
    print(f"[secure_boot] ESP32 target: {BOARD_MCU}")
    print(f"[secure_boot] Flash size: {FLASH_SIZE}")
    print(f"[secure_boot] Mode: {'Flash Encryption + Secure Boot' if SIGN_MODE else 'Secure Boot Only'}")

    if not ensure_signing_key():
        print("[secure_boot] FATAL: Cannot generate signing key. Secure boot aborted.")
        return

    if SIGN_MODE and not ensure_flash_key():
        print("[secure_boot] FATAL: Cannot generate flash encryption key.")
        return

    bin_path = find_firmware_bin()
    if bin_path and sign_firmware(bin_path) and SIGN_MODE:
        encrypt_firmware(bin_path)

    print_efuse_commands()
    print("[secure_boot] Done.")

main()
