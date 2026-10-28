#!/usr/bin/env python3
"""Stage a private RAM rootfs from user-supplied, static ARM binaries."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APPLETS = (
    "sh mount mkdir cat hostname ip ifconfig sleep udhcpc insmod lsmod mdev "
    "dmesg uname ps free ping sync poweroff reboot"
).split()


def check_elf(path, readelf):
    data = path.read_bytes()
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("Expected little-endian ELF32: " + path.name)
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    if header[1] != 2 or header[2] != 40 or header[7] & 0xFF000000 != 0x05000000:
        raise ValueError("Expected executable ARM EABI5: " + path.name)
    if not header[7] & 0x400 or header[7] & 0x200:
        raise ValueError("Expected hard-float ABI: " + path.name)
    offset, width, count = header[5], header[9], header[10]
    if width < 32 or count == 0 or offset + width * count > len(data):
        raise ValueError("Malformed program headers: " + path.name)
    for i in range(count):
        kind = struct.unpack_from("<I", data, offset + i * width)[0]
        if kind in (2, 3):
            raise ValueError("This profile accepts static executables only: " + path.name)
    output = subprocess.check_output(
        [readelf, "-A", str(path.resolve())],
        text=True,
        timeout=15,
        env={**os.environ, "LC_ALL": "C"},
    )
    architectures = re.findall(r"Tag_CPU_arch:\s+(\S+)", output)
    if not architectures or any(value != "v7" for value in architectures):
        raise ValueError("ARMv7 attributes required: " + path.name)
    fp = re.findall(r"Tag_FP_arch:\s+(\S+)", output)
    if any(value not in ("VFPv2", "VFPv3", "VFPv3-D16") for value in fp):
        raise ValueError("Floating-point instructions exceed Cortex-A9: " + path.name)
    return hashlib.sha256(data).hexdigest()


def configuration(path):
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError("Configuration must be a JSON object")
    required = {"hostname", "interface", "wifi_backend", "ssid", "ssh_port"}
    if set(cfg) != required:
        raise ValueError("Configuration fields must match network.example.json")
    for name in ("hostname", "interface"):
        if not isinstance(cfg[name], str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,31}", cfg[name]
        ):
            raise ValueError("Invalid " + name)
    if len(cfg["interface"]) > 15:
        raise ValueError("Linux interface names are limited to 15 bytes")
    if cfg["wifi_backend"] not in ("wext", "nl80211", "nl80211,wext"):
        raise ValueError("Unknown Wi-Fi backend")
    if not isinstance(cfg["ssid"], str) or not 1 <= len(cfg["ssid"].encode()) <= 32:
        raise ValueError("Set a Wi-Fi SSID of 1-32 UTF-8 bytes")
    if type(cfg["ssh_port"]) is not int or not 1 <= cfg["ssh_port"] <= 65535:
        raise ValueError("Invalid SSH port")
    return cfg


def wifi_config(ssid, password):
    if not 8 <= len(password) <= 63 or any(not 32 <= ord(c) <= 126 for c in password):
        raise ValueError("Use an ASCII WPA2 passphrase of 8-63 characters")
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()
    return (
        "ctrl_interface=/run/wpa_supplicant\nnetwork={\n"
        f"    ssid={ssid.encode().hex()}\n    psk={key}\n"
        "    key_mgmt=WPA-PSK\n    proto=RSN\n}\n"
    )


def ssh_fields(blob):
    fields = []
    offset = 0
    while offset < len(blob):
        if len(blob) - offset < 4:
            raise ValueError("Truncated SSH key field")
        length = struct.unpack_from(">I", blob, offset)[0]
        offset += 4
        if length > len(blob) - offset:
            raise ValueError("Truncated SSH key field")
        fields.append(blob[offset : offset + length])
        offset += length
    return fields


def public_key(path):
    lines = [s.strip() for s in path.read_text().splitlines() if s.strip()]
    if len(lines) != 1:
        raise ValueError("Supply one SSH public key")
    parts = lines[0].split()
    if len(parts) < 2 or parts[0] not in ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"):
        raise ValueError("Unsupported SSH public key")
    blob = base64.b64decode(parts[1], validate=True)
    fields = ssh_fields(blob)
    if not fields or fields[0] != parts[0].encode():
        raise ValueError("Invalid SSH public key")
    if fields[0] == b"ssh-ed25519":
        valid = len(fields) == 2 and len(fields[1]) == 32
    elif fields[0] == b"ecdsa-sha2-nistp256":
        valid = (
            len(fields) == 3
            and fields[1] == b"nistp256"
            and len(fields[2]) == 65
            and fields[2][0] == 4
        )
    else:
        valid = len(fields) == 3 and all(
            value and not value[0] & 0x80 and int.from_bytes(value, "big") > 0
            for value in fields[1:]
        )
    if not valid:
        raise ValueError("Malformed SSH public key fields")
    return " ".join(parts[:2]) + "\n"


def host_key(path):
    data = path.read_bytes()
    if not 32 <= len(data) <= 65536:
        raise ValueError("Supply an Ed25519 Dropbear-format private host key")
    fields = ssh_fields(data)
    if len(fields) != 2 or fields[0] != b"ssh-ed25519" or len(fields[1]) != 64:
        raise ValueError("Supply an Ed25519 Dropbear-format private host key")
    return data


def archive(root):
    """Deterministic Linux newc archive; no host UID, time or absolute paths."""
    output = io.BytesIO()
    paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    devices = [
        ("dev/console", 5, 1),
        ("dev/null", 1, 3),
        ("dev/zero", 1, 5),
        ("dev/random", 1, 8),
        ("dev/urandom", 1, 9),
        ("dev/tty", 5, 0),
        ("dev/ptmx", 5, 2),
    ]
    entries = paths + devices + [None]
    for ino, path in enumerate(entries, 1):
        major = minor = 0
        if path is None:
            name, mode, data = "TRAILER!!!", 0, b""
        elif isinstance(path, tuple):
            name, major, minor = path
            mode, data = stat.S_IFCHR | 0o600, b""
        else:
            name = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError("Unsupported archive file type: " + name)
            if path.is_symlink():
                mode = stat.S_IFLNK | 0o777
            data = (
                os.readlink(path).encode()
                if path.is_symlink()
                else (path.read_bytes() if path.is_file() else b"")
            )
        encoded = name.encode() + b"\0"
        fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
        output.write(b"070701" + "".join(f"{v:08x}" for v in fields).encode())
        output.write(encoded)
        output.write(b"\0" * (-output.tell() % 4))
        output.write(data)
        output.write(b"\0" * (-output.tell() % 4))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", fileobj=compressed, mode="wb", compresslevel=9, mtime=0
    ) as stream:
        stream.write(output.getvalue())
    return compressed.getvalue()


def copy_template(source_root, stage):
    for source in sorted(source_root.rglob("*")):
        relative = source.relative_to(source_root)
        if any(
            part == ".DS_Store" or part == "__MACOSX" or part.startswith("._")
            for part in relative.parts
        ):
            continue
        if source.is_symlink():
            raise ValueError("Template symlinks need an explicit staging rule")
        target = stage / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        else:
            raise ValueError("Unsupported template file type")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "busybox", "dropbear", "supplicant", "authorized-key", "host-key"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--board-init",
        type=Path,
        help="Your audited board hook; default initializes the supported MT5931 board",
    )
    parser.add_argument(
        "--vendor-dir", type=Path, required=True, help="Locally extracted stock system directory"
    )
    parser.add_argument("--readelf", default="armv7-linux-musleabihf-readelf")
    parser.add_argument(
        "--busybox-links", type=Path, default=ROOT / "build/toolchain/busybox-1.37.0/busybox.links"
    )
    parser.add_argument(
        "--usb-debug", type=Path, help="Optional stock static adbd, local USB diagnostics only"
    )
    args = parser.parse_args()
    cfg = configuration(args.config)
    links = args.busybox_links.read_text().splitlines()
    if not links or any(
        not re.fullmatch(r"/(?:usr/)?s?bin/[A-Za-z0-9_\[\].+-]+", value) for value in links
    ):
        raise ValueError("Invalid BusyBox applet manifest")
    names = {Path(value).name for value in links}
    if not set(APPLETS + ["[", "echo", "printf"]).issubset(names):
        raise ValueError("BusyBox manifest lacks required shell commands")
    identities = {
        name: check_elf(getattr(args, name), args.readelf)
        for name in ("busybox", "dropbear", "supplicant")
    }
    if (
        args.usb_debug
        and hashlib.sha256(args.usb_debug.read_bytes()).hexdigest()
        != "62c81c5dde3cef8cca5a6de4fa5cb9bc5bd390694ebc85aa05eb5300a0d21351"
    ):
        raise ValueError("Unsupported diagnostic adbd")
    key = public_key(args.authorized_key)
    private_key = host_key(args.host_key)
    vendor = {
        "lib/modules/mt5931.ko": (
            "lib/modules/mt5931.ko",
            "3b0280c41a8b8a5f29e80aa008a119a1dab9ae372e2db521bceed60530ceb7d5",
        ),
        "etc/firmware/WIFI_RAM_CODE": (
            "etc/firmware/WIFI_RAM_CODE",
            "af345785369ee0a53efd85654810ba47463833186201fb3d3e9f9ac8195ca53c",
        ),
    }
    for relative, (_, expected) in vendor.items():
        if hashlib.sha256((args.vendor_dir / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Unsupported stock asset: " + relative)
    # Input stays out of the shell, command line and build report.
    from getpass import getpass

    wifi = wifi_config(cfg["ssid"], getpass("Wi-Fi passphrase (private build): "))
    output = ROOT / "build"
    output.mkdir(mode=0o700, exist_ok=True)
    output.chmod(0o700)
    stage = output / "rootfs"
    stage.mkdir(mode=0o700)  # Refuse to overwrite a previous private build.
    for name in (
        "bin",
        "sbin",
        "etc/dropbear",
        "root/.ssh",
        "proc",
        "sys",
        "dev",
        "run",
        "tmp",
        "usr/share/udhcpc",
    ):
        (stage / name).mkdir(parents=True, exist_ok=True)
    for directory in stage.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o755)
    copy_template(ROOT / "rootfs", stage)
    if args.usb_debug:
        shutil.copyfile(args.usb_debug, stage / "sbin/adbd")
        (stage / "sbin/adbd").chmod(0o755)
    for relative, (destination, _) in vendor.items():
        target = stage / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.vendor_dir / relative, target)
        target.chmod(0o644)
    for name, target in (
        ("busybox", "bin/busybox"),
        ("dropbear", "sbin/dropbear"),
        ("supplicant", "sbin/wpa_supplicant"),
    ):
        shutil.copyfile(getattr(args, name), stage / target)
        (stage / target).chmod(0o755)
    for name in links:
        target = stage / name.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and not target.is_symlink():
            target.symlink_to("/bin/busybox")
    if args.board_init:
        shutil.copyfile(args.board_init, stage / "etc/board-init")
        (stage / "etc/board-init").chmod(0o755)
    files = {
        "etc/hostname": cfg["hostname"] + "\n",
        "etc/network-interface": cfg["interface"] + "\n",
        "etc/wifi-backend": cfg["wifi_backend"] + "\n",
        "etc/ssh-port": str(cfg["ssh_port"]) + "\n",
        "etc/wpa_supplicant.conf": wifi,
        "etc/passwd": "root:x:0:0:root:/root:/bin/sh\n",
        "etc/group": "root:x:0:\n",
        "etc/shadow": "root:!:0:0:99999:7:::\n",
        "etc/shells": "/bin/sh\n",
        "etc/resolv.conf": "",
        "root/.ssh/authorized_keys": key,
    }
    for name, data in files.items():
        path = stage / name
        path.write_text(data)
        path.chmod(
            0o600
            if name in ("etc/shadow", "etc/wpa_supplicant.conf", "root/.ssh/authorized_keys")
            else 0o644
        )
    (stage / "etc/dropbear/host_key").write_bytes(private_key)
    (stage / "etc/dropbear/host_key").chmod(0o600)
    (stage / "etc/random.seed").write_bytes(os.urandom(512))
    (stage / "etc/random.seed").chmod(0o600)
    (stage / "var").mkdir(exist_ok=True)
    (stage / "var/run").symlink_to("/run")
    (stage / "root").chmod(0o700)
    (stage / "root/.ssh").chmod(0o700)
    stage.chmod(0o755)
    image = archive(stage)
    (output / "rootfs.cpio.gz").write_bytes(image)
    (output / "rootfs.cpio.gz").chmod(0o600)
    report = {
        "format": "gzip/newc initramfs, not a boot image",
        "bytes": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "input_sha256": identities,
        "hardware_tested": False,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Private initramfs written under build/. No device was accessed.")


if __name__ == "__main__":
    try:
        os.umask(0o077)
        main()
    except (
        ValueError,
        KeyError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        struct.error,
    ) as error:
        raise SystemExit(str(error))
