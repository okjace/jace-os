"""Publish a Jace OS GitHub release on Jace Store, as links to the GitHub downloads
(the ISO is too big to upload, and stays on GitHub).

    python3 tools/publish-store.py v2026.10.30 [--dry-run]

Creates the "jace-os" project on Jace Store the first time. Needs Node and a Jace Store
token (JACE_STORE_TOKEN; create one at https://jace-store-deb.vercel.app/dashboard/tokens).
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "okjace/jace-os"
SLUG = "jace-os"
SITE = os.environ.get("JACE_STORE_URL", "https://jace-store-deb.vercel.app")
CLI = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "jace-store.mjs"
ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ("An Arch-based Linux with Windows-style windows, a start menu on the Windows key, "
           "virus protection, automatic updates, and the Jace apps built in.")


def cli(*args: str, check=True) -> dict:
    res = subprocess.run(["node", str(CLI), *args, "--json"], capture_output=True, text=True)
    out = {}
    if res.stdout.strip().startswith(("{", "[")):
        out = json.loads(res.stdout)
    if check and res.returncode != 0:
        sys.exit(f"jace-store {args[0]} failed: {out.get('error') if isinstance(out, dict) else ''} "
                 f"{res.stderr.strip()}")
    return out if res.returncode == 0 else {"_failed": True, **(out if isinstance(out, dict) else {})}


def release(tag: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "jace-os-publish"})
    if os.environ.get("GH_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    tag = args[0] if args[0].startswith("v") else f"v{args[0]}"
    version = tag[1:]
    dry = "--dry-run" in sys.argv

    rel = release(tag)
    assets = sorted(rel.get("assets", []), key=lambda a: a["name"])
    isos, sums = [], None
    for a in assets:
        name = a["name"]
        if name.endswith(".iso"):
            isos.append((a["browser_download_url"], f"Jace OS {version} (ISO)"))
        elif ".iso.part" in name:
            isos.append((a["browser_download_url"], f"ISO part {int(name.rsplit('part', 1)[1]) + 1} of "
                                                    f"{sum('.iso.part' in x['name'] for x in assets)}"))
        elif name == "SHA256SUMS":
            sums = a["browser_download_url"]
    if not isos or not sums:
        sys.exit(f"{tag}: the GitHub release needs the ISO and SHA256SUMS")
    print(f"{tag}: {len(isos)} ISO download(s)")
    if dry:
        print("  " + "\n  ".join(u for u, _l in isos))
        return

    urllib.request.urlretrieve(f"{SITE}/jace-store.mjs", CLI)
    project = cli("project", SLUG, check=False)
    if project.get("_failed"):
        print("Creating the Jace OS project on Jace Store")
        cli("create", "--type", "app", "--slug", SLUG, "--title", "Jace OS", "--summary", SUMMARY,
            "--description-file", str(ROOT / "README.md"), "--source-url", f"https://github.com/{REPO}",
            "--issues-url", f"https://github.com/{REPO}/issues", "--license", "MIT")
        project = cli("project", SLUG)
    existing = next((v for v in project.get("versions", []) if v.get("version_number") == version), None)
    if existing and any(".iso" in (f.get("url") or f.get("filename") or "") for f in existing.get("files", [])):
        print(f"{tag}: already on Jace Store")
        return

    # Jace Store downloads and hashes each linked file, which times out for a 2 GB ISO.
    # So the version starts with the tiny SHA256SUMS link (hashed as usual), and the ISO
    # links are added with --skip-hash: SHA256SUMS is what people check the ISO with.
    if existing:
        version_id = existing["id"]
    else:
        out = cli("publish", SLUG, "--version", version, "--name", f"Jace OS {version}", "--loaders", "linux",
                  "--changelog", f"{rel.get('html_url', '')}\n\n{rel.get('body', '')}"[:10000],
                  "--install-mode", "open", "--link", f"{sums}|Checksums (SHA256SUMS)")
        version_id = out["id"]
    for i, (url, label) in enumerate(isos):
        cli("add-link", version_id, url, "--label", label, "--skip-hash", "--install-mode", "open",
            "--platform", "linux", *(["--primary"] if i == 0 else []))
    print(f"  published: {SITE}/project/{SLUG}")


if __name__ == "__main__":
    main()
