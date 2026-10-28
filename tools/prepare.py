#!/usr/bin/env python3
"""Provision owner-specific keys and assemble the supported MK802 IIIS network image."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument(
        "--system-dir", type=Path, required=True, help="Extracted stock Android system directory"
    )
    parser.add_argument("--stock-boot", type=Path, required=True)
    parser.add_argument(
        "--authorized-key", type=Path, required=True, help="Your existing SSH public key"
    )
    parser.add_argument(
        "--usb-debug", type=Path, help="Optional stock adbd for first-boot diagnosis over USB"
    )
    args = parser.parse_args()
    os.umask(0o077)
    private = ROOT / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    server = private / "host_key"
    keygen = ROOT / "build/bin/dropbearkey-host"
    if not server.exists():
        subprocess.run(
            [str(keygen), "-t", "ed25519", "-f", str(server)], check=True, stdout=subprocess.DEVNULL
        )
    result = subprocess.check_output([str(keygen), "-y", "-f", str(server)], text=True)
    public = next(line for line in result.splitlines() if line.startswith("ssh-ed25519 "))
    # A fixed host alias lets the client verify its own prepared key before first contact.
    (private / "known_hosts").write_text("mk802-prepared " + " ".join(public.split()[:2]) + "\n")
    command = [
        sys.executable,
        str(ROOT / "tools/assemble.py"),
        "--config",
        str(args.network.resolve()),
        "--busybox",
        str(ROOT / "build/bin/busybox"),
        "--dropbear",
        str(ROOT / "build/bin/dropbearmulti"),
        "--supplicant",
        str(ROOT / "build/bin/wpa_supplicant"),
        "--authorized-key",
        str(args.authorized_key.resolve()),
        "--host-key",
        str(server),
        "--vendor-dir",
        str(args.system_dir.resolve()),
    ]
    if args.usb_debug:
        command += ["--usb-debug", str(args.usb_debug.resolve())]
    subprocess.run(command, check=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/package-boot.py"),
            "--stock-boot",
            str(args.stock_boot.resolve()),
        ],
        check=True,
    )
    print("Private boot image: build/boot-linux.img")
    print("Pinned SSH identity: private/known_hosts. No device was accessed.")


if __name__ == "__main__":
    main()
