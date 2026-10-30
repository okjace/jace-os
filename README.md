![Jace OS logo](https://okjace.github.io/jace-os/img/apple-touch-icon.png)

# Jace OS

**Website:** https://okjace.github.io/jace-os/ · **Download:** [latest release](https://github.com/okjace/jace-os/releases/latest)

An Arch-based Linux that works like you expect: windows behave like they do on Windows, a niri-style top bar, a start menu on the Windows key, one Settings app for everything, and updates that install themselves. Jace Launcher, Jace Social, Jace Store, Zen Browser and your Jace account are built in.

## Features

- **Windows-style windows** ([labwc](https://labwc.github.io), Wayland): minimize, maximize and close on the right. Drag a window to the top to maximize it, or to a side to snap it. Alt+Tab, Win+arrows, Alt+F4, Win+D and Win+L all work.
- **A start menu on the Windows key**: pinned apps, all apps and search as you type. Right-click an app to pin or hide it. Settings → Start menu changes where it opens, its size and layout, and what's pinned.
- **A top bar inspired by niri** (Waybar): rounded islands with the start button, desktops and open windows on the left, the clock in the middle, and tray, updates, sound, network, Bluetooth, battery, your Jace account and power on the right.
- **Settings**: your Jace account, updates, virus protection, Wi-Fi, Bluetooth, displays (resolution, scale, night light), sound, appearance and wallpaper, start menu, windows and desktops, keyboard and touchpad, power, date and time, apps (with Jace Store built in), default apps, users and About.
- **Get apps in Settings**: search Jace Store, Flathub and the Arch repositories from Settings → Apps and install with one click. Jace Store downloads are checked against the store's SHA-512. Everything you install there also shows up in Jace Store → My apps.
- **AppImages just work**: double-click one to open it. Right-click it in Files and choose **Install** to put it in ~/Applications and the start menu, and in Jace Store → My apps.
- **Virus protection** ([ClamAV](https://www.clamav.net)): real-time protection scans files in home folders as soon as they're downloaded, created or opened, and moves threats to quarantine. Every home folder is also scanned once a day, and virus signatures update by themselves. Settings → Virus protection has quick, full and folder scans, the quarantine (restore or delete) and switches for all of it. You get an alert whenever something is found. Real-time protection uses about 1 GB of memory, and you can turn it off.
- **Automatic updates**: `jace-update.timer` installs Arch updates (the Linux kernel and security fixes), new Jace app and Zen Browser releases, and Flatpak updates every few hours while plugged in. You can see and install them yourself in Settings → Updates.
- **The Jace apps**: Jace Launcher, Jace Social and Jace Store come preinstalled, along with Zen Browser as the default browser and [yay](https://github.com/Jguer/yay) for the AUR (Jace Store's AUR source uses it). The apps that come with Jace OS are added to Jace Store's My apps as custom apps.
- **Sign in with Jace**: the account page in Settings and the account button on the top bar sign in to [Jace](https://jaceo.vercel.app) (OAuth 2.0 with PKCE).
- **Boot splash and login screen**: a Plymouth theme with the Jace OS logo, and a greetd + ReGreet login screen.

## Keyboard shortcuts

| Keys | What it does |
|---|---|
| Win | Start menu (start typing to search) |
| Alt+Tab | Switch windows |
| Win+← / Win+→ | Snap to the left or right half |
| Win+↑ / Win+↓ | Maximize / restore or minimize |
| Win+D | Show the desktop |
| Win+E / Win+I / Win+L | Files / Settings / Lock |
| Win+Shift+S / Print | Screenshot an area / the whole screen |
| Ctrl+Shift+Esc | System Monitor |
| Ctrl+Alt+T | Terminal |

## Trying and installing

1. Download the ISO from the [releases](https://github.com/okjace/jace-os/releases/latest) and write it to a USB stick (8 GB or bigger) with balenaEtcher, Rufus or `dd`.
2. Start your computer from the USB stick. The boot menu has:
   - **Try Jace OS (without installing)**: the full desktop running from the USB stick. Nothing on the computer changes.
   - **Install Jace OS**: the same, with the installer opened right away.
   - **Try Jace OS (safe graphics)**: if the screen stays black or flickers.
3. Click **Install Jace OS** (in the welcome app, or the start menu). The installer asks for:
   - your language, keyboard and time zone
   - the disk to install on (it's erased), with optional encryption
   - your name, username, computer name and password

   Then it installs Jace OS, with the newest Jace apps and Zen Browser, in 10 to 20 minutes. You need an internet connection.
4. To keep Windows or another system alongside, or to pick your own partitions, use **Advanced install** on the installer's first page. It opens archinstall with the Jace OS preset.

### Already on Arch

```ini
# /etc/pacman.conf
[jace-os]
SigLevel = Optional TrustAll
Server = https://github.com/okjace/jace-os/releases/download/repo
```

```bash
sudo pacman -Syu jace-os jace-launcher-bin jace-social-bin jace-store-bin zen-browser-bin yay-bin
sudo /usr/lib/jace-os/enable-splash
```

Then choose **Jace OS** on the login screen.

## The repository

| Folder | What's in it |
|---|---|
| `desktop/` | Everything the `jace-os` package installs, laid out like `/`: the labwc, Waybar, mako and fuzzel configs (`usr/share/jace-os/xdg`), the Jace OS apps (`usr/lib/jace-os/jaceos`), scripts (`usr/bin`), the Plymouth theme, the login screen, wallpapers, the update timer and the installer preset. |
| `desktop/usr/lib/jace-os/jaceos/` | The Python + GTK 4 + libadwaita apps: `settings.py`, `menu.py` (start menu), `welcome.py`, `installer.py` (with the `install-backend` script), `account.py` (Sign in with Jace), `updates.py`, `antivirus.py`, `store.py` (Jace Store, Flathub and Arch in Settings) and `appimage.py`. |
| `packages/` | PKGBUILDs: `jace-os`, plus `jace-launcher-bin`, `jace-social-bin`, `jace-store-bin`, `zen-browser-bin` and `yay-bin`, which unpack each app's official release. |
| `archiso/` | The live USB profile (packages, live user and auto-login). `build.sh` combines it with archiso's `releng` boot setup. |
| `site/` | The website on GitHub Pages. |
| `tools/build-packages.sh` | Builds the package repository. |

### Building

On Arch:

```bash
tools/build-packages.sh          # packages into out/repo (needs base-devel, squashfs-tools)
sudo pacman -S archiso
sudo ./build.sh                  # the ISO into out/
```

GitHub Actions does both:

- **Packages** (`.github/workflows/packages.yml`) runs every hour and on every push. It only builds packages whose version changed (a new Jace app or Zen Browser release, or a Jace OS commit) and publishes the pacman repository as the `repo` release. An app repository can also trigger it right away with a `repository_dispatch` of type `app-released`.
- **ISO** (`.github/workflows/iso.yml`) builds the ISO after every change to Jace OS (once Packages has published it), every Monday, when you push a `v*` tag, or by hand. When the build works, it publishes a GitHub release (versioned by date, like `v2026.10.30`, or by your tag), then publishes that release on [Jace Store](https://jace-store-deb.vercel.app/project/jace-os) as links to the GitHub downloads (`tools/publish-store.py`, needs the `JACE_STORE_TOKEN` secret).
- **Website** (`.github/workflows/pages.yml`) publishes `site/`.

### Trying the apps without installing

The apps run from a checkout on any Linux with GTK 4 and libadwaita:

```bash
JACE_OS_ROOT="$PWD/desktop" desktop/usr/bin/jace-settings
JACE_OS_ROOT="$PWD/desktop" desktop/usr/bin/jace-menu
```

## Jace account sign-in

`/etc/jace-os/account.conf` holds the Jace URL and the `client_id` of the "Jace OS" app on Jace. Jace OS is a public client, so it uses PKCE and has no client secret. Sign-in needs Jace's token endpoint to accept PKCE (`code_verifier`) without a secret, and a registered `client_id`. Until both exist, Settings says sign-in isn't set up yet.

## License

MIT. Jace OS is based on Arch Linux and isn't affiliated with Arch Linux. Zen Browser, labwc, Waybar and the other bundled software keep their own licenses.
