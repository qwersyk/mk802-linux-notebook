#!/usr/bin/env python3
"""Build static Cortex-A9 userspace from hash-pinned upstream source archives."""

import argparse, hashlib, json, os, re, shutil, subprocess, tarfile, urllib.request
from pathlib import Path

P = Path(__file__).resolve().parents[1]
a = argparse.ArgumentParser(description=__doc__)
a.add_argument("--jobs", default="4")
a.add_argument("--cache", type=Path, default=P / "build/sources")
a.add_argument(
    "--fetch-only", action="store_true", help="Download and verify sources, without compiling"
)
args = a.parse_args()
B = P / "build/toolchain"
B.mkdir(parents=True, exist_ok=True)
cache = args.cache.resolve()
cache.mkdir(parents=True, exist_ok=True)
lock = json.loads((P / "config/sources.json").read_text())
for name, item in lock.items():
    archive = cache / name
    if not archive.exists():
        temporary = archive.with_suffix(archive.suffix + ".part")
        request = urllib.request.Request(
            item["url"], headers={"User-Agent": "mk802-linux-notebook"}
        )
        with urllib.request.urlopen(request, timeout=60) as source, temporary.open("wb") as dest:
            shutil.copyfileobj(source, dest)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != item["sha256"]:
            temporary.unlink()
            raise SystemExit("Source hash mismatch: " + name)
        temporary.rename(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != item["sha256"]:
        raise SystemExit("Source hash mismatch: " + name)
if args.fetch_only:
    print("All seven source archives verified.")
    raise SystemExit(0)
CROSS = "armv7-linux-musleabihf-"
cc = shutil.which(CROSS + "gcc")
if not cc:
    raise SystemExit("Install an ARMv7-musl cross GCC/binutils toolchain first.")
ar = shutil.which(CROSS + "ar")
ranlib = shutil.which(CROSS + "ranlib")
prefix = B / "musl-prefix"
runtime = B / "rt-builtins/lib/linux/libclang_rt.builtins-armv7.a"
wrapper = B / "cc-a9"
flags = "-Os -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -fno-stack-protector"
headers = Path(subprocess.check_output([cc, "-print-sysroot"], text=True).strip()) / "usr/include"
if not (headers / "linux/types.h").exists():
    raise SystemExit("Cross sysroot lacks Linux headers")
env = os.environ.copy()
env.update(LC_ALL="C", SOURCE_DATE_EPOCH="1767225600", KCONFIG_NOTIMESTAMP="1")
logs = P / "build/logs"
logs.mkdir(parents=True, exist_ok=True)


def run(cmd, cwd=B, extra=None, log="build", input=None):
    e = env.copy()
    e.update(extra or {})
    print(log, flush=True)
    with (logs / (log + ".txt")).open("wb") as out:
        subprocess.run(
            list(map(str, cmd)),
            cwd=cwd,
            env=e,
            stdout=out,
            stderr=subprocess.STDOUT,
            input=input,
            check=True,
        )


def extract(name):
    with tarfile.open(cache / name) as archive:
        archive.extractall(B, filter="data")


for name in [
    "musl-1.2.5.tar.gz",
    "busybox-1.37.0.tar.bz2",
    "dropbear-2024.86.tar.gz",
    "wpa_supplicant-2.11.tar.gz",
    "compiler-rt-20.1.8.src.tar.xz",
    "cmake-20.1.8.src.tar.xz",
]:
    extract(name)
m = B / "musl-1.2.5"
cfg = {"CC": cc, "AR": ar, "RANLIB": ranlib, "CFLAGS": flags}
run(
    [
        "./configure",
        "--prefix=" + str(prefix),
        "--syslibdir=" + str(prefix / "lib"),
        "--disable-shared",
        "--enable-wrapper=gcc",
    ],
    m,
    cfg,
    "musl-configure",
)
run(["make", "-j" + args.jobs], m, log="musl-build")
run(["make", "install"], m, log="musl-install")
if not (B / "cmake").exists():
    (B / "cmake").symlink_to("cmake-20.1.8.src")
run(
    [
        "cmake",
        "-S",
        B / "compiler-rt-20.1.8.src/lib/builtins",
        "-B",
        B / "rt-builtins",
        "-DCMAKE_SYSTEM_NAME=Linux",
        "-DCMAKE_C_COMPILER=" + cc,
        "-DCMAKE_ASM_COMPILER=" + cc,
        "-DCMAKE_C_COMPILER_TARGET=armv7-linux-musleabihf",
        "-DCMAKE_AR=" + ar,
        "-DCMAKE_RANLIB=" + ranlib,
        "-DCMAKE_C_FLAGS=" + flags,
        "-DCMAKE_ASM_FLAGS=" + flags,
        "-DLLVM_RUNTIMES_BUILD=ON",
        "-DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON",
        "-DCOMPILER_RT_BAREMETAL_BUILD=ON",
        "-DCOMPILER_RT_BUILD_CRT=OFF",
    ],
    log="runtime-configure",
)
run(["cmake", "--build", B / "rt-builtins", "-j" + args.jobs], log="runtime-build")
s = (prefix / "lib/musl-gcc.specs").read_text()
s = re.sub(r"\*libgcc:\n[^\n]*", "*libgcc:\n" + str(runtime), s)
s = re.sub(
    r"\*startfile:\n[^\n]*",
    "*startfile:\n" + str(prefix / "lib/crt1.o") + " " + str(prefix / "lib/crti.o"),
    s,
)
s = re.sub(r"\*endfile:\n[^\n]*", "*endfile:\n" + str(prefix / "lib/crtn.o"), s)
(B / "cortex-a9.specs").write_text(s)
wrapper.write_text(
    "#!/bin/sh\nexec "
    + cc
    + ' -specs="'
    + str(B / "cortex-a9.specs")
    + '" '
    + flags
    + ' -fno-unwind-tables -fno-asynchronous-unwind-tables "$@"\n'
)
wrapper.chmod(0o755)
bb = B / "busybox-1.37.0"
shutil.copyfile(P / "config/busybox.config", bb / ".config")
run(["make", "oldconfig"], bb, log="busybox-config", input=b"\n" * 10000)
run(
    [
        "make",
        "-j" + args.jobs,
        "ARCH=arm",
        "CROSS_COMPILE=" + CROSS,
        "CC=" + str(wrapper),
        "HOSTCC=clang",
        "EXTRA_CFLAGS=-Os -idirafter " + str(headers),
        "SKIP_STRIP=y",
    ],
    bb,
    log="busybox-build",
)
run(["make", "busybox.links"], bb, log="busybox-links")
db = B / "dropbear-DROPBEAR_2024.86"
run(
    [
        "./configure",
        "--host=arm-linux-musleabihf",
        "--disable-zlib",
        "--disable-utmp",
        "--disable-utmpx",
        "--disable-wtmp",
        "--disable-wtmpx",
        "--disable-lastlog",
        "--disable-syslog",
        "--enable-static",
    ],
    db,
    {"CC": str(wrapper), "AR": ar, "RANLIB": ranlib, "CFLAGS": "-Os", "LDFLAGS": "-static"},
    "dropbear-configure",
)
run(
    ["make", "-j" + args.jobs, "PROGRAMS=dropbear dropbearkey", "MULTI=1"],
    db,
    log="dropbear-build",
)
wp = B / "wpa_supplicant-2.11/wpa_supplicant"
shutil.copyfile(P / "config/wpa_supplicant.config", wp / ".config")
run(
    ["make", "-j" + args.jobs, "CC=" + str(wrapper)],
    wp,
    {"CFLAGS": "-Os -idirafter " + str(headers), "LDFLAGS": "-static"},
    "wpa-build",
)

extract("libnl-3.11.0.tar.gz")
nl = B / "libnl-3.11.0"
nlprefix = B / "libnl-prefix"
socket_c = nl / "lib/socket.c"
text = socket_c.read_text()
socket_c.write_text(
    text.replace(
        '#include "nl-aux-core/nl-core.h"', '#include "nl-aux-core/nl-core.h"\n#include <time.h>', 1
    )
)
netenv = {
    "CC": str(wrapper),
    "AR": ar,
    "RANLIB": ranlib,
    "CFLAGS": "-Os -idirafter " + str(headers),
    "LDFLAGS": "-static",
}
run(
    [
        "./configure",
        "--host=arm-linux-musleabihf",
        "--prefix=" + str(nlprefix),
        "--disable-shared",
        "--enable-static",
        "--disable-cli",
        "--disable-pthreads",
    ],
    nl,
    netenv,
    "libnl-configure",
)
run(["make", "-j" + args.jobs, "lib/libnl-3.la", "lib/libnl-genl-3.la"], nl, netenv, "libnl-build")
(nlprefix / "lib").mkdir(parents=True, exist_ok=True)
shutil.copytree(nl / "include/netlink", nlprefix / "include/libnl3/netlink", dirs_exist_ok=True)
for name in ["libnl-3.a", "libnl-genl-3.a"]:
    shutil.copyfile(nl / "lib/.libs" / name, nlprefix / "lib" / name)
wp = B / "wpa_supplicant-2.11/wpa_supplicant"
(wp / ".config").write_text(
    (P / "config/wpa_supplicant.config").read_text()
    + "\nCONFIG_DRIVER_NL80211=y\nCONFIG_LIBNL32=y\nLIBNL_INC="
    + str(nlprefix / "include/libnl3")
    + "\nLIBS += -L"
    + str(nlprefix / "lib")
    + " -lpthread\n"
)
run(["make", "clean"], wp, log="wpa-clean")
run(["make", "-j" + args.jobs, "CC=" + str(wrapper)], wp, netenv, "wpa-nl80211-build")
# Build a native key generator separately; private host keys are generated per owner.
with tarfile.open(cache / "dropbear-2024.86.tar.gz") as archive:
    native = B / "native"
    native.mkdir(exist_ok=True)
    archive.extractall(native, filter="data")
nd = native / "dropbear-DROPBEAR_2024.86"
run(
    ["./configure", "--disable-zlib"],
    nd,
    {"CC": shutil.which("cc"), "CFLAGS": "-O2", "LDFLAGS": ""},
    "keygen-configure",
)
run(["make", "-j" + args.jobs, "PROGRAMS=dropbearkey"], nd, log="keygen-build")
out = P / "build/bin"
out.mkdir(parents=True, exist_ok=True)
for name, source in {
    "busybox": bb / "busybox",
    "dropbearmulti": db / "dropbearmulti",
    "wpa_supplicant": wp / "wpa_supplicant",
    "dropbearkey-host": nd / "dropbearkey",
}.items():
    shutil.copyfile(source, out / name)
    (out / name).chmod(0o755)
    if name != "dropbearkey-host":
        subprocess.run([CROSS + "strip", "--strip-unneeded", str(out / name)], check=True)
print("Built userspace under build/bin. No device accessed.")
