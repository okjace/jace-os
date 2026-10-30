"""Jace Store inside Settings → Apps: search and install apps and games from the
same places Jace Store does:

  Jace Store   apps and games published on Jace Store (its /api/v2)
  Flathub      Flatpak apps, installed just for you
  Arch Linux   the official Jace OS / Arch repositories (pacman)

Everything Settings installs is written to Jace Store's own installed.json
(~/.local/share/jace-store), in the same format the Jace Store app uses, so it
shows up in Jace Store's My apps, with Open, Details and Uninstall, and Jace Store
keeps it up to date. Settings can remove those apps again too.

Downloads from Jace Store are checked against the store's SHA-512 before installing.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from jaceos import STORE_URL

API = f"{STORE_URL}/api/v2"
USER_AGENT = "JaceOS-Settings/1.0 (+https://github.com/okjace/jace-os)"
# Store projects that Jace OS already ships as system packages.
BUNDLED = {"jace-launcher": "jace-launcher", "jace-social-desktop": "jace-social", "jace-store-app": "jace-store"}
SOURCE_NAMES = {"jacestore": "Jace Store", "flathub": "Flathub", "distro": "Arch Linux"}


class StoreError(Exception):
    pass


@dataclass
class App:
    source: str            # jacestore | flathub | distro
    id: str                # project id / Flatpak app id / package name
    title: str
    summary: str = ""
    icon_url: str | None = None
    version: str = ""
    slug: str = ""         # Jace Store only
    author: str = ""
    extra: dict = field(default_factory=dict)


# --- Jace Store's installed.json --------------------------------------------------------------------

def _store_dir() -> Path:
    d = Path(os.environ.get("JACE_STORE_HOME") or
             Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "jace-store")
    d.mkdir(parents=True, exist_ok=True)
    return d


def installed() -> dict:
    try:
        return json.loads((_store_dir() / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _put(source: str, ident: str, record: dict):
    db = installed()
    db[f"{source}:{ident}"] = {**record, "source": source, "id": ident, "installed_at": time.time()}
    p = _store_dir() / "installed.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2), encoding="utf-8")
    tmp.replace(p)


def _drop(source: str, ident: str):
    db = installed()
    db.pop(f"{source}:{ident}", None)
    (_store_dir() / "installed.json").write_text(json.dumps(db, indent=2), encoding="utf-8")


def record_of(app: App) -> dict | None:
    return installed().get(f"{app.source}:{app.id}")


def status(app: App) -> str:
    """bundled | installed | available"""
    if app.source == "jacestore" and app.slug in BUNDLED:
        return "bundled"
    if record_of(app):
        return "installed"
    if app.source == "distro" and app.extra.get("installed"):
        return "installed"
    if app.source == "flathub" and app.id in _flatpaks():
        return "installed"
    return "available"


# --- searching ------------------------------------------------------------------------------------

def _get_json(url: str, data: dict | None = None, timeout=20):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise StoreError("Couldn't reach the internet.") from e


def search_jacestore(q: str, limit=20) -> list[App]:
    params = urllib.parse.urlencode({"query": q, "limit": limit, "index": "downloads",
                                     "facets": json.dumps([["project_type:app", "project_type:game"]])})
    hits = _get_json(f"{API}/search?{params}").get("hits", [])
    return [App("jacestore", h["project_id"], h.get("title") or h["slug"], h.get("description") or "",
                h.get("icon_url"), slug=h["slug"], author=h.get("author", ""),
                extra={"type": h.get("project_type")}) for h in hits]


def search_flathub(q: str, limit=20) -> list[App]:
    if not q:
        hits = _get_json("https://flathub.org/api/v2/collection/popular?page=1&per_page=20").get("hits", [])
    else:
        hits = _get_json("https://flathub.org/api/v2/search", {"query": q}).get("hits", [])
    hits = [h for h in hits if h.get("type", "desktop-application") in ("desktop-application", "console-application")]
    return [App("flathub", h["app_id"], h.get("name") or h["app_id"], h.get("summary") or "", h.get("icon"),
                author=h.get("developer_name") or "", extra={"verified": h.get("verification_verified")})
            for h in hits[:limit] if h.get("app_id")]


def search_distro(q: str, limit=20) -> list[App]:
    if not q:
        return []
    try:
        out = subprocess.run(["pacman", "-Ss", q], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    lines = out.splitlines()
    apps, seen = [], set()
    for head, desc in zip(lines[::2], lines[1::2]):
        m = re.match(r"^(\S+)/(\S+)\s+(\S+)(.*)$", head)
        if m and m.group(2) not in seen:   # the same package can be in more than one repository
            seen.add(m.group(2))
            apps.append(App("distro", m.group(2), m.group(2), desc.strip(), version=m.group(3),
                            extra={"repo": m.group(1), "installed": "[installed" in m.group(4)}))
    ql = q.lower()
    apps.sort(key=lambda a: (a.id.lower() != ql, not a.id.lower().startswith(ql), ql not in a.id.lower(), a.id))
    return apps[:limit]


def search(q: str) -> dict[str, list[App] | Exception]:
    """Searches every source at once. Values are results, or the error that source hit."""
    funcs = {"jacestore": search_jacestore, "flathub": search_flathub, "distro": search_distro}
    results = {}
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        futures = {pool.submit(f, q): k for k, f in funcs.items()}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results[futures[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001 - shown per source
                results[futures[fut]] = e
    return results


# --- installing -----------------------------------------------------------------------------------

def _flatpaks() -> set[str]:
    try:
        out = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True,
                             timeout=20).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def _run(cmd: list[str], what: str):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        last = (r.stderr.strip() or r.stdout.strip()).splitlines()
        raise StoreError(f"{what}: {last[-1] if last else 'cancelled'}")
    return r


def install(app: App, progress=None) -> str:
    if app.source == "flathub":
        _run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
              "https://dl.flathub.org/repo/flathub.flatpakrepo"], "Couldn't add Flathub")
        _run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", app.id], "Flatpak couldn't install it")
        _put("flathub", app.id, {"title": app.title, "summary": app.summary, "icon_url": app.icon_url,
                                 "version_number": app.version, "kind": "source", "launch_kind": "flatpak",
                                 "launch": app.id})
    elif app.source == "distro":
        _run(["pkexec", "pacman", "-S", "--needed", "--noconfirm", app.id], "pacman couldn't install it")
        _put("distro", app.id, {"title": app.title, "summary": app.summary, "icon_url": None,
                                "version_number": app.version, "kind": "source", "manager": "pacman"})
    else:
        _install_jacestore(app, progress)
    return f"{app.title} is installed"


def _kind_of(filename: str) -> str | None:
    f = filename.lower()
    if f.endswith((".pkg.tar.zst", ".pkg.tar.xz")):
        return "pacman"
    if f.endswith(".flatpakref"):
        return "flatpakref"
    if f.endswith(".flatpak"):
        return "flatpak"
    if f.endswith(".appimage"):
        return "appimage"
    return None


def _pick_file(app: App):
    versions = _get_json(f"{API}/project/{urllib.parse.quote(app.slug)}/version")
    order = ["pacman", "flatpakref", "flatpak", "appimage"]
    for v in sorted(versions if isinstance(versions, list) else [], key=lambda v: v.get("date_published", ""),
                    reverse=True):
        if v.get("version_type", "release") != "release":
            continue
        files = [f for f in v.get("files", []) if _kind_of(f.get("filename", ""))
                 and not re.search(r"(arm64|aarch64)", f["filename"], re.I)]
        if files:
            files.sort(key=lambda f: order.index(_kind_of(f["filename"])))
            return v, files[0]
    return None


def _download(file: dict, progress=None) -> Path:
    work = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "downloads"
    work.mkdir(parents=True, exist_ok=True)
    dest = work / os.path.basename(file["filename"])
    req = urllib.request.Request(file.get("direct_url") or file["url"], headers={"User-Agent": USER_AGENT})
    want = (file.get("hashes") or {}).get("sha512")
    h = hashlib.sha512()
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as out:
            total = int(r.headers.get("Content-Length") or file.get("size") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        dest.unlink(missing_ok=True)
        raise StoreError(f"The download failed: {e}") from e
    if want and h.hexdigest() != want:
        dest.unlink(missing_ok=True)
        raise StoreError("The download didn't match Jace Store's checksum, so it wasn't installed.")
    return dest


def _install_jacestore(app: App, progress):
    found = _pick_file(app)
    if not found:
        raise StoreError("open-in-store")
    ver, f = found
    fmt = _kind_of(f["filename"])
    path = _download(f, progress)
    try:
        if fmt == "pacman":
            _run(["pkexec", "pacman", "-U", "--noconfirm", str(path)], "pacman couldn't install it")
            name = subprocess.run(["pacman", "-Qqp", str(path)], capture_output=True, text=True).stdout.strip()
            rec = {"kind": "package", "manager": "pacman", "package": name}
        elif fmt in ("flatpakref", "flatpak"):
            before = _flatpaks()
            _run(["flatpak", "install", "--user", "-y", "--noninteractive",
                  "--from" if fmt == "flatpakref" else "--bundle", str(path)], "Flatpak couldn't install it")
            new = sorted(_flatpaks() - before)
            rec = {"kind": "flatpak", "package": new[0] if new else None, "launch": new[0] if new else None}
        else:
            rec = _install_appimage(app, path)
    finally:
        if fmt != "appimage":
            path.unlink(missing_ok=True)
    _put("jacestore", app.id, {
        "title": app.title, "slug": app.slug, "project_id": app.id, "icon_url": app.icon_url,
        "version_id": ver["id"], "version_number": ver.get("version_number", ""), "file_id": f.get("id"),
        "filename": f["filename"], "format": fmt, "action": "install", **rec,
    })


def _install_appimage(app: App, path: Path) -> dict:
    # Same places the Jace Store app uses, so it can manage it later.
    apps_dir = Path(os.environ.get("JACE_STORE_APPS") or Path.home() / "Applications")
    apps_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w .-]", "", app.title).strip() or app.slug
    dest = apps_dir / f"{safe}.AppImage"
    dest.unlink(missing_ok=True)
    path.replace(dest)
    dest.chmod(0o755)

    icon = "application-x-executable"
    if app.icon_url:
        try:
            req = urllib.request.Request(app.icon_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                icon_file = _store_dir() / "icons" / f"{app.slug}.png"
                icon_file.parent.mkdir(parents=True, exist_ok=True)
                icon_file.write_bytes(r.read(4 << 20))
                icon = str(icon_file)
        except (urllib.error.URLError, OSError):
            pass
    menu = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
    menu.mkdir(parents=True, exist_ok=True)
    entry = menu / f"jacestore-{re.sub(r'[^a-z0-9-]', '-', app.slug.lower())}.desktop"
    ex = str(dest).replace('"', '\\"')
    entry.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={app.title}", f"Comment={app.summary[:200]}",
        f'Exec=env APPIMAGELAUNCHER_DISABLE=1 "{ex}" %U', f"Path={dest.parent}", f"Icon={icon}",
        "Terminal=false", "Categories=Game;" if app.extra.get("type") == "game" else "Categories=Utility;",
        "X-JaceStore=true", ""]), encoding="utf-8")
    entry.chmod(0o755)
    return {"kind": "appimage", "paths": [str(dest), str(entry)], "launch": str(dest), "desktop_file": str(entry)}


def uninstall(app: App) -> str:
    rec = record_of(app)
    if app.source == "flathub":
        _run(["flatpak", "uninstall", "-y", "--noninteractive", app.id], "Flatpak couldn't remove it")
    elif app.source == "distro":
        _run(["pkexec", "pacman", "-R", "--noconfirm", app.id], "pacman couldn't remove it")
    elif rec:
        kind = rec.get("kind")
        if kind == "package" and rec.get("package"):
            _run(["pkexec", "pacman", "-R", "--noconfirm", rec["package"]], "pacman couldn't remove it")
        elif kind == "flatpak" and rec.get("package"):
            _run(["flatpak", "uninstall", "-y", "--noninteractive", rec["package"]], "Flatpak couldn't remove it")
        else:
            for p in rec.get("paths", []):
                Path(p).unlink(missing_ok=True)
    _drop(app.source, app.id)
    return f"Removed {app.title}"


def launch_command(app: App) -> list[str] | None:
    if app.source == "jacestore" and app.slug in BUNDLED:
        return [BUNDLED[app.slug]]
    if app.source == "flathub":
        return ["flatpak", "run", app.id]
    rec = record_of(app) or {}
    if rec.get("kind") == "appimage" and rec.get("launch"):
        return [rec["launch"]]
    if rec.get("kind") == "flatpak" and rec.get("launch"):
        return ["flatpak", "run", rec["launch"]]
    return None


def from_record(rec: dict) -> App:
    return App(rec.get("source", ""), rec.get("id", ""), rec.get("title") or rec.get("id", ""),
               rec.get("summary", ""), rec.get("icon_url"), rec.get("version_number", ""), rec.get("slug", ""))


def open_in_store(app: App | None = None):
    url = []
    if app and app.source == "jacestore" and app.slug:
        url = [f"jacestore://project/{app.slug}"]
    subprocess.Popen(["jace-store", *url], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- the Jace OS apps in Jace Store's My apps ---------------------------------------------------------

def _find_icon(name: str | None) -> str | None:
    """An icon name from a .desktop file -> a picture file Jace Store can show."""
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    for root in (Path("/usr/share/icons/hicolor"), Path("/usr/share/icons/Papirus"), Path("/usr/share/icons/Adwaita")):
        for size in ("256x256", "scalable", "128x128", "64x64", "48x48"):
            for ext in (".png", ".svg"):
                f = root / size / "apps" / f"{name}{ext}"
                if f.exists():
                    return str(f)
    for ext in (".png", ".svg"):
        if Path(f"/usr/share/pixmaps/{name}{ext}").exists():
            return f"/usr/share/pixmaps/{name}{ext}"
    return None


def seed_my_apps(app_ids: list[str]) -> int:
    """Adds the apps that come with Jace OS to Jace Store's My apps (your own library there,
    never uploaded) as custom apps. Each one is added once: if you delete it from My apps,
    it stays deleted. Returns how many were added."""
    import configparser
    import uuid
    from jaceos import config_dir

    done_file = config_dir() / "my-apps-seeded"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    lib_file = _store_dir() / "library.json"
    try:
        library = json.loads(lib_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        library = []
    have = {a.get("system_id") for a in library}
    added = 0
    for app_id in app_ids:
        path = next((d / app_id for d in (Path("/usr/share/applications"), Path("/usr/local/share/applications"))
                     if (d / app_id).exists()), None)
        if app_id in done or path is None:
            continue
        done.add(app_id)
        if app_id in have:
            continue
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
            e = cp["Desktop Entry"]
        except (configparser.Error, KeyError, OSError):
            continue
        library.append({"id": uuid.uuid4().hex[:12], "name": e.get("Name", path.stem), "kind": "desktop",
                        "target": str(path), "args": "", "icon": _find_icon(e.get("Icon")), "website": "",
                        "notes": e.get("Comment", ""), "origin": "jace-os", "system_id": app_id,
                        "added_at": time.time()})
        added += 1
    if added:
        tmp = lib_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(library, indent=2), encoding="utf-8")
        tmp.replace(lib_file)
    done_file.write_text("\n".join(sorted(done)) + "\n")
    return added


def main():
    """jace-my-apps: put the Jace OS apps in Jace Store's My apps (run at sign-in)."""
    from jaceos import menu
    ids = list(dict.fromkeys(menu.DEFAULTS["pinned"] + ["jace.desktop", "jace-welcome.desktop"]))
    seed_my_apps(ids)
