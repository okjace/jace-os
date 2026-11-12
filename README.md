![Jace OS logo](https://okjace.github.io/jace-os/img/apple-touch-icon.png)

# Jace OS

**Website:** https://okjace.github.io/jace-os/ · **Download:** [latest release](https://github.com/okjace/jace-os/releases/latest)

An Arch-based Linux that works like you expect: windows behave like they do on Windows, a niri-style top bar, a start menu on the Windows key, one Settings app for everything, and updates that install themselves. Jace Launcher, Jace Social, Jace Store, Jace Browser and your Jace account are built in.

## Features

- **Windows-style windows** ([labwc](https://labwc.github.io), Wayland): minimize, maximize and close on the right. Drag a window to the top to maximize it, or to a side to snap it. Alt+Tab, Win+arrows, Alt+F4, Win+D and Win+L all work.
- **A start menu on the Windows key**: pinned apps, all apps and search as you type. Right-click an app to pin or hide it. Settings → Start menu changes where it opens, its size and layout, and what's pinned.
- **A top bar inspired by niri** (Waybar): rounded islands with the start button, desktops and open windows on the left, the clock in the middle, and tray, updates, sound, network, Bluetooth, battery, your Jace account and power on the right.
- **Modern apps**: GNOME's libadwaita apps throughout: Files (Nautilus), Ptyxis terminal, Text Editor, Image Viewer, Showtime videos, Decibels music, Papers documents, Calculator, Disks and System Monitor. Older GTK 3 apps get the matching adw-gtk3 theme, and Qt apps follow it.
- **Settings**: your Jace account, updates, virus protection, Wi-Fi, Bluetooth, displays (resolution, scale, night light), sound, appearance and wallpaper, start menu, windows and desktops, keyboard and touchpad, power, date and time, apps (with Jace Store built in), default apps, storage, users and About.
- **Get apps in Settings**: search Jace Store, Flathub and the Arch repositories from Settings → Apps and install with one click. Jace Store downloads are checked against the store's SHA-512. Everything you install there also shows up in Jace Store → My apps.
- **AppImages just work**: double-click one to open it. Right-click it in Files and choose **Install** to put it in ~/Applications and the start menu, and in Jace Store → My apps.
- **Virus protection** ([ClamAV](https://www.clamav.net)): real-time protection scans files in home folders as soon as they're downloaded, created or opened, and moves threats to quarantine. Every home folder is also scanned once a day, and virus signatures update by themselves. Settings → Virus protection has quick, full and folder scans, the quarantine (restore or delete) and switches for all of it. You get an alert whenever something is found. Real-time protection uses about 1 GB of memory, and you can turn it off.
- **Storage**: Settings → Storage shows how full each drive is and what's using space, and frees it up: the trash, old package downloads, AUR build files, picture previews, unused packages and Flatpak runtimes. It can empty old trash by itself and warns you when a drive is almost full.
- **Menus that match**: right-click the desktop for the Jace OS menu, and every GTK app's menus (right-click, ⋮ and tray menus) get the Jace green and rounder, roomier items.
- **Languages**: Setup and Settings → Language switch the whole computer to 32 languages. Setup itself is translated into 18 so far (right to left for Arabic and Hebrew), and GNOME apps, Jace Browser and most others follow the system language.
- **Reset this computer**: Settings → About can reset just your settings, or remove every account and its files and start Setup again, like a new computer. Apps stay installed.
- **Clipboard manager**: Win+V shows everything you copied, pictures included. Search it, pin things to keep them, and click to paste.
- **A top bar you can change**: Settings → Top bar turns modules on and off (Now Playing, weather, processor, memory, Do not disturb, Keep awake and more), moves it to the bottom, and sets the clock (12 or 24 hours, seconds, date). Click the clock (or Win+N) for the calendar.
- **Face unlock**: Settings → Face unlock sets up Howdy, the Linux Windows Hello, for the lock screen, sign-in and password prompts. Best with an infrared camera.
- **Automatic updates**: `jace-update.timer` installs Arch updates (the Linux kernel and security fixes), new Jace app and Jace Browser releases, and Flatpak updates (system-wide and each person's own) every few hours while plugged in. You can see and install them yourself in Settings → Updates, with a progress bar, including apps you installed from the AUR with yay (those are built as you, so they're only updated when you click Update). New Jace app releases show up there as soon as they're on GitHub.
- **The Jace apps**: Jace Launcher, Jace Social and Jace Store come preinstalled, along with [Jace Browser](https://github.com/okjace/jace-browser) as the default browser and [yay](https://github.com/Jguer/yay) for the AUR (Jace Store's AUR source uses it). The apps that come with Jace OS are added to Jace Store's My apps as custom apps.
- **Sign in with Jace**: the account page in Settings and the account button on the top bar sign in to [Jace](https://jaceo.vercel.app) (OAuth 2.0 with PKCE). Your Jace display name and photo become this computer's account name and picture (you can turn that off), and the top bar's corner shows your picture and name right away. Not signed in? It shows your account on this computer. Jace apps can ask Jace OS to sign them in too (`jace-account --token-for`).
- **Cloud sync**: Settings → Jace account keeps what you choose the same on all your Jace OS computers: your look, wallpaper, desktop (top bar, start menu, shortcuts, windows, keyboard and mouse) and apps. It can also sync files: Documents & Desktop, your Claude sessions (chats, edit history and memory) and project folders you pick, keeping permissions and symlinks and skipping build folders; something deleted on one computer goes to the Trash on the others. Everything is end-to-end encrypted with a sync passphrase (AES-256-GCM), so neither Jace nor its file storage (Backblaze B2) can read it. Setup offers it right after Sign in with Jace, so a new computer brings everything in.
- **Users**: pick a ready-made picture, build your own (colours and an emoji or your initials) or use a photo; change your name; add people with a password, set passwords, and delete accounts in Settings → Users.
- **Lock screen**: gtklock with your blurred wallpaper, a big clock, your picture and name, media controls and power buttons. Face unlock works on it too.
- **Animations**: the start menu, clipboard and calendar slide and fade, Settings pages crossfade, and the top bar and buttons ease between states. (labwc itself doesn't animate windows.)
- **Restart reminders**: after an update that needs it, a notification and the top bar say whether to restart (Linux, drivers, system libraries) or just sign out (desktop parts).
- **Boot splash and login screen**: a Plymouth theme with the Jace OS logo, and a greetd + ReGreet login screen.

## Keyboard shortcuts

Change any of these, turn them off, or add your own in Settings → Keyboard shortcuts. Pick your terminal in Settings → Apps.

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
| Ctrl+Alt+T or Win+Enter | Terminal |
| Win+. | Emoji and symbols |
| Win+V | Clipboard history |
| Win+N | Calendar |

## Trying and installing

1. Download the ISO from the [releases](https://github.com/okjace/jace-os/releases/latest) and write it to a USB stick (8 GB or bigger) with balenaEtcher, Rufus or `dd`.
2. Start your computer from the USB stick. The boot menu has:
   - **Try Jace OS (without installing)**: the full desktop running from the USB stick. Nothing on the computer changes.
   - **Install Jace OS**: the same, with the installer opened right away.
   - **Try Jace OS (safe graphics)**: if the screen stays black or flickers.
3. Click **Install Jace OS** (in the notification, or the start menu) and pick the disk to install on (it's erased), with optional encryption. It installs Jace OS, with the newest Jace apps and Jace Browser, in 10 to 20 minutes. You need an internet connection.
4. Restart and take out the USB stick. **Setup** starts: an animated, phone-style welcome where you pick your language, keyboard and Wi-Fi, sign in with Jace (your Jace name and picture become your account's), pick your time zone and look, and make your account. Then you're signed straight in.
5. To keep Windows or another system alongside, or to pick your own partitions, use **Advanced install** on the installer's first page. It opens archinstall with the Jace OS preset.

### Already on Arch

```ini
# /etc/pacman.conf
[jace-os]
SigLevel = Optional TrustAll
Server = https://github.com/okjace/jace-os/releases/download/repo
```

```bash
sudo pacman -Syu jace-os jace-launcher-bin jace-social-bin jace-store-bin jace-browser-bin yay-bin
sudo /usr/lib/jace-os/enable-splash
```

Then choose **Jace OS** on the login screen.

## The repository

| Folder | What's in it |
|---|---|
| `desktop/` | Everything the `jace-os` package installs, laid out like `/`: the labwc, Waybar, mako and fuzzel configs (`usr/share/jace-os/xdg`), the Jace OS apps (`usr/lib/jace-os/jaceos`), scripts (`usr/bin`), the Plymouth theme, the login screen, wallpapers, the update timer and the installer preset. |
| `desktop/usr/lib/jace-os/jaceos/` | The Python + GTK 4 + libadwaita apps: `settings.py`, `menu.py` (start menu), `setup.py` (Setup, the first start), `installer.py` (with the `install-backend` script), `account.py` (Sign in with Jace), `updates.py`, `antivirus.py`, `store.py` (Jace Store, Flathub and Arch in Settings) and `appimage.py`. |
| `packages/` | PKGBUILDs: `jace-os`, plus `jace-launcher-bin`, `jace-social-bin`, `jace-store-bin`, `jace-browser-bin`, `zen-browser-bin` (kept for computers that still have it) and `yay-bin`, which unpack each app's official release. |
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

- **Packages** (`.github/workflows/packages.yml`) runs every hour and on every push. It only builds packages whose version changed (a new Jace app or Jace Browser release, or a Jace OS commit) and publishes the pacman repository as the `repo` release. An app repository can also trigger it right away with a `repository_dispatch` of type `app-released`.
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

MIT. Jace OS is based on Arch Linux and isn't affiliated with Arch Linux. labwc, Waybar and the other bundled software keep their own licenses.
