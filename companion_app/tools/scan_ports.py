"""List all available COM ports with name, description, and hardware ID.

Run standalone: python companion_app/tools/scan_ports.py
"""

import serial.tools.list_ports

# Colour output constants
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def scan_ports() -> None:
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No COM ports found.")
        return

    stasys_outgoing = []
    stasys_incoming = []
    other_paired = []
    generic_bt = []
    other = []

    for port in ports:
        name = port.device
        desc = port.description or ""
        hwid = port.hwid or ""
        hwid_upper = hwid.upper()

        is_paired_bt = "LOCALMFG&0002" in hwid_upper
        is_generic_bt = "LOCALMFG&0000" in hwid_upper and "LOCALMFG&0002" not in hwid_upper

        if is_paired_bt:
            desc_upper = desc.upper()
            name_upper = (name or "").upper()
            if "ESP32SPP" in name_upper or "ESP32SPP" in desc_upper:
                label = "OUTGOING"
                stasys_outgoing.append((name, desc, hwid, label))
            elif "OUTGOING" in name_upper or "OUTGOING" in desc_upper:
                label = "OUTGOING"
                stasys_outgoing.append((name, desc, hwid, label))
            elif "INCOMING" in name_upper or "INCOMING" in desc_upper:
                label = "INCOMING"
                stasys_incoming.append((name, desc, hwid, label))
            else:
                # Single paired BT SPP port with no keyword — treat as Outgoing
                label = "OUTGOING (single)"
                stasys_outgoing.append((name, desc, hwid, label))
        elif is_generic_bt:
            generic_bt.append((name, desc, hwid))
        else:
            other.append((name, desc, hwid))

    print(f"Total ports: {len(ports)}\n")

    def print_port(name: str, desc: str, hwid: str, label: str) -> None:
        label_str = f"[{label}]"
        if "OUTGOING" in label:
            colour = _GREEN
        elif "INCOMING" in label:
            colour = _YELLOW
        else:
            colour = ""
        label_str = f"{colour}{label_str}{_RESET}"
        print(f"  {name}  {label_str}  desc={desc!r}")

    if stasys_outgoing:
        print("STASYS Outgoing (use this to connect):")
        for n, d, h, l in stasys_outgoing:
            print_port(n, d, h, l)
        print()

    if stasys_incoming:
        print("STASYS Incoming (fallback only):")
        for n, d, h, l in stasys_incoming:
            print_port(n, d, h, l)
        print()

    if other_paired:
        print("Other paired BT SPP:")
        for n, d, h, l in other_paired:
            print_port(n, d, h, l)
        print()

    if generic_bt:
        print("Generic Bluetooth (skip):")
        for n, d, h in generic_bt:
            print(f"  {n}  [{_YELLOW}GENERIC BT (skip){_RESET}]  desc={d!r}")
        print()

    if other:
        print("Other ports:")
        for n, d, h in other:
            print(f"  {n}  desc={d!r}")


if __name__ == "__main__":
    scan_ports()
