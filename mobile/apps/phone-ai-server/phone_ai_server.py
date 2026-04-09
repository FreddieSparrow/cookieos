#!/usr/bin/env python3
"""
CookieOS Phone AI Server
Runs Gemma 2B on Android (via Termux/Ollama-Android or MediaPipe)
and exposes an Ollama-compatible HTTP API over the local network,
so CookieOS desktops and CookieAI apps can use the phone as an AI server.

Architecture:
  Phone (Android)         →  Phone AI Server (port 11434)
  CookieOS Desktop/App    →  Connects to http://<phone-ip>:11434

The phone acts as a private AI inference node — no cloud, just local Wi-Fi/USB.

Install on Android (Termux):
  pkg install python ollama
  python phone_ai_server.py --start

Connect from desktop:
  OLLAMA_HOST=http://192.168.1.x:11434 cookiechat --chat
  Or in CookieAI App: Settings → Ollama host → http://192.168.1.x:11434
"""

import os
import sys
import json
import socket
import threading
import subprocess
import time
import logging
import argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

log = logging.getLogger("cookieos.phone-ai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHONE_MODEL   = "gemma3:2b"          # ~1.7GB — fits on most Android phones
OLLAMA_HOST   = "127.0.0.1"
OLLAMA_PORT   = 11434
PROXY_HOST    = "0.0.0.0"           # Listen on all interfaces (LAN accessible)
PROXY_PORT    = 11434
DISCOVERY_PORT = 5353               # mDNS-style discovery port

# ── Device detection (Android/Termux) ────────────────────────────────────────

def is_android() -> bool:
    return (
        os.path.exists("/data/data/com.termux") or
        "ANDROID_ROOT" in os.environ or
        sys.platform == "android"
    )


def get_device_name() -> str:
    if is_android():
        try:
            return subprocess.check_output(
                ["getprop", "ro.product.model"], stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            return "Android Phone"
    return socket.gethostname()


def get_local_ip() -> str:
    """Get local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def check_ram_mb() -> int:
    """Check available RAM on Android/Linux."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 2048  # Conservative default


# ── Ollama management ─────────────────────────────────────────────────────────

def ensure_ollama():
    """Ensure Ollama is installed (Termux or system)."""
    if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
        log.info("Installing Ollama...")
        if is_android():
            # Termux package
            subprocess.run(["pkg", "install", "-y", "ollama"], check=True)
        else:
            subprocess.run(
                "curl -fsSL https://ollama.com/install.sh | sh",
                shell=True, check=True
            )


def ensure_model():
    """Pull Gemma 2B if not present."""
    result = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True
    )
    if PHONE_MODEL.split(":")[0] not in result.stdout:
        ram_mb = check_ram_mb()
        log.info("Available RAM: %d MB", ram_mb)

        if ram_mb < 1800:
            log.warning(
                "Low RAM (%d MB). Gemma 2B needs ~2GB free. "
                "Close other apps and retry.", ram_mb
            )

        log.info("Pulling %s (~1.7GB)...", PHONE_MODEL)
        subprocess.run(["ollama", "pull", PHONE_MODEL], check=True)
        log.info("Model ready: %s", PHONE_MODEL)
    else:
        log.info("Model already installed: %s", PHONE_MODEL)


def start_ollama_server() -> subprocess.Popen:
    """Start Ollama server bound to localhost."""
    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{OLLAMA_HOST}:{OLLAMA_PORT}"
    env["OLLAMA_KEEP_ALIVE"] = "10m"    # Keep model loaded for 10 mins
    env["OLLAMA_NUM_THREAD"]  = "4"     # Limit CPU threads on mobile

    log.info("Starting Ollama server on %s:%d", OLLAMA_HOST, OLLAMA_PORT)
    proc = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    log.info("Ollama server PID: %d", proc.pid)
    return proc


# ── Proxy handler (adds CORS + device info headers) ──────────────────────────

class PhoneAIProxy(BaseHTTPRequestHandler):
    """
    Transparent proxy from LAN → Ollama localhost.
    Adds CookieOS discovery headers so clients can identify the device.
    """

    OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"

    def log_message(self, fmt, *args):
        log.debug("[proxy] " + fmt, *args)

    def _add_cookieos_headers(self):
        self.send_header("X-CookieOS-Device",  get_device_name())
        self.send_header("X-CookieOS-Model",   PHONE_MODEL)
        self.send_header("X-CookieOS-Version", "1.0.0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._add_cookieos_headers()
        self.end_headers()

    def _proxy(self, method: str):
        import urllib.request
        import urllib.error

        url = self.OLLAMA_URL + self.path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else None

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, val)
                self._add_cookieos_headers()
                self.end_headers()

                # Stream response
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()

        except urllib.error.URLError as e:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self._add_cookieos_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):  self._proxy("GET")
    def do_POST(self): self._proxy("POST")


# ── LAN discovery beacon ──────────────────────────────────────────────────────

def run_discovery_beacon():
    """
    Broadcasts a simple UDP beacon so CookieOS devices can auto-discover
    the phone AI server on the local network.
    Packet format: JSON { "service": "cookieos-ai", "host": ip, "port": port, "model": model }
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    payload = json.dumps({
        "service": "cookieos-ai",
        "host":    get_local_ip(),
        "port":    PROXY_PORT,
        "model":   PHONE_MODEL,
        "device":  get_device_name(),
        "version": "1.0.0",
    }).encode()

    log.info("Discovery beacon started — broadcasting every 5s")
    while True:
        try:
            sock.sendto(payload, ("<broadcast>", DISCOVERY_PORT))
        except Exception:
            pass
        time.sleep(5)


def scan_for_phone_ai(timeout: float = 5.0) -> list[dict]:
    """
    Scan the LAN for CookieOS phone AI servers.
    Call this from CookieOS desktop or CookieAI app.
    Returns list of discovered servers.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    try:
        sock.bind(("", DISCOVERY_PORT))
    except OSError:
        return []

    found = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(1024)
            info = json.loads(data)
            if info.get("service") == "cookieos-ai":
                info["source_ip"] = addr[0]
                if info not in found:
                    found.append(info)
        except (socket.timeout, json.JSONDecodeError):
            break

    sock.close()
    return found


# ── Main ──────────────────────────────────────────────────────────────────────

def start_server():
    ensure_ollama()
    ensure_model()

    ollama_proc = start_ollama_server()
    local_ip    = get_local_ip()

    print(f"\n🍪 CookieOS Phone AI Server")
    print(f"   Device:  {get_device_name()}")
    print(f"   Model:   {PHONE_MODEL}")
    print(f"   Listen:  {PROXY_HOST}:{PROXY_PORT}")
    print(f"\n   Connect from CookieOS / CookieAI App:")
    print(f"     Ollama host: http://{local_ip}:{PROXY_PORT}")
    print(f"\n   Or let CookieOS auto-discover (UDP broadcast on port {DISCOVERY_PORT})")
    print(f"\n   Press Ctrl+C to stop.\n")

    # Discovery beacon thread
    beacon = threading.Thread(target=run_discovery_beacon, daemon=True)
    beacon.start()

    # Proxy server
    server = HTTPServer((PROXY_HOST, PROXY_PORT), PhoneAIProxy)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        ollama_proc.terminate()


def scan_command():
    print("Scanning for CookieOS Phone AI servers on LAN...")
    servers = scan_for_phone_ai(timeout=5.0)
    if servers:
        print(f"\nFound {len(servers)} server(s):\n")
        for s in servers:
            print(f"  {s['device']}")
            print(f"    URL:   http://{s['host']}:{s['port']}")
            print(f"    Model: {s['model']}")
            print()
    else:
        print("No servers found. Make sure the phone server is running.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CookieOS Phone AI Server")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("start", help="Start the AI server (run on phone)")
    sub.add_parser("scan",  help="Scan LAN for phone AI servers (run on desktop/app)")

    args = parser.parse_args()

    if args.cmd == "scan":
        scan_command()
    else:
        start_server()
