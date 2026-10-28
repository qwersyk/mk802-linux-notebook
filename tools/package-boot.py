#!/usr/bin/env python3
"""Package a private native boot image from an exact, owner-supplied stock backup."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
STOCK_BOOT = "ab52ee82f9fed2686dd11c5be094f720367538faaf447fd7f17df5fe252d3cba"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def image_id(image):
    kernel, _, ramdisk, _, second, _, _, page, _, _ = struct.unpack_from("<10I", image, 8)
    h = hashlib.sha1()
    offset = page
    for size in (kernel, ramdisk, second):
        h.update(image[offset : offset + size])
        h.update(struct.pack("<I", size))
        offset += (size + page - 1) // page * page
    h.update(image[32:576])
    return h.digest() + bytes(12)


def package(original, rootfs):
    if digest(original) != STOCK_BOOT:
        raise ValueError("Unsupported stock boot.img; do not bypass this compatibility gate")
    if original[:8] != b"ANDROID!" or image_id(original) != original[576:608]:
        raise ValueError("Stock Rockchip boot ID failed verification")
    ks, ka, rs, ra, second, _, tags, page, dt, unused = struct.unpack_from("<10I", original, 8)
    if (ka, ra, page, second, dt, unused) != (0x60408000, 0x62000000, 16384, 0, 0, 0):
        raise ValueError("Unexpected stock memory layout")
    lock = json.loads((ROOT / "config/kernel-patches.json").read_text())
    kernel = bytearray(original[page : page + ks])
    if digest(kernel) != lock["original_sha256"]:
        raise ValueError("Unexpected stock kernel")
    for patch in lock["patches"]:
        at = patch["offset"]
        before, after = bytes.fromhex(patch["before"]), bytes.fromhex(patch["after"])
        if len(before) != len(after) or kernel[at : at + len(before)] != before:
            raise ValueError("Kernel patch precondition failed")
        kernel[at : at + len(after)] = after
    if digest(kernel) != lock["patched_sha256"]:
        raise ValueError("Patched kernel failed verification")
    cpio = gzip.decompress(rootfs)
    if not cpio.startswith(b"070701") or len(rootfs) > rs:
        raise ValueError("Initramfs format or stock ramdisk capacity exceeded")
    image = bytearray(original)
    image[page : page + ks] = kernel
    start = page + (ks + page - 1) // page * page
    size = (rs + page - 1) // page * page
    image[start : start + size] = rootfs + bytes(size - len(rootfs))
    image[576:608] = image_id(image)
    if image_id(image) != image[576:608] or gzip.decompress(image[start : start + rs]) != cpio:
        raise ValueError("Packaged boot verification failed")
    return bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-boot", type=Path, required=True)
    parser.add_argument("--rootfs", type=Path, default=ROOT / "build/rootfs.cpio.gz")
    args = parser.parse_args()
    image = package(args.stock_boot.read_bytes(), args.rootfs.read_bytes())
    output = ROOT / "build/boot-linux.img"
    with output.open("xb") as dest:
        dest.write(image)
    output.chmod(0o600)
    report = {
        "sha256": digest(image),
        "bytes": len(image),
        "stock_sha256": STOCK_BOOT,
        "hardware_tested_this_build": False,
        "nand_written": False,
        "kernel": "exact historical standalone kernel, guarded NAND callbacks, fixed clock, one CPU",
    }
    (ROOT / "build/boot-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
