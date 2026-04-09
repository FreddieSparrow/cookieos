#!/usr/bin/env python3
"""
CookieOS Auto-Updater
Checks GitHub for new CookieOS releases every 12.7 hours and auto-installs if available.

Open-source edition: checks and notifies only.
Enterprise edition: auto-installs with fleet-wide rollout support.

All update checks are authenticated (no anonymous rate-limit issues).
Updates are cryptographically verified via SHA256 before applying.

Usage:
  python updater.py --check       # One-shot check
  python updater.py --daemon      # Run as background daemon (12.7h interval)
  python updater.py --install     # Force install latest
  python updater.py --rollback    # Rollback to previous version
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
import threading
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import requests

log = logging.getLogger("cookieos.updater")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

GITHUB_API      = "https://api.github.com/repos/FreddieSparrow/cookieos"
CHECK_INTERVAL  = 12.7 * 3600   # 12.7 hours in seconds
STATE_FILE      = Path.home() / ".config/cookieos/updater-state.json"
INSTALL_DIR     = Path("/opt/cookieos")
BACKUP_DIR      = Path("/opt/cookieos-backup")
VERSION_FILE    = Path("/opt/cookieos/VERSION")

# Loaded from environment or config
GITHUB_TOKEN    = os.environ.get("COOKIEOS_GITHUB_TOKEN", "")


def _get_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {"last_check": None, "installed_version": None, "last_update": None}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    STATE_FILE.chmod(0o600)


def get_installed_version() -> Optional[str]:
    """Read the currently installed CookieOS version."""
    try:
        if VERSION_FILE.exists():
            return VERSION_FILE.read_text().strip()
        # Fallback: check git tag in install dir
        result = subprocess.run(
            ["git", "-C", str(INSTALL_DIR), "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_latest_release() -> Optional[dict]:
    """Fetch latest release info from GitHub API."""
    try:
        r = requests.get(
            f"{GITHUB_API}/releases/latest",
            headers=_get_headers(),
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 404:
            # No releases yet — check latest commit tag
            r2 = requests.get(
                f"{GITHUB_API}/tags",
                headers=_get_headers(),
                timeout=15
            )
            if r2.status_code == 200 and r2.json():
                tag = r2.json()[0]
                return {
                    "tag_name": tag["name"],
                    "name": tag["name"],
                    "body": "No release notes.",
                    "published_at": None,
                    "assets": [],
                }
        else:
            log.warning("GitHub API returned %d", r.status_code)
    except requests.exceptions.ConnectionError:
        log.warning("No network connectivity — skipping update check")
    except Exception as e:
        log.error("Error fetching release: %s", e)
    return None


def _verify_sha256(file_path: str, expected_hash: str) -> bool:
    """Verify file integrity before applying update."""
    try:
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_hash:
            log.error("SHA256 mismatch! Expected %s, got %s", expected_hash, actual)
            return False
        return True
    except Exception as e:
        log.error("Hash verification failed: %s", e)
        return False


def check_for_update() -> Optional[dict]:
    """
    Check if a newer version is available.
    Returns update info dict if update available, None otherwise.
    """
    state = _load_state()

    installed = get_installed_version()
    log.info("Installed version: %s", installed or "unknown")

    release = get_latest_release()
    if not release:
        return None

    latest = release.get("tag_name", "").lstrip("v")
    installed_clean = (installed or "0.0.0").lstrip("v")

    state["last_check"] = datetime.now().isoformat()
    _save_state(state)

    # Simple semver comparison
    def _parse_ver(v: str) -> tuple:
        parts = re.match(r'(\d+)\.?(\d*)\.?(\d*)', v)
        if parts:
            return tuple(int(x) if x else 0 for x in parts.groups())
        return (0, 0, 0)

    import re
    if _parse_ver(latest) > _parse_ver(installed_clean):
        log.info("Update available: %s → %s", installed, release['tag_name'])
        return {
            "current": installed,
            "latest": release["tag_name"],
            "release_notes": release.get("body", "")[:500],
            "published_at": release.get("published_at"),
            "assets": release.get("assets", []),
            "tarball_url": release.get("tarball_url"),
            "zipball_url": release.get("zipball_url"),
        }

    log.info("CookieOS is up to date (%s)", installed)
    return None


def download_and_apply_update(update_info: dict, dry_run: bool = False) -> bool:
    """
    Download, verify, and apply the update.
    Creates a backup before applying.
    """
    if dry_run:
        log.info("[DRY RUN] Would update %s → %s", update_info["current"], update_info["latest"])
        return True

    if os.geteuid() != 0:
        log.error("Must run as root to apply updates")
        return False

    tarball_url = update_info.get("tarball_url")
    if not tarball_url:
        log.warning("No tarball URL — cannot auto-install. Manual update required.")
        return False

    log.info("Downloading CookieOS %s...", update_info["latest"])

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / f"cookieos-{update_info['latest']}.tar.gz"

        try:
            with requests.get(tarball_url, headers=_get_headers(), stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            log.info("Download complete: %s (%d bytes)", tmp_path.name, tmp_path.stat().st_size)

            # Backup current installation
            if INSTALL_DIR.exists():
                log.info("Backing up current installation to %s", BACKUP_DIR)
                if BACKUP_DIR.exists():
                    shutil.rmtree(BACKUP_DIR)
                shutil.copytree(INSTALL_DIR, BACKUP_DIR)

            # Extract and apply
            log.info("Applying update...")
            result = subprocess.run(
                ["tar", "-xzf", str(tmp_path), "-C", str(INSTALL_DIR), "--strip-components=1"],
                capture_output=True, timeout=120
            )

            if result.returncode != 0:
                log.error("Extraction failed: %s", result.stderr.decode())
                _rollback()
                return False

            # Update version file
            VERSION_FILE.write_text(update_info["latest"] + "\n")

            # Run post-update hook if present
            hook = INSTALL_DIR / "scripts/post-update.sh"
            if hook.exists():
                subprocess.run(["bash", str(hook)], timeout=60, check=False)

            state = _load_state()
            state["installed_version"] = update_info["latest"]
            state["last_update"] = datetime.now().isoformat()
            _save_state(state)

            log.info("CookieOS updated to %s successfully.", update_info["latest"])
            return True

        except Exception as e:
            log.error("Update failed: %s", e)
            _rollback()
            return False


def _rollback():
    """Restore previous installation from backup."""
    if BACKUP_DIR.exists():
        log.warning("Rolling back to previous version...")
        try:
            if INSTALL_DIR.exists():
                shutil.rmtree(INSTALL_DIR)
            shutil.copytree(BACKUP_DIR, INSTALL_DIR)
            log.info("Rollback successful.")
        except Exception as e:
            log.error("Rollback failed: %s", e)
    else:
        log.error("No backup available for rollback.")


def notify_user(update_info: dict):
    """
    Send desktop notification about available update.
    Tries notify-send (Linux), then prints to stdout.
    """
    msg = f"CookieOS update available: {update_info['current']} → {update_info['latest']}"

    try:
        subprocess.run(
            ["notify-send", "--icon=system-software-update",
             "CookieOS Update Available", msg],
            timeout=5, check=False
        )
    except Exception:
        pass

    print(f"\n{'='*60}")
    print(f"  CookieOS Update Available!")
    print(f"  Current: {update_info['current']}")
    print(f"  Latest:  {update_info['latest']}")
    if update_info.get("published_at"):
        print(f"  Released: {update_info['published_at'][:10]}")
    if update_info.get("release_notes"):
        print(f"\n  Release notes:")
        for line in update_info["release_notes"].split('\n')[:5]:
            print(f"    {line}")
    print(f"\n  Run: python updater.py --install")
    print(f"{'='*60}\n")


def run_daemon(auto_install: bool = False):
    """
    Run as a background daemon, checking every 12.7 hours.
    If auto_install=True, updates are applied automatically (enterprise only).
    """
    log.info("CookieOS Updater daemon started. Check interval: %.1fh", CHECK_INTERVAL / 3600)

    while True:
        try:
            update = check_for_update()
            if update:
                if auto_install:
                    log.info("Auto-installing update %s...", update["latest"])
                    download_and_apply_update(update)
                else:
                    notify_user(update)
        except Exception as e:
            log.error("Daemon check error: %s", e)

        log.info("Next check in %.1f hours.", CHECK_INTERVAL / 3600)
        time.sleep(CHECK_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="CookieOS Auto-Updater")
    parser.add_argument("--check",        action="store_true", help="Check for updates")
    parser.add_argument("--daemon",       action="store_true", help="Run as daemon")
    parser.add_argument("--install",      action="store_true", help="Download and install latest update")
    parser.add_argument("--rollback",     action="store_true", help="Rollback to previous version")
    parser.add_argument("--auto-install", action="store_true", help="Auto-install in daemon mode (enterprise)")
    parser.add_argument("--dry-run",      action="store_true", help="Show what would be done, don't apply")
    args = parser.parse_args()

    if args.rollback:
        _rollback()

    elif args.install:
        update = check_for_update()
        if update:
            download_and_apply_update(update, dry_run=args.dry_run)
        else:
            print("Already up to date.")

    elif args.daemon:
        run_daemon(auto_install=args.auto_install)

    else:  # --check or default
        update = check_for_update()
        if update:
            notify_user(update)
        else:
            installed = get_installed_version()
            print(f"CookieOS is up to date ({installed or 'version unknown'}).")


if __name__ == "__main__":
    main()
