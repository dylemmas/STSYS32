"""STASYS companion app entry point.

Usage:
    python main.py                     Launch GUI (PyQt6 desktop app)
    python main.py --console           Interactive device console
    python main.py --monitor [--port]  Live session monitor
    python main.py --scan              List available COM ports
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_TOOLS_DIR = Path(__file__).parent / "tools"


def main() -> None:
    parser = argparse.ArgumentParser(description="STASYS ESP32 companion app")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--console", action="store_true",
                       help="Interactive device console")
    group.add_argument("--monitor", action="store_true",
                       help="Live session monitor")
    group.add_argument("--scan", action="store_true",
                       help="List available COM ports")

    parser.add_argument("--port", type=str, default=None,
                        help="COM port (auto-discovers if omitted)")
    parser.add_argument("--auto-start", action="store_true",
                        help="Auto-start recording session (monitor mode)")

    args = parser.parse_args()

    if args.console:
        sys.path.insert(0, str(Path(__file__).parent))
        from tools.console import main as console_main
        sys.argv = ["console.py"]
        if args.port:
            sys.argv.extend(["--port", args.port])
        console_main()

    elif args.monitor:
        sys.path.insert(0, str(Path(__file__).parent))
        from tools.monitor import main as monitor_main
        sys.argv = ["monitor.py"]
        if args.port:
            sys.argv.extend(["--port", args.port])
        if args.auto_start:
            sys.argv.append("--auto-start")
        monitor_main()

    elif args.scan:
        sys.path.insert(0, str(Path(__file__).parent))
        from tools.scan_ports import scan_ports
        scan_ports()

    else:
        # Launch GUI by default
        from PyQt6.QtWidgets import QApplication
        from gui.main_window import MainWindow
        from gui.theme import DARK_QSS

        app = QApplication(sys.argv)
        app.setStyleSheet(DARK_QSS)
        win = MainWindow()
        win.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
