#!/usr/bin/env python3
"""Extract two hash-checked Wi-Fi assets from a stock ext filesystem, read-only."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "lib/modules/mt5931.ko": "3b0280c41a8b8a5f29e80aa008a119a1dab9ae372e2db521bceed60530ceb7d5",
    "etc/firmware/WIFI_RAM_CODE": "af345785369ee0a53efd85654810ba47463833186201fb3d3e9f9ac8195ca53c",
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--system-image", type=Path, required=True)
parser.add_argument("--debugfs", default="debugfs")
parser.add_argument(
    "--usb-debug", action="store_true", help="Also extract the compatible stock diagnostic adbd"
)
args = parser.parse_args()
if not shutil.which(args.debugfs):
    raise SystemExit("debugfs required; install e2fsprogs and pass its executable path")
if args.usb_debug:
    ASSETS["bin/adbd"] = "62c81c5dde3cef8cca5a6de4fa5cb9bc5bd390694ebc85aa05eb5300a0d21351"
image = args.system_image.resolve()
with image.open("rb") as source:
    source.seek(1080)
    if source.read(2) != b"\x53\xef":
        raise SystemExit(
            "Expected a raw ext filesystem image; sparse Android images must be unsparsed first"
        )
out = ROOT / "private/system"
out.mkdir(mode=0o700, parents=True, exist_ok=False)
for relative, expected in ASSETS.items():
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    # No -w: debugfs opens the input filesystem read-only. Output names are fixed.
    subprocess.run(
        [args.debugfs, "-R", "dump /" + relative + " " + target.name, str(image)],
        cwd=target.parent,
        check=True,
        capture_output=True,
        timeout=30,
    )
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        target.unlink()
        raise SystemExit("Unsupported stock asset: " + relative)
    target.chmod(0o600)
print("Compatible assets extracted to private/system. Input image unchanged.")
