#!/bin/bash
# Builds the Jace OS packages into a pacman repository in out/repo (jace-os.db):
# jace-os, jace-launcher-bin, jace-social-bin, jace-store-bin, jace-browser-bin, zen-browser-bin
# (for computers that still have it) and yay-bin.
# The app packages are set to the newest GitHub release of each app first, and only
# packages whose new version isn't built or published yet are built.
#
#   tools/build-packages.sh            build what's new
#   tools/build-packages.sh jace-os    just these
#
# CI puts the published database in out/repo and the published file names in
# out/published.txt first, so a run with nothing new finishes in seconds.
# Run it on Arch (or in an archlinux container) as a normal user with base-devel.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD
repo=$root/out/repo
published=$root/out/published.txt
mkdir -p "$repo"
[ -f "$published" ] || : > "$published"

latest_tag() { # owner/repo [tag-prefix]
  local api="https://api.github.com/repos/$1/releases?per_page=30" auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  curl -fsSL "${auth[@]}" "$api" |
    python3 -c 'import json,sys; p=sys.argv[1]
for r in json.load(sys.stdin):
    if not r["draft"] and not r["prerelease"] and r["tag_name"].startswith(p):
        print(r["tag_name"]); break' "${2:-}"
}

set_version() { # pkgdir version: a new version starts again at pkgrel 1; the same version keeps its pkgrel
  local f="packages/$1/PKGBUILD"
  if ! grep -qx "pkgver=$2" "$f"; then
    sed -i -e "s/^pkgver=.*/pkgver=$2/" -e "s/^pkgrel=.*/pkgrel=1/" "$f"
  fi
}

update_versions() {
  local t
  t=$(latest_tag okjace/jace-launcher v) && [ -n "$t" ] && set_version jace-launcher-bin "${t#v}"
  t=$(latest_tag okjace/jace-social app-v) && [ -n "$t" ] && set_version jace-social-bin "${t#app-v}"
  t=$(latest_tag okjace/jace-store desktop-v) && [ -n "$t" ] && set_version jace-store-bin "${t#desktop-v}"
  t=$(latest_tag okjace/jace-browser v) && [ -n "$t" ] && set_version jace-browser-bin "${t#v}"
  t=$(latest_tag Jguer/yay v) && [ -n "$t" ] && set_version yay-bin "${t#v}"
  t=$(latest_tag zen-browser/desktop) && [ -n "$t" ] && sed -i "s/^_tag=.*/_tag=$t/" packages/zen-browser-bin/PKGBUILD
  grep -h '^pkgver=\|^_tag=' packages/*/PKGBUILD | sed 's/^/  /'
}

pkgs=("$@")
[ ${#pkgs[@]} -eq 0 ] && pkgs=(jace-os jace-launcher-bin jace-social-bin jace-store-bin jace-browser-bin zen-browser-bin yay-bin)

echo "==> Newest versions"
update_versions || echo "  (couldn't reach GitHub; building the versions in the PKGBUILDs)"

built=()
for p in "${pkgs[@]}"; do
  if [ "$p" = jace-os ]; then
    # Its version comes from pkgver() (VERSION plus the commit count), which --packagelist doesn't run.
    base=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' desktop/usr/lib/jace-os/jaceos/__init__.py)
    count=$(git rev-list --count HEAD 2>/dev/null || echo 0)
    file="jace-os-$base.r$count-1-x86_64.pkg.tar.zst"
  else
    file=$(cd "packages/$p" && PKGDEST="$repo" makepkg --packagelist | head -n1)
    file=$(basename "$file")
  fi
  if [ -f "$repo/$file" ] || grep -qxF "$file" "$published"; then
    echo "==> $p is up to date ($file)"
    continue
  fi
  echo "==> Building $p ($file)"
  # --nodeps: these only unpack and copy files, so nothing has to be installed to build them.
  (cd "packages/$p" && PKGDEST="$repo" makepkg --force --cleanbuild --nodeps --noconfirm)
  built+=("$file")
done

if [ ${#built[@]} -eq 0 ]; then
  echo "==> Nothing new to publish"
  exit 0
fi

echo "==> Updating the repository database"
# Adds to the published database if CI downloaded it, replacing older versions of each package.
(cd "$repo" && repo-add --prevent-downgrade jace-os.db.tar.gz "${built[@]/#/./}")
# GitHub release files can't be symlinks, so make the plain names real files.
(cd "$repo" && for f in jace-os.db jace-os.files; do
  [ -L "$f" ] && cp --remove-destination "$(readlink "$f")" "$f"
done; true)
printf '%s\n' "${built[@]}" > "$root/out/built.txt"
ls -lh "$repo"
