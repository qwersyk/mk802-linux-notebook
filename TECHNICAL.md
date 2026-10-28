# Technical notes

These notes describe the MK802 IIIS used in the experiment and the published
Wi-Fi/SSH build. The later display, media and remote work is recorded separately
from the components included here.

## Wi-Fi and SSH verification, 29 October 2026

The Wi-Fi/SSH build was compiled on Apple Silicon from the archives pinned in
`config/sources.json`, then booted through USB Loader on the test unit.

Verified on hardware:

- Standalone Linux with BusyBox as PID1.
- Automatic WPA2 association and DHCP.
- Public-key SSH with a pre-provisioned host identity, remote commands and a PTY.
- Startup with and without the optional USB ADB bridge.

The build without adbd produced a 1,076,155-byte compressed initramfs. Tests ran
from RAM without writing NAND. The larger system described in the experiment
booted from internal NAND; permanent installation of this minimal build was not
tested. Compatibility is limited to the board and firmware below. Long-term
stability and full NAND recovery remain unverified.

## Hardware and starting point

| Item | Observed on this device |
| --- | --- |
| Model | Rikomagic MK802 IIIS |
| SoC | Rockchip RK3066, dual Cortex-A9, ARMv7 |
| Memory | 1 GiB RAM, 4 GiB NAND |
| Original userspace | Android 4.2.2, June 2013 build |
| Kernel retained | Vendor Linux 3.0.36+ |
| Wi-Fi | MediaTek MT5931, identified during live bring-up |
| Host and access | MacBook, USB Loader and HDMI; no serial console or SD card |

The stock filesystem contained several Wi-Fi drivers and module versions.
The profile uses the identified MT5931 and matching 3.0.36+ module; the case
label alone does not identify the wireless hardware.

## Backup and recovery limits

The 4,294,967,296-byte logical NAND backup was read twice and compared. SHA-256,
partition metadata and image checksums were verified before offline analysis of
the kernel, boot, recovery and system partitions.

That was a logical image, not a complete physical NAND dump with all spare/OOB
data. Reading it twice did not prove it could be restored. Vendor FTL, ECC,
calibration and boot metadata complicate restoration.

## USB protocol

The stock loader was not a generic modern U-Boot console. In its RAM-read path
it added SDRAM base `0x60000000` to the address supplied by the host. Passing an
already absolute address therefore targeted the wrong location. An older
[rkflashtool revision](https://github.com/linux-rockchip/rkflashtool/commit/6eb0e22fdd000798a428cce3313821a23b729db0)
preserved the needed subtraction. Corrected reads matched saved loader code.

The Loader idle shutdown was measured repeatedly at about 202 seconds. A
bounded read-only keepalive held a session beyond that interval. This identifies
a protocol timeout; supply stability under Linux load requires separate testing.

The Loader execute command did not perform the CPU and cache setup required
for a Linux handoff. RAM transport and execution were established separately, with small
markers before a full kernel. RAM writes also cleared an additional 512 bytes
after the payload on this loader, making instruction-sized patches especially
dangerous. These observations are not a portable USB boot recipe.

## Kernel handoff and init

RAM placement had to preserve the live loader and its diagnostic state. CPU
cache/MMU state, board setup, clocks, interrupts and memory initialization all
mattered. Early markers helped distinguish a stopped kernel from broken
diagnostics. A vendor clock-initialization path interfered with the RAM route;
avoiding a CPU frequency transition was another step toward the first full boot.

The root filesystem used BusyBox, musl, Dropbear and wpa_supplicant. Cross builds
targeted Cortex-A9/ARMv7 hard-float with suitable VFP/NEON instructions. One
toolchain's startup objects assumed VFPv4, which this CPU does not implement;
the build therefore checks CPU and floating-point attributes as well as ELF
architecture and linkage.

Android was temporarily useful as a source of live hardware information and an
intermediate environment for SSH experiments. Independent Linux boot came
later. The root filesystem remained RAM-based even after the boot image was
installed persistently; persistent application data lived separately.

## Kernel compatibility

The historical
[PicUntu instructions](https://github.com/sgjava/byoc/wiki/PicUntu) distinguish
the MK802 III from the IIIS and warn about wireless variants. They assume a
microSD installation path. The implementation here uses USB Loader instead.
PicUntu compatibility was not tested on this unit.

The retained vendor kernel supported this board's memory, Wi-Fi, HDMI and
vendor storage layout. Modern SoC support is not complete support for the board. A replacement needs
the correct device tree, power and clock setup, display/audio path and a proven
boot route. NAND access must also respect the existing vendor layout.

This was a custom musl userspace, not a contemporary Debian installation.
Debian 13's glibc 2.41 has a Linux 3.2 minimum, already newer than 3.0.36.
Static binaries still need compatible syscalls. The published userspace was
checked on the retained kernel; unrelated software may need adaptations. See
[musl's platform notes](https://wiki.musl-libc.org/supported-platforms).

## Networking and application updates

Network startup consists of Wi-Fi association, DHCP and SSH. Public-key
authentication and a pre-provisioned server identity support verified remote
access without a USB cable.

Later application updates staged an inactive release, checked file hashes and
links, selected it atomically and checked service health. The previous release
could be selected again if activation failed. The data store normally returned
to read-only operation. This was application rollback, not dual-slot boot
recovery. This later application store is not part of the Wi-Fi/SSH build.

## Display, video and audio

The launcher rendered to a framebuffer. The working media route used adapted
Rockchip MPP userspace, the legacy VPU/ION interface, decoded NV12 buffers and
the vendor LCDC/HDMI video layer. It did not require an Android desktop or a
working Mali/OpenGL compositor. Mali is a GPU; the H.264 decoder is another block.

A bounded 75-frame 640x360 decode comparison matched FFmpeg's output byte for
byte. Buffer addresses needed updating per displayed frame. Overlay ordering,
transparency, queue limits, timestamps, flushing and teardown each caused
separate problems.

Android's Bionic/Stagefright libraries could not simply be copied into a musl
process. The port needed a compatible userspace path to the kernel interfaces.
Modern Linux offers a different direction through Hantro/V4L2 Request,
DRM/KMS and Mesa Lima, but that route was not brought up on this board.

Audio and video eventually used a shared timeline. Live buffering needed a byte
cap as well as a duration target; asking for more cache did not fix bad clock
handling. Pause, resume and seek exposed ownership and lifecycle bugs. One
vendor I2S width-setting defect and VPU module lifetime defects were identified,
patched and tested in bounded cases. Those fixes did not establish the cause of
all noise or full-device freezes. Audio output also required listening tests
on the television.

## Remote input and AirPlay

[atv-core](https://github.com/corvofeng/atv-core) provided the starting protocol
implementation. Native delegates connected touch, buttons and volume to the
launcher. Additional Remote Text Input work connected the iPhone keyboard to
focused text fields, including session identity, insertion, deletion and Return.
[pyatv](https://github.com/postlund/pyatv/tree/master/pyatv/protocols/companion)
was an important protocol reference.

On-device testing confirmed that typed letters appeared on the television and
Return advanced to the password field. Other input methods were not fully
tested. An OPACK reference-decoding bug also caused Remote connection failures.
AirPlay used [UxPlay](https://github.com/FDH2/UxPlay) and a compatible media path.

[Conduit](https://conduit.flosch.at/) is an Android Apple TV remote client;
compatibility with this receiver was not tested. Android screen casting would
require a separate Google Cast or Miracast receiver implementation.

## What remains uncertain

- Complete freezes and display stripes occurred, including while idle. No final
  root cause was established; power, memory, temperature and drivers remain
  possibilities, not diagnoses.
- Physical NAND restoration was not verified. Application fallback is separate.
- Short successful playback tests do not prove long-term stability, all codecs,
  every source, perfect synchronization or physical audio quality.
- The exact vendor kernel source tree was not recovered. The build reuses the
  matching stock binary and applies the included fixed patch set.

## Build and run

The supported profile is **MK802 IIIS, MT5931 Wi-Fi, vendor 3.0.36+** with the
exact stock kernel and Loader fingerprints below. Other products with the same
case or RK3066 chip can differ. The scripts reject different firmware; another
revision needs its own compatibility analysis and boot validation.

Build inputs are upstream userspace sources and a compatible stock backup.
The tools extract the Wi-Fi assets, configure the rootfs, patch the stock kernel
and package a boot image. The USB uploader runs it in RAM.

### 1. Host prerequisites

The tested build host is an Apple Silicon Mac with command-line developer tools,
Python 3.12+, make and the following Homebrew packages:

```sh
brew install messense/macos-cross-toolchains/armv7-unknown-linux-musleabihf \
  cmake pkgconf libusb e2fsprogs
```

Use a checkout path without spaces. The source builder uses an ARMv7-musl GCC
cross compiler and Linux headers from its sysroot. It rebuilds musl and compiler
runtime for Cortex-A9 so a prebuilt toolchain's VFPv4 startup objects do not leak
into the result. Configurations are in `config/`; URLs and SHA-256 values are in
`config/sources.json`. Seven downloads are verified before extraction.

### 2. Read and verify a backup

You need your own stock `boot.img`, raw ext `system.img`, logical NAND backup and
raw IDB backup with 528-byte records. Keep two verified copies before changing
anything. The logical image alone is not complete physical NAND/OOB recovery.

The historical layout was boot at LBA `0xa000`, length `0x6000` sectors, and
system at `0x41a000`, length `0x180000`; sectors are 512 bytes. Confirm your own
parameter map before reading or using these offsets. The supported stock boot
partition has SHA-256:

```text
ab52ee82f9fed2686dd11c5be094f720367538faaf447fd7f17df5fe252d3cba
```

For owners without an existing backup, the historical read tools were
[rkdeveloptool](https://github.com/rockchip-linux/rkdeveloptool/tree/304f073752fd25c854e1bcf05d8e7f925b1f4e14)
for logical NAND and
[rkflashtool](https://github.com/linux-rockchip/rkflashtool/tree/fc2181c577ef3fb1e821818dfd07e0dac0575b74)
for the legacy IDB format. Their read operations for the confirmed layout are:

```sh
mkdir -p private/backup
rkdeveloptool rl 0 0x800000 private/backup/flash.img
rkdeveloptool rl 0xa000 0x6000 private/backup/boot.img
rkdeveloptool rl 0x41a000 0x180000 private/backup/system.img
rkflashtool i 0 122880 > private/backup/idb-528.bin
```

The last command reads the first 15 historical blocks in the Rockchip IDB
record format. This is not a full raw NAND dump. Check exit codes and lengths;
a second independent read must match the first. Do not resume a failed USB
transfer or retry a hung command without investigating it. Use upstream build
instructions for these tools; other revisions may behave differently.

### 3. Build the userspace

```sh
python3 tools/build-userspace.py
```

This builds BusyBox, musl, compiler-rt, Dropbear, libnl and wpa_supplicant with
both nl80211 and WEXT. A native Dropbear key generator is built separately.
The configuration includes shell test/echo/printf builtins and the compiled
BusyBox applet list. Dropbear is built without syslog and logs to stderr;
its init command omits the unsupported `-E` flag. wpa_supplicant is built with
config-write support disabled, so generated profiles omit `update_config`.
The DHCP helper deletes only default routes during renewal to preserve the
connected subnet route with this BusyBox version.

Outputs and build logs stay in ignored `build/`. To fetch and verify sources
without compiling, add `--fetch-only`. An existing archive cache can be supplied
with `--cache PATH`; every cached archive is still checked.

### 4. Extract matching board assets

```sh
python3 tools/extract-wifi.py \
  --system-image private/backup/system.img \
  --debugfs "$(brew --prefix e2fsprogs)/sbin/debugfs" \
  --usb-debug
```

`debugfs` opens the image read-only. The helper extracts and hash-checks the
MT5931 module and `WIFI_RAM_CODE`. The optional flag also extracts the stock
static `adbd` for first-boot diagnosis; omit it if you do not want that bridge.
A sparse Android filesystem image must first be converted to raw ext format.
The stock image used here was already raw ext3.

### 5. Set your network and SSH identity

```sh
cp network.example.json private/network.json
```

Fill in `ssid` and choose a hostname. Leave `interface` as `wlan0` and
`wifi_backend` as `nl80211,wext` for this profile. It supports WPA2-Personal;
WPA3-only, enterprise and captive-portal networks are not covered.

Use an existing SSH client public key, or make one with `ssh-keygen -t ed25519`.
Keep its private counterpart on your computer. Then run:

```sh
python3 tools/prepare.py \
  --network private/network.json \
  --system-dir private/system \
  --stock-boot private/backup/boot.img \
  --authorized-key ~/.ssh/id_ed25519.pub \
  --usb-debug private/system/bin/adbd
```

The Wi-Fi passphrase is requested through a hidden prompt. A fresh device host
key is generated once and preserved under `private/`. Password SSH login and
forwarding are disabled. If you omitted diagnostic extraction, omit the final
`--usb-debug` argument too.

The result is `build/rootfs.cpio.gz` and `build/boot-linux.img`. The packager
checks the original boot ID, original kernel hash, each patch's old bytes, the
final kernel hash, RAM layout, ramdisk capacity and Rockchip's extended SHA-1.
The fixed patch set reproduces the historical standalone kernel: known NAND
callbacks disabled, early SCU setup, fixed CPU clock and one CPU. It is specific
to this stock image, not a generic kernel patch.

The generated image contains the configured Wi-Fi key and SSH host key. Keep
it private. To prepare another image, move `build/rootfs` and
`build/boot-linux.img` aside; assembly refuses to overwrite an existing build.

### 6. First boot in RAM

Prepare the uploader while the stick is disconnected:

```sh
python3 tools/prepare-ram.py \
  --idb private/backup/idb-528.bin \
  --flash-image private/backup/flash.img
```

This extracts and checks five identical Loader copies from the backup. It builds
the ARM cache/MMU handoff and macOS libusb uploader, pinning all image hashes.
The uploader will compare the live Loader code, MMU table and LBA0 before
uploading. It reads every uploaded byte back before sending one execute request.
Its command whitelist has no NAND write, erase or reboot opcode. It temporarily
patches Loader RAM; power removal clears the change.

Connect with Reset held, release Reset, then promptly run:

```sh
build/ram/usb-boot private/ram-test --one-ram-linux \
  build/ram/kernel.bin build/ram/rootfs.bin build/ram/atags.bin build/ram/handoff.bin
```

Use a new output prefix each time. Stock Loader's idle timeout is roughly 202
seconds. A `COK2` checkpoint proves the cache handoff, **not Linux or networking**.
Wait for association and DHCP, then find the device in your router's client list.
No mDNS hostname service or graphical launcher is included in this profile.
The stock HDMI logo may remain visible. Use DHCP and SSH to confirm startup;
this profile has no graphical status screen.

If you enabled local USB diagnosis, use Android platform-tools:

```sh
adb devices
adb forward tcp:2222 tcp:22
```

This bridge is for a directly attached, trusted USB host; it has no TCP ADB
listener. Use the same pinned SSH command below with `-p 2222` and
`root@127.0.0.1` to inspect `/run/boot.log`, `/run/wifi.log`, `/run/dhcp.log` and
`/run/dropbear.log`. The debug flag enables shell tracing of init; those logs
must stay private. Runtime logs share a 16 MiB tmpfs cap and disappear on reboot.

### 7. Verify SSH

Replace `DEVICE` with the DHCP address:

```sh
ssh -o HostKeyAlias=mk802-prepared \
  -o UserKnownHostsFile=private/known_hosts \
  -o StrictHostKeyChecking=yes \
  -i ~/.ssh/id_ed25519 root@DEVICE
```

The known-hosts file contains the public key generated for this image, so you
can verify identity on the first connection. Check `uname -r`, `cat /proc/1/comm`,
`ip addr`, `ip route` and `free`. PID1 should be the BusyBox init script, with no
Android userspace running. An interactive SSH shell should allocate a PTY too.

`tools/inspect-linux.sh` can collect a small read-only hardware inventory:

```sh
ssh -o HostKeyAlias=mk802-prepared \
  -o UserKnownHostsFile=private/known_hosts \
  -o StrictHostKeyChecking=yes root@DEVICE 'sh -s' < tools/inspect-linux.sh
```

### Persistent installation is a separate step

A successful RAM boot must come first. For the supported layout, persistent
installation replaces only the 12 MiB boot partition. It can interrupt boot if
power fails; the physical NAND restore path has not been validated. Application
A/B fallback does not protect this boot partition. Preserve Loader, parameter,
recovery and your original images.

The historical boot-only operation was `rkdeveloptool wl 0xa000 IMAGE`, followed
by `rkdeveloptool rl 0xa000 0x6000 READBACK` and a complete byte-for-byte comparison
with the intended image before a normal boot. A return to Loader and a write of
the saved stock boot was the planned rollback. Verify RAM boot, stable
power and a recovery route before writing.

## Upstream work

| Project | Role in the original experiment |
| --- | --- |
| [Linux](https://www.kernel.org/) | Kernel and driver infrastructure |
| [linux-rockchip](https://github.com/linux-rockchip) | Historical tools, boot and board references |
| [rkflashtool](https://github.com/linux-rockchip/rkflashtool) | USB protocol comparison |
| [BusyBox](https://busybox.net/) | Small userspace and diagnostic tools |
| [musl](https://musl.libc.org/) | C library for the custom ARM userspace |
| [Dropbear](https://github.com/mkj/dropbear) | SSH server |
| [wpa_supplicant](https://w1.fi/wpa_supplicant/) | Wi-Fi association |
| [UxPlay](https://github.com/FDH2/UxPlay) | AirPlay receiver |
| [GStreamer](https://gstreamer.freedesktop.org/) | Media pipelines and clocks |
| [Rockchip MPP](https://github.com/rockchip-linux/mpp) | Starting point for the legacy decode adapter |
| [FFmpeg](https://ffmpeg.org/) | Media inspection and reference decode |
| [ALSA](https://www.alsa-project.org/) | Audio interface |
| [atv-core](https://github.com/corvofeng/atv-core) | Phone remote starting point |
| [pyatv](https://github.com/postlund/pyatv) | Companion and text-input protocol references |
| [Unicorn](https://www.unicorn-engine.org/) | Bounded execution of ARM code during offline investigation |

Userspace versions: BusyBox 1.37.0, musl 1.2.5, Dropbear 2024.86 and
wpa_supplicant 2.11. Download URLs and archive hashes are pinned in
`config/sources.json`.

Additional primary references:

- [ARM Linux boot requirements](https://docs.kernel.org/arch/arm/booting.html)
- [Linux initramfs format](https://docs.kernel.org/driver-api/early-userspace/buffer-format.html)
- [glibc 2.41 Linux configuration](https://github.com/bminor/glibc/blob/glibc-2.41/sysdeps/unix/sysv/linux/configure.ac)
- [GStreamer stateless H.264 decoder](https://gstreamer.freedesktop.org/documentation/v4l2codecs/v4l2slh264dec.html)
- [Mesa Lima](https://docs.mesa3d.org/drivers/lima.html)

## Distribution boundaries

The host utilities and shell code are MIT. `ram/marker.S`, `ram/handoff.S`
and their linker script are distributed under GPL-2.0-or-later; the license text
is in `ram/COPYING`. The fixed kernel patch manifest describes changes to the
stock GPL kernel binary and follows those kernel terms. No complete stock
kernel, module, firmware or adbd binary is redistributed. Upstream userland
sources retain their own licenses inside each verified download. If you share
an image, you must separately satisfy all applicable source and license duties;
this source-only repository is not a license grant for vendor firmware.
Third-party images and marks are not covered by the source-code license.
