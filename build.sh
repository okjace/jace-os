#!/bin/bash
# Builds the Jace OS live/install ISO into out/.
#
#   sudo ./build.sh
#
# Needs Arch Linux (or an archlinux container run with --privileged) with archiso
# installed, and the packages from tools/build-packages.sh in out/repo (build.sh
# downloads them from the GitHub release if they're missing).
set -euo pipefail
cd "$(dirname "$0")"
root=$PWD
work=${WORK_DIR:-$root/work}
out=$root/out
repo=$out/repo
releng=/usr/share/archiso/configs/releng
profile=$work/profile

[ "$(id -u)" -eq 0 ] || { echo "Run this as root: sudo ./build.sh" >&2; exit 1; }
command -v mkarchiso >/dev/null || { echo "Install archiso first: pacman -S archiso" >&2; exit 1; }

if [ ! -f "$repo/jace-os.db" ]; then
  echo "==> Downloading the Jace OS packages"
  mkdir -p "$repo"
  base=https://github.com/okjace/jace-os/releases/download/repo
  curl -fL "$base/jace-os.db" -o "$repo/jace-os.db"
  tar -xzOf "$repo/jace-os.db" --wildcards '*/desc' | awk '/%FILENAME%/{getline; print}' |
    while read -r pkg; do curl -fL "$base/$pkg" -o "$repo/$pkg"; done
  cp "$repo/jace-os.db" "$repo/jace-os.db.tar.gz"
fi

echo "==> Putting the profile together"
rm -rf "$profile"
mkdir -p "$profile"
# Boot menus and live-system plumbing come from archiso's own releng profile...
cp -a "$releng/." "$profile/"
# ...and everything else from ours.
cp -a archiso/. "$profile/"

# No root auto-login on tty1: the Jace OS login screen (greetd) uses it.
rm -f "$profile/airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf"

# Packages: what releng needs to boot, plus ours.
grep -Ev '^\s*(#|$)' archiso/packages.x86_64 | sort -u > "$profile/packages.x86_64"

# The local Jace OS repository.
cp "$releng/pacman.conf" "$profile/pacman.conf"
printf '\n[jace-os]\nSigLevel = Optional TrustAll\nServer = file://%s\n' "$repo" >> "$profile/pacman.conf"

# The boot splash: Plymouth in the initramfs, "quiet splash" on every boot entry.
sed -Ei '/^HOOKS=/ s/\budev\b/udev plymouth/' "$profile/airootfs/etc/mkinitcpio.conf.d/archiso.conf"
find "$profile/efiboot" "$profile/syslinux" "$profile/grub" -type f \( -name '*.conf' -o -name '*.cfg' \) 2>/dev/null |
  while read -r f; do
    sed -Ei -e 's/Arch Linux/Jace OS/g' \
      -e '/^\s*(options|APPEND|linux)\s.*archisobasedir/ s/$/ quiet splash/' "$f"
  done
echo "==> Building the ISO (this takes a while)"
rm -rf "$work/iso"
mkarchiso -v -r -w "$work/iso" -o "$out" "$profile"
ls -lh "$out"/*.iso
