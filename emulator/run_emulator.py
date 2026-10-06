"""Start ELM327-emulator with the Deepal S05 scenario.

    python emulator/run_emulator.py              # TCP port 35000
    python emulator/run_emulator.py --pty        # Linux/macOS pseudo-terminal

Then, in another terminal:

    python -m deepal_s05 --port socket://localhost:35000 check
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from elm import Elm  # noqa: E402
from deepal_s05_scenario import ObdMessage  # noqa: E402


def start(net_port=None, net_interface="localhost"):
    emulator = Elm(net_port=net_port, net_interface=net_interface)
    emulator.ObdMessage.update(ObdMessage)
    emulator.set_sorted_obd_msg("deepal_s05")
    return emulator


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--port", type=int, default=35000, help="TCP port")
    p.add_argument("--interface", default="localhost",
                   help="ใช้ 0.0.0.0 ถ้าจะให้มือถือในวง WiFi เดียวกันต่อได้")
    p.add_argument("--pty", action="store_true",
                   help="ใช้ pseudo-terminal แทน TCP (Linux/macOS)")
    args = p.parse_args()

    emulator = start(None if args.pty else args.port, args.interface)
    with emulator:
        if args.pty:
            while emulator.get_pty() is None:
                time.sleep(0.1)
            print("พร้อมแล้ว: --port %s" % emulator.get_pty())
        else:
            print("พร้อมแล้ว: --port socket://%s:%d" % (
                "localhost" if args.interface == "0.0.0.0" else
                args.interface, args.port))
        print("กด Ctrl+C เพื่อหยุด")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
