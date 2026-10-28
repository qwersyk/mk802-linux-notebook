#!/bin/sh
# Read-only inventory. No device nodes, network probes or configuration changes.
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Run this on the Linux target, not on the build computer.' >&2
    exit 1
fi
printf '\nKernel and architecture\n'
uname -r -m
printf '\nMemory\n'
if [ -r /proc/meminfo ]; then
    while IFS= read -r line; do
        case "$line" in MemTotal:*|MemFree:*|MemAvailable:*|SwapTotal:*) printf '%s\n' "$line" ;; esac
    done < /proc/meminfo
fi
printf '\nLoaded module names\n'
if [ -r /proc/modules ]; then
    while read -r name rest; do printf '%s\n' "$name"; done < /proc/modules
fi
printf '\nFramebuffer geometry\n'
for fb in /sys/class/graphics/fb*; do
    [ -d "$fb" ] || continue
    printf '%s\n' "${fb##*/}"
    for field in virtual_size bits_per_pixel; do
        [ -r "$fb/$field" ] || continue
        IFS= read -r value < "$fb/$field"
        printf '  %s: %s\n' "$field" "$value"
    done
done
printf '\nALSA PCM endpoints (no playback)\n'
if [ -r /proc/asound/pcm ]; then
    while IFS= read -r line; do printf '%s\n' "$line"; done < /proc/asound/pcm
fi
printf '\nMTD layout (if exposed by this kernel)\n'
if [ -r /proc/mtd ]; then
    while IFS= read -r line; do printf '%s\n' "$line"; done < /proc/mtd
fi
