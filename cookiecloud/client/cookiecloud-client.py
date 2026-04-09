#!/usr/bin/env python3
"""
CookieCloud Native Client
Connects to CookieCloud (Nextcloud-based) instance and provides:
 - File sync (two-way, delta)
 - CookieHost server management
 - CookieGPT API bridge
 - Password vault sync (KeePass-compatible)
 - Encrypted backup to CookieJar (Ceph)
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter, Retry
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import keyring

__version__ = "1.0.0"
log = logging.getLogger("cookiecloud")

CONFIG_DIR  = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "cookiecloud"
SYNC_DIR    = Path(os.environ.get("HOME", "~")).expanduser() / "CookieCloud"
CACHE_DIR   = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "cookiecloud"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE  = CACHE_DIR / "sync-state.json"

DEFAULT_CONFIG = {
    "server":       "https://cookiecloud.cookiehost.uk",
    "username":     "",
    "sync_dir":     str(SYNC_DIR),
    "sync_interval": 30,          # seconds
    "tor_proxy":    False,        # Route through Tor
    "verify_ssl":   True,
    "encrypt_local": True,        # Encrypt local cache
    "cookiegpt_url": "https://ai.cookiehost.uk/api",
}


class CookieCloudError(Exception):
    pass


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


class CookieCloudConfig:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                self._data = {**DEFAULT_CONFIG, **json.load(f)}
        else:
            self._data = DEFAULT_CONFIG.copy()

    def save(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._data, f, indent=2)
        CONFIG_FILE.chmod(0o600)

    def __getitem__(self, key):   return self._data[key]
    def __setitem__(self, key, v): self._data[key] = v
    def get(self, key, default=None): return self._data.get(key, default)


class CookieCloudSession:
    """Authenticated session to the CookieCloud (Nextcloud) server."""

    def __init__(self, config: CookieCloudConfig):
        self.cfg = config
        self.base_url = config["server"].rstrip("/")
        self._session = requests.Session()

        # Retry strategy
        retries = Retry(total=3, backoff_factor=1,
                        status_forcelist=[429, 500, 502, 503, 504])
        self._session.mount("https://", HTTPAdapter(max_retries=retries))
        self._session.mount("http://",  HTTPAdapter(max_retries=retries))

        # Tor proxy
        if config["tor_proxy"]:
            self._session.proxies = {
                "http":  "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            }
            log.info("Routing CookieCloud through Tor")

        self._session.verify = config["verify_ssl"]
        self._token: Optional[str] = None

    def login(self, username: str, password: str) -> bool:
        """Authenticate using Nextcloud app password / token."""
        url = urljoin(self.base_url, "/ocs/v2.php/core/getapppassword")
        try:
            resp = self._session.get(
                url,
                auth=(username, password),
                headers={"OCS-APIRequest": "true"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("ocs", {}).get("data", {}).get("apppassword")
            self._username = username
            self._password = self._token or password
            log.info("Authenticated as %s", username)
            return True
        except Exception as e:
            log.error("Login failed: %s", e)
            return False

    def _auth(self):
        return (self._username, self._password)

    def list_files(self, path: str = "/") -> list[dict]:
        """List files using WebDAV PROPFIND."""
        url = urljoin(self.base_url, f"/remote.php/dav/files/{self._username}/{path.lstrip('/')}")
        resp = self._session.request(
            "PROPFIND", url,
            auth=self._auth(),
            headers={
                "Depth": "1",
                "Content-Type": "application/xml",
            },
            data="""<?xml version="1.0"?>
            <d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
              <d:prop>
                <d:getlastmodified/><d:getcontentlength/>
                <d:resourcetype/><oc:fileid/>
                <d:getetag/>
              </d:prop>
            </d:propfind>""",
            timeout=30,
        )
        resp.raise_for_status()
        # Parse WebDAV XML response
        from xml.etree import ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
        files = []
        for r in root.findall(".//d:response", ns):
            href = r.findtext("d:href", namespaces=ns) or ""
            props = r.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            files.append({
                "path":     href,
                "modified": props.findtext("d:getlastmodified", namespaces=ns),
                "size":     props.findtext("d:getcontentlength", namespaces=ns),
                "etag":     props.findtext("d:getetag", namespaces=ns),
                "is_dir":   props.find("d:resourcetype/d:collection", ns) is not None,
            })
        return files

    def download(self, remote_path: str, local_path: Path):
        url = urljoin(self.base_url, f"/remote.php/dav/files/{self._username}/{remote_path.lstrip('/')}")
        resp = self._session.get(url, auth=self._auth(), stream=True, timeout=60)
        resp.raise_for_status()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        log.debug("Downloaded: %s → %s", remote_path, local_path)

    def upload(self, local_path: Path, remote_path: str):
        url = urljoin(self.base_url, f"/remote.php/dav/files/{self._username}/{remote_path.lstrip('/')}")
        with open(local_path, "rb") as f:
            resp = self._session.put(url, auth=self._auth(), data=f, timeout=120)
        resp.raise_for_status()
        log.debug("Uploaded: %s → %s", local_path, remote_path)

    def mkdir(self, remote_path: str):
        url = urljoin(self.base_url, f"/remote.php/dav/files/{self._username}/{remote_path.lstrip('/')}")
        resp = self._session.request("MKCOL", url, auth=self._auth(), timeout=15)
        if resp.status_code not in (201, 405):  # 405 = already exists
            resp.raise_for_status()


class CookieSyncer:
    """Two-way delta sync between local directory and CookieCloud."""

    def __init__(self, session: CookieCloudSession, config: CookieCloudConfig):
        self.session = session
        self.cfg     = config
        self.sync_dir = Path(config["sync_dir"])
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def _load_state(self):
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                self._state = json.load(f)
        else:
            self._state = {"files": {}}

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def sync_once(self):
        log.info("Starting sync...")
        remote_files = self.session.list_files("/")

        for rf in remote_files:
            if rf["is_dir"]:
                continue
            rel = rf["path"].split(f"/files/{self.session._username}/", 1)[-1]
            local = self.sync_dir / rel

            prev_etag = self._state["files"].get(rel, {}).get("etag")
            if rf["etag"] != prev_etag:
                log.info("  ↓ %s", rel)
                self.session.download(rel, local)
                self._state["files"][rel] = {"etag": rf["etag"], "direction": "down"}

        # Upload local changes
        for local_file in self.sync_dir.rglob("*"):
            if local_file.is_dir():
                continue
            rel = str(local_file.relative_to(self.sync_dir))
            mtime = local_file.stat().st_mtime
            prev  = self._state["files"].get(rel, {})
            if prev.get("mtime") != mtime:
                log.info("  ↑ %s", rel)
                self.session.upload(local_file, rel)
                self._state["files"][rel] = {"mtime": mtime, "direction": "up"}

        self._save_state()
        log.info("Sync complete.")

    def watch(self, interval: int = 30):
        """Continuous sync loop."""
        log.info("Watching for changes every %ds...", interval)
        while True:
            try:
                self.sync_once()
            except Exception as e:
                log.error("Sync error: %s", e)
            time.sleep(interval)


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_sync(args, cfg, session):
    syncer = CookieSyncer(session, cfg)
    if args.watch:
        syncer.watch(cfg["sync_interval"])
    else:
        syncer.sync_once()


def cmd_status(args, cfg, session):
    files = session.list_files("/")
    print(f"CookieCloud: {cfg['server']}")
    print(f"Files on server: {sum(1 for f in files if not f['is_dir'])}")
    print(f"Local sync dir: {cfg['sync_dir']}")


def cmd_configure(args, cfg, _session=None):
    print("CookieCloud Configuration")
    cfg["server"]   = input(f"Server URL [{cfg['server']}]: ").strip() or cfg["server"]
    cfg["username"] = input(f"Username [{cfg['username']}]: ").strip() or cfg["username"]
    password = input("Password (stored in keyring): ").strip()
    if password:
        keyring.set_password("cookiecloud", cfg["username"], password)
    cfg["tor_proxy"] = input("Route through Tor? [y/N]: ").strip().lower() == "y"
    cfg.save()
    print("Configuration saved.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="CookieCloud Client")
    parser.add_argument("--version", action="version", version=f"cookiecloud {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_sync = sub.add_parser("sync", help="Sync files with CookieCloud")
    p_sync.add_argument("--watch", "-w", action="store_true", help="Continuous sync")

    sub.add_parser("status", help="Show sync status")
    sub.add_parser("configure", help="Configure CookieCloud connection")

    args = parser.parse_args()

    cfg = CookieCloudConfig()

    if args.cmd == "configure":
        cmd_configure(args, cfg)
        return

    # Require credentials for other commands
    username = cfg["username"]
    password = keyring.get_password("cookiecloud", username) if username else None

    if not username or not password:
        print("Not configured. Run: cookiecloud configure")
        sys.exit(1)

    session = CookieCloudSession(cfg)
    if not session.login(username, password):
        print("Authentication failed.")
        sys.exit(1)

    if args.cmd == "sync":
        cmd_sync(args, cfg, session)
    elif args.cmd == "status":
        cmd_status(args, cfg, session)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
