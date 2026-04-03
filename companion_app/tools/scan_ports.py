"""List all available COM ports with name, description, and hardware ID.

Flag ports whose description contains 'STASYS' or 'Bluetooth'.
Run standalone: python companion_app/tools/scan_ports.py
"""

import sys
import serial.tools.list_ports

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def ansi(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}"


def scan_ports() -> None:
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No COM ports found.")
        sys.exit(0)

    stasys_ports = []
    other_ports = []

    for port in ports:
        name = port.device
        desc = port.description or ""
        hwid = port.hwid or ""
        # LOCALMFG&0002 indicates a paired Bluetooth SPP device (STASYS).
        # LOCALMFG&0000 is a generic/unpaired BT port — skip it.
        is_paired_bt = "LOCALMFG&0002" in hwid.upper()
        is_generic_bt = "LOCALMFG&0000" in hwid.upper() and "LOCALMFG&0002" not in hwid.upper()

        if is_paired_bt:
            entry = f"  {name}  [{ansi(_GREEN, 'PAIRED BT SPP')}]  desc={desc!r}  hwid={hwid!r}"
            stasys_ports.append(entry)
        elif is_generic_bt:
            entry = f"  {name}  [{ansi(_YELLOW, 'GENERIC BT (skip)')}]  desc={desc!r}  hwid={hwid!r}"
            other_ports.append(entry)
        else:
            entry = f"  {name}  desc={desc!r}  hwid={hwid!r}"
            other_ports.append(entry)

    print(f"Total ports found: {len(ports)}")
    print()

    if stasys_ports:
        print("Paired Bluetooth SPP ports (likely STASYS):")
        for e in stasys_ports:
            print(e)
        print()

    print("Other ports:")
    for e in other_ports:
        print(e)


if __name__ == "__main__":
    scan_ports()
