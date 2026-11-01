"""Jace OS virus protection (ClamAV): status, scans, quarantine and alerts.

The system side:
  clamd + clamonacc   real-time protection: files in home folders are scanned when
                      they're created, downloaded or opened; threats are quarantined
  freshclam           keeps the virus signatures up to date
  jace-av-scan.timer  scans every home folder once a day
  jace-av             the root helper Settings uses to change all that

This module is the user side: Settings → Virus protection, scans you start yourself
(your own quarantine is ~/.local/share/jace-os/quarantine), and the alerts.

  jace-av-notify    shows an alert whenever a threat is found (started at sign-in)
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from jaceos import data_dir, ok, run, which

EVENTS = Path("/var/lib/jace-os/av/events.log")
LAST_SCAN = Path("/var/lib/jace-os/av/last-scan.json")
SOCKET = Path("/run/clamav/clamd.ctl")
SIGNATURES = Path("/var/lib/clamav")
HELPER = "/usr/bin/jace-av"


def quarantine_dir() -> Path:
    d = data_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _scanning_flag() -> Path:
    return data_dir() / "av-scanning"


# --- status ---------------------------------------------------------------------------------------

def installed() -> bool:
    return which("clamdscan") or which("clamscan")


def realtime_enabled() -> bool:
    return ok(["systemctl", "is-enabled", "--quiet", "clamav-clamonacc.service"])


def realtime_running() -> bool:
    return ok(["systemctl", "is-active", "--quiet", "clamav-clamonacc.service"])


def autoscan_enabled() -> bool:
    return ok(["systemctl", "is-enabled", "--quiet", "jace-av-scan.timer"])


def signatures() -> dict:
    """{"have": bool, "version": "27812", "date": "Thu Oct 29 2026", "age_days": 1.2}"""
    files = list(SIGNATURES.glob("daily.c[vl]d"))
    if not files:
        return {"have": False}
    out = run(["clamscan", "--version"]).strip()      # ClamAV 1.5.4/27812/Thu Oct 29 07:39:02 2026
    m = re.match(r"ClamAV [^/]+/(\d+)/(.+)$", out)
    age = (time.time() - max(f.stat().st_mtime for f in files)) / 86400
    return {"have": True, "version": m.group(1) if m else "", "date": m.group(2) if m else "",
            "age_days": age}


def last_scheduled_scan() -> dict | None:
    try:
        return json.loads(LAST_SCAN.read_text())
    except (OSError, ValueError):
        return None


def events(limit: int = 50) -> list[dict]:
    """Threats clamd found (real-time, scheduled or from Settings), newest first."""
    try:
        lines = EVENTS.read_text(errors="ignore").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _dismissed_file() -> Path:
    return data_dir() / "av-dismissed.json"


def _event_key(ev: dict) -> str:
    return f"{ev.get('time')}|{ev.get('file')}"


def dismissed(ev: dict) -> bool:
    """Detections you've dealt with (deleted, quarantined, restored or ignored) leave the list."""
    try:
        return _event_key(ev) in json.loads(_dismissed_file().read_text())
    except (OSError, ValueError):
        return False


def dismiss(ev: dict):
    try:
        done = json.loads(_dismissed_file().read_text())
    except (OSError, ValueError):
        done = []
    done.append(_event_key(ev))
    _dismissed_file().write_text(json.dumps(done[-500:]))


def system_quarantine() -> dict[str, dict]:
    """What real-time protection and scheduled scans quarantined: name -> {size, time}."""
    r = subprocess.run(["pkexec", HELPER, "quarantine"], capture_output=True, text=True)
    items = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            items[parts[0]] = {"size": int(parts[1]), "time": float(parts[2])}
    return items


def helper(*args: str) -> tuple[bool, str]:
    r = subprocess.run(["pkexec", HELPER, *args], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


# --- scanning -------------------------------------------------------------------------------------

def quick_scan_paths() -> list[str]:
    home = Path.home()
    names = ["Downloads", "Desktop", "Documents", "Applications", ".local/share/applications", ".config/autostart"]
    xdg = run(["xdg-user-dir", "DOWNLOAD"]).strip()
    paths = {str(home / n) for n in names if (home / n).exists()}
    if xdg and Path(xdg).exists():
        paths.add(xdg)
    return sorted(paths)


def scan(paths: list[str], on_found=None, on_progress=None) -> dict:
    """Scans files and folders as you. Returns {"found": [{"file", "virus"}], "scanned": n, "seconds": s}.
    Uses the running clamd when real-time protection is on (fast), otherwise clamscan."""
    flag = _scanning_flag()
    flag.touch()
    start = time.time()
    if SOCKET.exists() and which("clamdscan"):
        cmd = ["clamdscan", "--fdpass", "--multiscan", "--infected", "--stdout", *paths]
    else:
        if not list(SIGNATURES.glob("daily.c[vl]d")):
            flag.unlink(missing_ok=True)
            raise RuntimeError("There are no virus signatures yet. Turn on real-time protection or "
                               "click Update signatures first.")
        cmd = ["clamscan", "--recursive", "--infected", "--stdout", *paths]
    found, scanned, errors = [], 0, []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.rstrip()
            m = re.match(r"^(.*): (.+) FOUND$", line)
            if m:
                item = {"file": m.group(1), "virus": m.group(2)}
                found.append(item)
                if on_found:
                    on_found(item)
            m = re.match(r"^Scanned files: (\d+)", line)
            if m:
                scanned = int(m.group(1))
            elif line.startswith(("ERROR:", "LibClamAV Error:")) or line.endswith(" ERROR"):
                errors.append(line.split("ERROR:", 1)[-1].strip())
            elif on_progress and line.startswith("/"):
                on_progress(line)
        proc.wait()
    finally:
        flag.unlink(missing_ok=True)
    # Exit code 2 means ClamAV hit an error; only fail if it didn't scan anything at all.
    if proc.returncode == 2 and not found and not scanned:
        raise RuntimeError("The scan didn't run: " + (errors[-1] if errors else "ClamAV hit an error."))
    return {"found": found, "scanned": scanned, "seconds": time.time() - start, "errors": errors}


# --- your quarantine ------------------------------------------------------------------------------

def quarantine(file: str, virus: str) -> Path:
    """Moves a file you own into your quarantine, where it can't run (no permissions)."""
    src = Path(file)
    dest = quarantine_dir() / f"{int(time.time())}-{src.name}"
    shutil.move(str(src), dest)
    dest.chmod(0o000)
    meta = dest.with_name(dest.name + ".json")
    meta.write_text(json.dumps({"original": str(src), "virus": virus, "time": time.time()}))
    return dest


def user_quarantine() -> list[dict]:
    items = []
    for meta in sorted(quarantine_dir().glob("*.json"), reverse=True):
        try:
            info = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        info["path"] = str(meta.with_suffix(""))
        if Path(info["path"]).exists():
            items.append(info)
    return items


def restore(item: dict) -> str:
    path, original = Path(item["path"]), Path(item["original"])
    original.parent.mkdir(parents=True, exist_ok=True)
    path.chmod(0o600)
    shutil.move(str(path), original)
    Path(item["path"] + ".json").unlink(missing_ok=True)
    return str(original)


def delete(item: dict):
    path = Path(item["path"])
    path.chmod(0o600)
    path.unlink(missing_ok=True)
    Path(item["path"] + ".json").unlink(missing_ok=True)


# --- alerts ---------------------------------------------------------------------------------------

def notify_main():
    """jace-av-notify: alerts for threats found by real-time protection and scheduled scans."""
    home = str(Path.home())
    try:
        pos = EVENTS.stat().st_size
    except OSError:
        pos = 0
    while True:
        time.sleep(3)
        try:
            size = EVENTS.stat().st_size
        except OSError:
            continue
        if size < pos:      # the log was cleared
            pos = 0
        if size == pos:
            continue
        with open(EVENTS, errors="ignore") as f:
            f.seek(pos)
            new = f.read()
            pos = f.tell()
        # Scans you start in Settings show their own results.
        if _scanning_flag().exists():
            continue
        for line in new.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not ev.get("file", "").startswith(home):
                continue
            threading.Thread(target=_alert, args=(ev,), daemon=True).start()


def _alert(ev: dict):
    name = os.path.basename(ev["file"])
    r = subprocess.run(["notify-send", "-a", "Virus protection", "-u", "critical", "-i", "security-low",
                        "-A", "open=Open Virus protection",
                        "Threat found and quarantined",
                        f"{name} had {ev.get('virus', 'a virus')}. Jace OS moved it to quarantine."],
                       capture_output=True, text=True)
    if r.stdout.strip() == "open":
        subprocess.Popen(["jace-settings", "--page", "security"], start_new_session=True)
