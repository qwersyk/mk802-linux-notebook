#!/usr/bin/env python3
"""Build a fixed-image macOS RAM uploader from owner-supplied, compatible backups."""

import argparse
import hashlib
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def rc4(data):
    # Rockchip's public image-format scrambling key, not an owner credential.
    key = bytes.fromhex("7c4e0304550509072d2c7b38170d1711")
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % 16]) & 255
        state[i], state[j] = state[j], state[i]
    i = j = 0
    out = bytearray()
    for value in data:
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        out.append(value ^ state[(state[i] + state[j]) & 255])
    return bytes(out)


def extract_loader(raw):
    loaders = []
    for block in range(5):

        def sector(index):
            start = (block * 8192 + index) * 528
            value = raw[start : start + 512]
            if len(value) != 512:
                raise ValueError("Incomplete 528-byte IDB backup")
            return value

        header = rc4(sector(0))
        if header[:4] != bytes.fromhex("55aaf00f") or struct.unpack_from("<I", header, 8)[0]:
            raise ValueError("Unsupported IDB format")
        start = struct.unpack_from("<H", header, 12)[0]
        ddr, total = struct.unpack_from("<HH", header, 506)
        if not 0 < ddr < total <= 1024:
            raise ValueError("Invalid IDB sector count")
        payload = b"".join(rc4(sector(i)) for i in range(start, start + total))
        loaders.append(payload[ddr * 512 :])
    if not all(value == loaders[0] for value in loaders):
        raise ValueError("IDB loader copies differ")
    if (
        hashlib.sha256(loaders[0]).hexdigest()
        != "050c59e8e57da3c2fcee17615e5e73308e57585f9e3474f4b5bc677afc236b6d"
    ):
        raise ValueError("Unsupported Loader; no uploader will be built")
    return loaders[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--idb", type=Path, required=True, help="Raw IDB backup with 528-byte sectors"
    )
    parser.add_argument(
        "--flash-image",
        type=Path,
        required=True,
        help="Your logical NAND backup, for LBA0 verification",
    )
    parser.add_argument("--rootfs", type=Path, default=ROOT / "build/rootfs.cpio.gz")
    args = parser.parse_args()
    loader = extract_loader(args.idb.read_bytes())
    with args.flash_image.open("rb") as source:
        lba0 = source.read(512)
    if len(lba0) != 512:
        raise ValueError("Incomplete logical NAND backup")
    out = ROOT / "build/ram"
    out.mkdir(mode=0o700, exist_ok=True)
    for name, file in [("marker", "marker.S"), ("handoff", "handoff.S")]:
        subprocess.run(
            [
                "armv7-linux-musleabihf-as",
                "-mcpu=cortex-a9",
                "-o",
                str(out / (name + ".o")),
                str(ROOT / "ram" / file),
            ],
            check=True,
        )
    subprocess.run(
        [
            "armv7-linux-musleabihf-ld",
            "-T",
            str(ROOT / "ram/handoff.ld"),
            "-o",
            str(out / "handoff.elf"),
            str(out / "marker.o"),
            str(out / "handoff.o"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "armv7-linux-musleabihf-objcopy",
            "-O",
            "binary",
            str(out / "handoff.elf"),
            str(out / "handoff.bin"),
        ],
        check=True,
    )
    boot = (ROOT / "build/boot-linux.img").read_bytes()
    ks, _, rs, _ = struct.unpack_from("<4I", boot, 8)
    page = struct.unpack_from("<I", boot, 36)[0]
    kernel = boot[page : page + ks]
    rootfs = args.rootfs.read_bytes()

    def tag(kind, data):
        data += bytes(-len(data) % 4)
        return struct.pack("<II", 2 + len(data) // 4, kind) + data

    cmd = (
        "console=ttyFIQ0 loglevel=6 maxcpus=1 rdinit=/init init=/init root=/dev/ram0 rw panic=0 "
        "initrd=0x62000000,0x%x mem=260M@0x60000000" % len(rootfs)
    )
    atags = tag(0x54410001, struct.pack("<III", 1, 4096, 0))
    atags += tag(0x54410002, struct.pack("<II", 260 * 1024 * 1024, 0x60000000))
    atags += tag(0x54420005, struct.pack("<II", 0x62000000, len(rootfs)))
    atags += tag(0x54410009, cmd.encode() + b"\0") + bytes(8)
    handoff = (out / "handoff.bin").read_bytes()
    if (
        len(handoff) != 4224
        or len(atags) > 512
        or len(kernel) + 0x60408000 + 512 >= 0x61F00000
        or len(rootfs) + 0x62000000 + 512 >= 0x63000000
    ):
        raise ValueError("RAM layout rejected")
    base = 0x6007F818

    def region(address, size):
        return loader[address - base : address - base + size]

    overlay = bytearray(region(0x60081E00, 256))
    offset = base + 0x2620 - 0x60081E00
    if struct.unpack_from("<I", overlay, offset)[0] != 0xE3A01000:
        raise ValueError("Loader overlay precondition failed")
    branch = 0x61F00200 - (base + 0x2620 + 8)
    struct.pack_into("<I", overlay, offset, 0xEA000000 | ((branch // 4) & 0xFFFFFF))
    # Recreate the loader's identity-mapped section table from its public layout.
    table = b"".join(
        struct.pack("<I", (i << 20) | (0xC0A if i == 0 or 1536 <= i < 2545 else 0xC12))
        for i in range(4096)
    )
    arrays = {
        "native_csw": region(0x60081C00, 512),
        "native_patch_and_tail": region(0x60081E00, 768),
        "native_usb": region(0x60082100, 2304),
        "proof_overlay": bytes(overlay),
        "expected_table": table,
        "expected_lba0": lba0,
        "expected_chip": bytes.fromhex("41303033313130323131313131303156"),
    }

    def array(data):
        return "{" + ",".join(hex(value) for value in data) + "}"

    header = "\n".join(
        "static const unsigned char %s[%d]=%s;" % (name, len(data), array(data))
        for name, data in arrays.items()
    )
    rows = []
    for name, address, data in [
        ("kernel", 0x60408000, kernel),
        ("rootfs", 0x62000000, rootfs),
        ("atags", 0x60000800, atags),
        ("handoff", 0x61F00000, handoff),
    ]:
        (out / (name + ".bin")).write_bytes(data)
        rows.append(
            '{"%s",0x%x,%d,%s}' % (name, address, len(data), array(hashlib.sha256(data).digest()))
        )
    (out / "linux-images.h").write_text(
        header + "\nstatic const struct image_def definitions[4]={" + ",".join(rows) + "};\n"
    )
    flags = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "libusb-1.0"], text=True
    ).split()
    subprocess.run(
        [
            "cc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DHANDOFF_CHECKPOINT",
            "-I" + str(out),
            str(ROOT / "ram/usb-boot.c"),
            *flags,
            "-o",
            str(out / "usb-boot"),
        ],
        check=True,
    )
    print("Built build/ram/usb-boot. No USB device was opened. Keep all build files private.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
