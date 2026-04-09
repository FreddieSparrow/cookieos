#!/usr/bin/env python3
"""
CookieShield — CookieOS AI-Powered Self-Defense System
Continuously monitors the system for new threats and uses Ollama (local AI)
to analyse, classify, and generate patches/mitigations on-the-fly.

Capabilities:
  1. Real-time file/process/network anomaly detection
  2. Automated CVE feed monitoring (offline-capable via local cache)
  3. AI-driven threat analysis (Gemma 4 via Ollama — no cloud)
  4. Automatic sysctl/iptables/AppArmor patch generation
  5. Optional automatic application of low-risk patches
  6. CookieCloud threat intelligence sharing (opt-in, anonymised)
  7. Tailscale-distributed fleet alerts

Architecture:
  Monitor threads → AnomalyQueue → AIAnalyser → PatchEngine → Enforcer

Run as root:
  python3 cookie-shield.py --daemon
  python3 cookie-shield.py --status
  python3 cookie-shield.py --scan
"""

import os
import sys
import json
import time
import socket
import hashlib
import logging
import argparse
import threading
import subprocess
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("cookieos.shield")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

STATE_DIR  = Path("/var/lib/cookieos/shield")
LOG_FILE   = Path("/var/log/cookieos/shield.log")
PATCH_DIR  = Path("/var/lib/cookieos/shield/patches")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
SHIELD_MODEL = os.environ.get("COOKIEOS_SHIELD_MODEL", "gemma4:4b")


# ── Threat levels ─────────────────────────────────────────────────────────────

class ThreatLevel(Enum):
    INFO     = 0    # Informational — log only
    LOW      = 1    # Log + alert
    MEDIUM   = 2    # Log + alert + generate patch
    HIGH     = 3    # Log + alert + auto-apply patch + isolate process
    CRITICAL = 4    # Immediate isolation + emergency patch + fleet alert


@dataclass
class Threat:
    id:          str
    level:       ThreatLevel
    category:    str         # "malware" | "exploit" | "anomaly" | "cve" | "rootkit"
    description: str
    evidence:    list[str]   = field(default_factory=list)
    process:     str         = ""
    pid:         int         = 0
    file_path:   str         = ""
    network:     str         = ""
    timestamp:   str         = field(default_factory=lambda: datetime.utcnow().isoformat())
    patched:     bool        = False
    patch_id:    str         = ""


@dataclass
class Patch:
    id:          str
    threat_id:   str
    commands:    list[str]           # Shell commands to apply
    sysctl:      dict[str, str]      # sysctl keys to set
    apparmor:    str                 # AppArmor rule additions
    iptables:    list[str]           # iptables rules
    description: str
    risk_level:  str                 # "safe" | "moderate" | "invasive"
    auto_apply:  bool                # AI determined this is safe to auto-apply
    applied:     bool = False
    timestamp:   str  = field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Anomaly monitors ──────────────────────────────────────────────────────────

class ProcessMonitor:
    """Monitors running processes for suspicious behaviour."""

    SUSPICIOUS_PATTERNS = [
        (r"(curl|wget|nc|ncat|netcat).+\|\s*sh",   "command injection"),
        (r"python.+-c.+(exec|eval|__import__)",     "python code exec"),
        (r"base64\s+-d.+\|\s*(bash|sh)",            "base64 shell exec"),
        (r"chmod\s+[+]?[sx].+/tmp/",               "tmp setuid"),
        (r"(dd|cat)\s+/dev/mem",                   "memory dump"),
        (r"nmap\s+.+-sS",                           "stealth port scan"),
        (r"tcpdump.+(-i\s+any|-w\s+/tmp/)",        "suspicious packet capture"),
        (r"iptables\s+-F",                          "firewall flush"),
        (r"systemctl\s+stop\s+(apparmor|ufw|fail2ban)", "security service stop"),
    ]

    def get_suspicious_processes(self) -> list[Threat]:
        threats = []
        try:
            output = subprocess.check_output(
                ["ps", "axo", "pid,user,cmd", "--no-headers"],
                text=True, timeout=10,
            )
            for line in output.strip().splitlines():
                parts = line.split(None, 2)
                if len(parts) < 3:
                    continue
                pid, user, cmd = parts[0], parts[1], parts[2]

                for pattern, desc in self.SUSPICIOUS_PATTERNS:
                    if re.search(pattern, cmd, re.IGNORECASE):
                        threats.append(Threat(
                            id=f"proc-{pid}-{int(time.time())}",
                            level=ThreatLevel.HIGH,
                            category="anomaly",
                            description=f"Suspicious process: {desc}",
                            evidence=[f"cmd: {cmd[:200]}", f"user: {user}"],
                            process=cmd[:100],
                            pid=int(pid),
                        ))
        except Exception as e:
            log.debug("Process scan error: %s", e)
        return threats


class FileIntegrityMonitor:
    """Monitors critical system files for unexpected changes."""

    CRITICAL_FILES = [
        "/etc/passwd", "/etc/shadow", "/etc/sudoers",
        "/etc/cron.d", "/etc/crontab",
        "/usr/bin/sudo", "/usr/bin/su",
        "/etc/ld.so.preload",     # Rootkit indicator
        "/etc/ld.so.conf.d",
        "/proc/sys/kernel/modules_disabled",
    ]

    def __init__(self):
        self._baseline: dict[str, str] = {}
        self._state_file = STATE_DIR / "fim-baseline.json"
        self._load_baseline()

    def _load_baseline(self):
        if self._state_file.exists():
            self._baseline = json.loads(self._state_file.read_text())

    def _save_baseline(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._baseline, indent=2))

    def _hash_file(self, path: str) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def build_baseline(self):
        log.info("[fim] Building file integrity baseline...")
        for path in self.CRITICAL_FILES:
            if os.path.exists(path):
                h = self._hash_file(path)
                if h:
                    self._baseline[path] = h
        self._save_baseline()
        log.info("[fim] Baseline complete (%d files)", len(self._baseline))

    def check(self) -> list[Threat]:
        threats = []
        for path, expected_hash in self._baseline.items():
            current = self._hash_file(path)
            if current is None:
                threats.append(Threat(
                    id=f"fim-missing-{int(time.time())}",
                    level=ThreatLevel.HIGH,
                    category="rootkit",
                    description=f"Critical file missing: {path}",
                    file_path=path,
                ))
            elif current != expected_hash:
                threats.append(Threat(
                    id=f"fim-modified-{int(time.time())}",
                    level=ThreatLevel.CRITICAL,
                    category="rootkit",
                    description=f"Critical file modified: {path}",
                    evidence=[f"expected: {expected_hash[:16]}",
                               f"current:  {current[:16]}"],
                    file_path=path,
                ))
        return threats


class NetworkAnomalyMonitor:
    """Detects unexpected outbound connections."""

    ALLOWED_PORTS  = {22, 53, 80, 443, 11434, 5678, 9050, 9051}
    SUSPICIOUS_IPS = set()  # Populated from CVE feed

    def check(self) -> list[Threat]:
        threats = []
        try:
            output = subprocess.check_output(
                ["ss", "-tunp", "--no-header"],
                text=True, timeout=5,
            )
            for line in output.strip().splitlines():
                parts = line.split()
                if len(parts) < 6:
                    continue
                state    = parts[1]
                dst      = parts[4]   # dst:port
                proc_info = " ".join(parts[5:])

                # Parse destination port
                try:
                    dst_port = int(dst.rsplit(":", 1)[-1])
                except ValueError:
                    continue

                dst_ip = dst.rsplit(":", 1)[0].strip("[]")

                # Check for suspicious outbound on unexpected ports
                if (state == "ESTAB" and
                    dst_port not in self.ALLOWED_PORTS and
                    not dst_ip.startswith("127.") and
                    not dst_ip.startswith("10.") and
                    not dst_ip.startswith("192.168.") and
                    not dst_ip.startswith("100.")):   # Tailscale range
                    threats.append(Threat(
                        id=f"net-{dst_ip}-{dst_port}-{int(time.time())}",
                        level=ThreatLevel.MEDIUM,
                        category="anomaly",
                        description=f"Unexpected outbound connection to {dst_ip}:{dst_port}",
                        network=f"{dst_ip}:{dst_port}",
                        evidence=[f"process: {proc_info[:100]}"],
                    ))
        except Exception as e:
            log.debug("Network scan error: %s", e)
        return threats


# ── CVE Feed Monitor ──────────────────────────────────────────────────────────

class CVEMonitor:
    """
    Monitors NVD/CVE feeds and flags CVEs affecting installed packages.
    Uses cached data — works offline, updates when network available.
    """

    CACHE_FILE    = STATE_DIR / "cve-cache.json"
    NVD_FEED_URL  = "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=100&pubStartDate="
    UPDATE_INTERVAL = 3600   # Refresh every hour

    def __init__(self):
        self._cache: list[dict] = []
        self._last_update = 0.0
        self._load_cache()

    def _load_cache(self):
        if self.CACHE_FILE.exists():
            try:
                data = json.loads(self.CACHE_FILE.read_text())
                self._cache = data.get("cves", [])
                self._last_update = data.get("timestamp", 0.0)
            except Exception:
                pass

    def _save_cache(self, cves: list[dict]):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.CACHE_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "cves":      cves,
        }))
        self._cache = cves

    def refresh(self):
        if time.time() - self._last_update < self.UPDATE_INTERVAL:
            return
        try:
            import urllib.request
            since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000")
            url   = self.NVD_FEED_URL + since
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read())
                cves = [
                    {
                        "id":          v["cve"]["id"],
                        "description": v["cve"]["descriptions"][0]["value"][:500]
                        if v["cve"].get("descriptions") else "",
                        "severity":    v["cve"].get("metrics", {})
                                       .get("cvssMetricV31", [{}])[0]
                                       .get("cvssData", {})
                                       .get("baseSeverity", "UNKNOWN"),
                        "published":   v["cve"]["published"],
                    }
                    for v in data.get("vulnerabilities", [])
                ]
            self._save_cache(cves)
            log.info("[cve] Updated CVE cache: %d entries", len(cves))
        except Exception as e:
            log.debug("[cve] Could not refresh: %s", e)

    def get_critical_cves(self) -> list[Threat]:
        threats = []
        for cve in self._cache:
            if cve.get("severity") in ("CRITICAL", "HIGH"):
                threats.append(Threat(
                    id=f"cve-{cve['id']}-{int(time.time())}",
                    level=ThreatLevel.HIGH if cve["severity"] == "CRITICAL" else ThreatLevel.MEDIUM,
                    category="cve",
                    description=f"{cve['id']}: {cve['description'][:200]}",
                    evidence=[f"severity: {cve['severity']}", f"published: {cve['published']}"],
                ))
        return threats


# ── AI Threat Analyser ────────────────────────────────────────────────────────

class AIThreatAnalyser:
    """
    Uses Ollama (local AI) to analyse threats and generate patches.
    All analysis is local — no data leaves the device.
    """

    def __init__(self):
        self._available = self._check_ollama()

    def _check_ollama(self) -> bool:
        try:
            import requests
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def analyse_threat(self, threat: Threat) -> str:
        """Ask the AI to explain the threat and suggest mitigations."""
        if not self._available:
            return "AI analysis unavailable (Ollama not running)."

        prompt = f"""You are CookieShield, the CookieOS AI security system.
Analyse this threat and provide a brief technical assessment + top 3 mitigations.
Be concise — max 200 words.

Threat: {threat.category.upper()}
Level:  {threat.level.name}
Description: {threat.description}
Evidence: {'; '.join(threat.evidence[:3])}
File: {threat.file_path or 'N/A'}
Process: {threat.process or 'N/A'}
Network: {threat.network or 'N/A'}

Respond with: ASSESSMENT, then numbered MITIGATIONS. No markdown headers."""

        try:
            import requests
            r = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model":  SHIELD_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 300},
                },
                timeout=60,
            )
            return r.json().get("response", "").strip()
        except Exception as e:
            return f"AI analysis error: {e}"

    def generate_patch(self, threat: Threat) -> Optional[Patch]:
        """Use AI to generate a system patch for a detected threat."""
        if not self._available:
            return None

        prompt = f"""You are CookieShield. Generate a minimal system patch for this threat.
Output ONLY valid JSON — no markdown, no explanation outside the JSON.

Threat: {threat.category} — {threat.description}
Level: {threat.level.name}
File: {threat.file_path or 'none'}
Process: {threat.process or 'none'}
Network: {threat.network or 'none'}

JSON schema (use exactly this structure):
{{
  "description": "one line summary",
  "commands": ["bash command 1", "bash command 2"],
  "sysctl": {{"key": "value"}},
  "iptables": ["iptables -A rule"],
  "apparmor": "additional apparmor rule lines",
  "risk_level": "safe|moderate|invasive",
  "auto_apply": true|false
}}

Rules:
- Only generate commands directly relevant to this specific threat
- risk_level "safe" = sysctl/iptables only; "moderate" = kill process; "invasive" = file changes
- auto_apply=true only for "safe" risk_level
- If no patch is possible/safe, return empty commands/rules
- Never generate commands that would destroy the system or remove critical services"""

        try:
            import requests
            r = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model":  SHIELD_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 600},
                },
                timeout=90,
            )
            response = r.json().get("response", "").strip()

            # Extract JSON from response (AI sometimes adds preamble)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if not json_match:
                log.warning("[shield] AI returned non-JSON patch response")
                return None

            patch_data = json.loads(json_match.group())

            # Safety gate: never auto-apply invasive patches
            if patch_data.get("risk_level") == "invasive":
                patch_data["auto_apply"] = False

            patch = Patch(
                id=f"patch-{threat.id}",
                threat_id=threat.id,
                commands=patch_data.get("commands", []),
                sysctl=patch_data.get("sysctl", {}),
                apparmor=patch_data.get("apparmor", ""),
                iptables=patch_data.get("iptables", []),
                description=patch_data.get("description", ""),
                risk_level=patch_data.get("risk_level", "moderate"),
                auto_apply=bool(patch_data.get("auto_apply", False)),
            )
            return patch

        except Exception as e:
            log.error("[shield] Patch generation error: %s", e)
            return None


# ── Patch Enforcer ────────────────────────────────────────────────────────────

class PatchEnforcer:
    """Applies generated patches to the live system."""

    def apply(self, patch: Patch) -> bool:
        log.info("[enforcer] Applying patch %s (%s)", patch.id, patch.risk_level)
        PATCH_DIR.mkdir(parents=True, exist_ok=True)

        # Save patch to disk first (audit trail)
        patch_file = PATCH_DIR / f"{patch.id}.json"
        patch_file.write_text(json.dumps({
            "id":          patch.id,
            "threat_id":   patch.threat_id,
            "description": patch.description,
            "risk_level":  patch.risk_level,
            "auto_apply":  patch.auto_apply,
            "applied_at":  datetime.utcnow().isoformat(),
            "commands":    patch.commands,
            "sysctl":      patch.sysctl,
            "iptables":    patch.iptables,
        }, indent=2))

        success = True

        # Apply sysctl
        for key, val in patch.sysctl.items():
            try:
                subprocess.run(["sysctl", "-w", f"{key}={val}"], check=True,
                               capture_output=True)
                log.info("[enforcer]   sysctl %s=%s", key, val)
            except Exception as e:
                log.error("[enforcer]   sysctl error: %s", e)
                success = False

        # Apply iptables rules
        for rule in patch.iptables:
            try:
                subprocess.run(rule.split(), check=True, capture_output=True)
                log.info("[enforcer]   iptables: %s", rule)
            except Exception as e:
                log.error("[enforcer]   iptables error: %s", e)
                success = False

        # Run shell commands (only for safe+moderate patches)
        if patch.risk_level in ("safe", "moderate"):
            for cmd in patch.commands:
                try:
                    subprocess.run(cmd, shell=True, check=True,
                                   timeout=30, capture_output=True)
                    log.info("[enforcer]   cmd: %s", cmd[:80])
                except Exception as e:
                    log.error("[enforcer]   cmd error: %s — %s", cmd[:80], e)
                    success = False

        patch.applied = success
        return success


# ── Main Shield Daemon ────────────────────────────────────────────────────────

class CookieShield:
    def __init__(self, auto_patch: bool = True):
        self.auto_patch   = auto_patch
        self.process_mon  = ProcessMonitor()
        self.fim_mon      = FileIntegrityMonitor()
        self.net_mon      = NetworkAnomalyMonitor()
        self.cve_mon      = CVEMonitor()
        self.ai_analyser  = AIThreatAnalyser()
        self.enforcer     = PatchEnforcer()
        self._threat_log: list[Threat] = []
        self._running     = False

    def scan_once(self) -> list[Threat]:
        threats = []
        threats += self.process_mon.get_suspicious_processes()
        threats += self.fim_mon.check()
        threats += self.net_mon.check()
        self.cve_mon.refresh()
        threats += self.cve_mon.get_critical_cves()
        return threats

    def handle_threat(self, threat: Threat):
        self._threat_log.append(threat)
        log.warning("[shield] THREAT [%s] %s: %s",
                    threat.level.name, threat.category, threat.description)

        # Get AI analysis for medium+ threats
        if threat.level.value >= ThreatLevel.MEDIUM.value:
            analysis = self.ai_analyser.analyse_threat(threat)
            log.info("[shield] AI analysis: %s", analysis[:200])

            # Generate patch
            patch = self.ai_analyser.generate_patch(threat)
            if patch:
                log.info("[shield] Patch generated: %s (risk=%s, auto=%s)",
                         patch.description, patch.risk_level, patch.auto_apply)

                if self.auto_patch and patch.auto_apply:
                    applied = self.enforcer.apply(patch)
                    if applied:
                        threat.patched   = True
                        threat.patch_id  = patch.id
                        log.info("[shield] Patch auto-applied: %s", patch.id)
                    else:
                        log.error("[shield] Patch application failed: %s", patch.id)
                else:
                    log.info("[shield] Patch saved for manual review: %s",
                             PATCH_DIR / f"{patch.id}.json")

        # Critical: also kill the process immediately
        if threat.level == ThreatLevel.CRITICAL and threat.pid:
            try:
                os.kill(threat.pid, 9)
                log.warning("[shield] Killed suspicious process PID %d", threat.pid)
            except Exception:
                pass

    def run_daemon(self, interval: int = 30):
        """Run as a background daemon, scanning every `interval` seconds."""
        self._running = True
        log.info("[shield] CookieShield daemon started (interval=%ds, auto_patch=%s)",
                 interval, self.auto_patch)

        # Build FIM baseline on first run
        if not self.fim_mon._state_file.exists():
            self.fim_mon.build_baseline()

        while self._running:
            try:
                threats = self.scan_once()
                for threat in threats:
                    self.handle_threat(threat)
                if not threats:
                    log.debug("[shield] Scan complete — no threats detected.")
            except Exception as e:
                log.error("[shield] Scan error: %s", e)
            time.sleep(interval)

    def status(self) -> dict:
        return {
            "running":          self._running,
            "ai_available":     self.ai_analyser._available,
            "threats_detected": len(self._threat_log),
            "patched":          sum(1 for t in self._threat_log if t.patched),
            "cve_cache_size":   len(self.cve_mon._cache),
            "fim_baseline_files": len(self.fim_mon._baseline),
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CookieShield — AI-powered self-defense for CookieOS"
    )
    parser.add_argument("--daemon",     action="store_true", help="Run as background daemon")
    parser.add_argument("--scan",       action="store_true", help="Run a single scan")
    parser.add_argument("--status",     action="store_true", help="Show shield status")
    parser.add_argument("--baseline",   action="store_true", help="Rebuild FIM baseline")
    parser.add_argument("--no-autopatch", action="store_true",
                        help="Generate patches but don't apply automatically")
    parser.add_argument("--interval",   type=int, default=30,
                        help="Scan interval in seconds (daemon mode)")
    args = parser.parse_args()

    shield = CookieShield(auto_patch=not args.no_autopatch)

    if args.baseline:
        shield.fim_mon.build_baseline()
        return

    if args.status:
        s = shield.status()
        print(f"\n🛡 CookieShield Status")
        for k, v in s.items():
            print(f"   {k}: {v}")
        return

    if args.scan:
        print("[shield] Running scan...")
        threats = shield.scan_once()
        if threats:
            print(f"\n⚠  {len(threats)} threat(s) detected:")
            for t in threats:
                print(f"   [{t.level.name}] {t.category}: {t.description[:80]}")
                shield.handle_threat(t)
        else:
            print("✓ No threats detected.")
        return

    if args.daemon:
        if os.geteuid() != 0:
            print("Error: daemon mode requires root.")
            sys.exit(1)
        shield.run_daemon(interval=args.interval)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
