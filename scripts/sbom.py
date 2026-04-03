# sbom.py — Software Bill of Materials generation for STASYS ESP32
#
# Usage (in platformio.ini):
#   extra_scripts = pre:build_timestamp.py:post:scripts/sbom.py
#
# Generates an SPDX 2.3 JSON SBOM after every build, including:
#   - ESP32 framework components and versions
#   - Arduino core version
#   - All PlatformIO library dependencies
#   - Build flags and compiler info
#   - Firmware metadata (version, build timestamp)
#
# Output: .pio/build/<env>/sbom.json
#
# SPDX 2.3 format fields:
#   spdxVersion, SPDXID, name, documentNamespace,
#   creationInfo, packages[], files[]

import json
import os
import hashlib

Import("env")

PROJECT_DIR  = os.path.dirname(env["PROJECT_DIR"])
BUILD_DIR    = env["BUILD_DIR"]
FIRMWARE_BIN = os.path.join(BUILD_DIR, "firmware.bin")
SBOM_OUT     = os.path.join(BUILD_DIR, "sbom.json")

# ------------------------------------------------------------------
# SHA-256 of firmware binary
# ------------------------------------------------------------------
def _sha256(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ------------------------------------------------------------------
# Gather framework / package info from PlatformIO env
# ------------------------------------------------------------------
def get_framework_info():
    framework = env.get("FRAMEWORK", "")
    platform = env.get("PLATFORM", "")
    board = env.get("BOARD", "")

    packages = []
    for pkg, version in env.get("PIOPACKAGES", {}).items():
        packages.append({
            "name": pkg,
            "version": version,
            "supplier": "Espressif",
            "type": "framework",
        })

    # Compiler info
    cc = env.get("CC", "xtensa-esp32-elf-gcc")
    cflags = env.get("CFLAGS", "")
    ldscript = env.get(" LDSCRIPT", "")

    return {
        "framework": framework,
        "platform": platform,
        "board": board,
        "compiler": cc,
        "packages": packages,
        "build_flags": env.get("BUILD_FLAGS", []),
    }


# ------------------------------------------------------------------
# Build SBOM document
# ------------------------------------------------------------------
def build_sbom():
    print(f"[sbom] Generating SBOM...")

    fw_sha256  = _sha256(FIRMWARE_BIN)
    fw_version = env.get("BUILD_VERSION_MAJOR", "1")
    fw_minor   = env.get("BUILD_VERSION_MINOR", "0")
    fw_patch   = env.get("BUILD_VERSION_PATCH", "1")
    build_ts   = env.get("BUILD_TIMESTAMP", "0")
    fcc_id     = env.get("FIRMWARE_FCC_ID", "UNKNOWN")
    ic_id      = env.get("FIRMWARE_IC_ID", "UNKNOWN")
    ce_marked  = env.get("FIRMWARE_CE_MARKED", "0")

    fw_meta = {
        "name": "STASYS-ESP32-Firmware",
        "version": f"{fw_version}.{fw_minor}.{fw_patch}",
        "build_timestamp": build_ts,
        "fcc_id": fcc_id,
        "ic_id": ic_id,
        "ce_marked": bool(ce_marked == "1"),
        "sha256": fw_sha256 or "N/A",
    }

    fw_info = get_framework_info()

    # SPDX document
    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "STASYS ESP32 Firmware",
        "documentNamespace": "https://stasys.example.com/sbom/firmware",
        "creationInfo": {
            "created": env["BUILD_TIME"] if "BUILD_TIME" in env else "unknown",
            "creators": [
                "Tool: PlatformIO",
                f"Builder: {fw_info['compiler']}",
            ],
            "comment": "Auto-generated SBOM. Replace FCC/IC IDs with actual values.",
        },
        "packages": [],
        "files": [],
    }

    # Root firmware package
    root_pkg = {
        "SPDXID": "SPDXRef-Firmware",
        "name": fw_meta["name"],
        "versionInfo": fw_meta["version"],
        "supplier": "Organization: STASYS",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": True,
        "verificationCodeValue": fw_sha256 or "NONE",
        "hasFiles": ["SPDXRef-File-firmware.bin"],
        "comment": (
            f"STASYS shooting trainer firmware. "
            f"FCC ID: {fcc_id}, IC: {ic_id}, CE: {ce_marked}. "
            f"Built: {build_ts}."
        ),
        "externalRefs": [
            {"referenceCategory": "BUILD-METADATA",
             "referenceType": "build-configuration",
             "referenceLocator": f"build_flags:{fw_info['build_flags']}"},
        ],
    }
    doc["packages"].append(root_pkg)

    # Firmware binary file entry
    if fw_sha256:
        doc["files"].append({
            "SPDXID": "SPDXRef-File-firmware.bin",
            "fileName": "firmware.bin",
            "checksum": [{"algorithm": "SHA256", "value": fw_sha256}],
        })

    # Framework packages
    for pkg in fw_info.get("packages", []):
        pkg_id = f"SPDXRef-Pkg-{pkg['name'].replace('.', '-')}"
        doc["packages"].append({
            "SPDXID": pkg_id,
            "name": pkg["name"],
            "versionInfo": pkg["version"],
            "supplier": pkg.get("supplier", "Espressif"),
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
        })

    # Write output
    with open(SBOM_OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    print(f"[sbom] Written: {SBOM_OUT}")
    print(f"[sbom]   firmware: v{fw_meta['version']} SHA256={fw_sha256[:16]}...")
    print(f"[sbom]   FCC ID: {fcc_id}  IC: {ic_id}  CE: {ce_marked}")
    print(f"[sbom]   packages: {len(fw_info.get('packages', []))}")


build_sbom()