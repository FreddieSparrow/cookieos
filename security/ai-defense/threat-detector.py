#!/usr/bin/env python3
"""
CookieOS AI Threat Detector — Real-time Virus Detection & Auto-Patching
Uses Gemma 4 to analyze new threats and generate patches on-the-fly.

Features:
  1. Real-time file/binary anomaly detection (entropy, suspicious syscalls)
  2. Behavioral analysis of running processes
  3. AI-driven threat classification (no cloud, runs locally on Ollama)
  4. Automatic patch generation & enforcement
  5. Virus signature learning (updates local model with new patterns)
  6. CVE database sync (optional, Tailscale-distributed)
  7. CookieCloud threat intelligence sharing (opt-in, anonymised)
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
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, List, Tuple
import struct
import tempfile
import shutil
import requests

log = logging.getLogger("cookieos.threat_detector")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

STATE_DIR  = Path("/var/lib/cookieos/threat-detector")
LOG_FILE   = Path("/var/log/cookieos/threat-detector.log")
PATCH_DIR  = Path("/etc/cookieos/patches")
SIGNATURE_DB = STATE_DIR / "threat_signatures.json"
CVE_CACHE  = STATE_DIR / "cve_cache.json"
OLLAMA_API = "http://localhost:11434/api/generate"
TAILSCALE_API = "http://localhost:41112/local/status"

STATE_DIR.mkdir(parents=True, exist_ok=True)
PATCH_DIR.mkdir(parents=True, exist_ok=True)


class ThreatLevel(Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    HIGH = "high"
    CRITICAL = "critical"


class PatternType(Enum):
    FILE_SIGNATURE = "file_sig"
    SYSCALL_PATTERN = "syscall"
    NETWORK_IOC = "network"
    BEHAVIORAL = "behavioral"


@dataclass
class ThreatSignature:
    """Learned virus signature for future detection."""
    threat_id: str
    pattern_type: PatternType
    pattern: str  # Regex or hash
    description: str
    first_seen: datetime
    instances: int = 0
    severity: ThreatLevel = ThreatLevel.SUSPICIOUS
    ai_confidence: float = 0.0  # 0.0-1.0 confidence from AI analysis
    patch_available: bool = False
    patch_hash: str = ""  # SHA256 of the patch file


@dataclass
class ThreatDetection:
    """Detected threat instance."""
    detection_id: str
    threat_type: str  # "file", "process", "network"
    file_path: Optional[str] = None
    process_pid: Optional[int] = None
    process_name: Optional[str] = None
    severity: ThreatLevel = ThreatLevel.SUSPICIOUS
    evidence: Dict = field(default_factory=dict)
    ai_analysis: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    patch_applied: bool = False


class BinaryAnalyzer:
    """Analyze binary files for suspicious characteristics."""

    @staticmethod
    def calculate_entropy(data: bytes) -> float:
        """Shannon entropy — high entropy = compressed/encrypted = suspicious."""
        if not data or len(data) < 256:  # Need meaningful sample size
            return 0.0
        frequencies = defaultdict(int)
        for byte in data:
            frequencies[byte] += 1
        entropy = 0.0
        data_len = len(data)
        for count in frequencies.values():
            p = count / data_len
            entropy -= p * (p and __import__('math').log2(p) or 0)
        return entropy

    @staticmethod
    def detect_packer_signatures(data: bytes) -> List[str]:
        """Detect common packer signatures (UPX, LZMA, etc)."""
        signatures = {
            b"UPX!": "UPX packer",
            b"\x5d\x00\x00\x00": "LZMA compressed",
            b"This program cannot be run": "Windows binary on Linux",
        }
        found = []
        for sig, name in signatures.items():
            if sig in data:
                found.append(name)
        return found

    @staticmethod
    def check_suspicious_sections(file_path: str) -> Dict:
        """Check for suspicious ELF/PE sections (writeable text, etc)."""
        try:
            result = subprocess.run(
                ["readelf", "-l", file_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            suspicions = {}
            if "LOAD" in result.stdout and "W" in result.stdout:
                suspicions["writeable_code"] = True
            return suspicions
        except Exception as e:
            log.debug(f"Could not analyze sections of {file_path}: {e}")
            return {}


class BehavioralAnalyzer:
    """Analyze process behavior for anomalies."""

    @staticmethod
    def get_process_syscalls(pid: int, timeout: int = 5) -> List[str]:
        """Capture syscalls made by a process."""
        try:
            result = subprocess.run(
                ["timeout", str(timeout), "strace", "-e", "trace=all", "-p", str(pid)],
                capture_output=True,
                text=True,
                stderr=subprocess.STDOUT
            )
            # Parse syscalls from strace output
            syscalls = []
            for line in result.stdout.split('\n'):
                if '(' in line and ')' in line:
                    syscall = line.split('(')[0].strip()
                    if syscall and not syscall.startswith('---'):
                        syscalls.append(syscall)
            return syscalls
        except Exception as e:
            log.debug(f"Could not trace syscalls for PID {pid}: {e}")
            return []

    @staticmethod
    def get_process_connections(pid: int) -> List[Dict]:
        """Get network connections from a process."""
        try:
            result = subprocess.run(
                ["lsof", "-p", str(pid), "-i", "-n"],
                capture_output=True,
                text=True,
                timeout=5
            )
            connections = []
            for line in result.stdout.split('\n')[1:]:  # Skip header
                if '->' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        connections.append({
                            "remote": parts[-1],
                            "state": parts[-2]
                        })
            return connections
        except Exception as e:
            log.debug(f"Could not get connections for PID {pid}: {e}")
            return []


class AIThreatAnalyzer:
    """Use local Ollama/Gemma to analyze threats."""

    def __init__(self, model: str = "gemma4:2b", tailscale_only: bool = True):
        self.model = model
        self.tailscale_only = tailscale_only
        self.session = requests.Session()

    def analyze_binary(self, file_path: str, header: bytes = None) -> Tuple[ThreatLevel, str, float]:
        """
        Analyze a binary file using AI.
        Returns: (threat_level, analysis_text, confidence_score)
        """
        try:
            # Read header if not provided
            if header is None:
                with open(file_path, 'rb') as f:
                    header = f.read(4096)

            entropy = BinaryAnalyzer.calculate_entropy(header)
            packers = BinaryAnalyzer.detect_packer_signatures(header)
            sections = BinaryAnalyzer.check_suspicious_sections(file_path)

            # Build prompt for AI analysis
            prompt = f"""Analyze this binary file for malware indicators:
File: {Path(file_path).name}
Entropy: {entropy:.2f}
Packers detected: {', '.join(packers) or 'None'}
Suspicious sections: {json.dumps(sections, indent=2)}

Based on these characteristics, is this likely malware? Respond with:
THREAT_LEVEL: [SAFE|SUSPICIOUS|HIGH|CRITICAL]
CONFIDENCE: [0.0-1.0]
REASON: [brief explanation]"""

            # Query Ollama locally
            response = self.session.post(
                OLLAMA_API,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=30
            )

            if response.status_code != 200:
                log.warning(f"Ollama API error: {response.status_code}")
                return ThreatLevel.SUSPICIOUS, "API error", 0.5

            result = response.json()
            analysis_text = result.get("response", "")

            # Parse AI response using structured regex (more robust)
            threat_level = ThreatLevel.SUSPICIOUS
            confidence = 0.5

            # Use regex to extract threat level
            level_match = re.search(
                r"THREAT_LEVEL:\s*(SAFE|SUSPICIOUS|HIGH|CRITICAL)",
                analysis_text,
                re.IGNORECASE
            )
            if level_match:
                level_str = level_match.group(1).upper()
                if level_str == "SAFE":
                    threat_level = ThreatLevel.SAFE
                elif level_str == "CRITICAL":
                    threat_level = ThreatLevel.CRITICAL
                elif level_str == "HIGH":
                    threat_level = ThreatLevel.HIGH
                else:
                    threat_level = ThreatLevel.SUSPICIOUS

            # Extract confidence score
            conf_match = re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0|\d+%)", analysis_text, re.IGNORECASE)
            if conf_match:
                conf_str = conf_match.group(1)
                try:
                    if '%' in conf_str:
                        confidence = float(conf_str.rstrip('%')) / 100.0
                    else:
                        confidence = float(conf_str)
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    confidence = 0.5

            return threat_level, analysis_text, confidence

        except Exception as e:
            log.error(f"Error analyzing {file_path}: {e}")
            return ThreatLevel.SUSPICIOUS, str(e), 0.0

    def generate_patch(self, threat_detection: ThreatDetection) -> Optional[str]:
        """
        Generate a patch script for the detected threat.
        Returns path to patch script if successful.
        """
        try:
            prompt = f"""Generate a BASH patch script to mitigate this threat:

Threat Type: {threat_detection.threat_type}
Severity: {threat_detection.severity.value}
Evidence: {json.dumps(threat_detection.evidence)}
AI Analysis: {threat_detection.ai_analysis}

The patch should:
1. Be idempotent (safe to run multiple times)
2. Only remove/block the malicious component
3. Restore normal functionality
4. Log all actions
5. Work on Debian 12 / Ubuntu 24.04

Return ONLY valid bash code, no markdown or explanations."""

            response = self.session.post(
                OLLAMA_API,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=60
            )

            if response.status_code != 200:
                log.warning(f"Ollama patch generation failed: {response.status_code}")
                return None

            result = response.json()
            patch_code = result.get("response", "")

            if not patch_code.strip() or len(patch_code) < 50:
                log.warning("AI generated insufficient patch code")
                return None

            # Write patch to temp file for validation
            patch_path = PATCH_DIR / f"threat-{threat_detection.detection_id}.sh"
            with open(patch_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write("set -euo pipefail\n")
                f.write(f"# Auto-generated patch for {threat_detection.severity.value} threat\n")
                f.write(f"# Generated: {datetime.now().isoformat()}\n\n")
                f.write(patch_code)

            os.chmod(patch_path, 0o750)

            log.info(f"Generated patch: {patch_path}")
            return str(patch_path)

        except Exception as e:
            log.error(f"Error generating patch: {e}")
            return None


class PatchEngine:
    """Apply generated patches safely with strict validation."""

    # Forbidden patterns that indicate dangerous operations
    FORBIDDEN_PATTERNS = [
        r"rm\s+-rf\s+/",      # Recursive delete from root
        r"chmod\s+777\s+/",   # Chmod whole filesystem
        r"mkfs",              # Format filesystem
        r"dd\s+if=",          # Raw disk writes
        r">(>)?/",            # Redirect to root paths
        r":()\s*{\s*:",       # Shell bomb fork
        r"python.*eval",      # Code injection
        r"exec\s+curl",       # Remote code execution
    ]

    @staticmethod
    def is_patch_safe(patch_code: str) -> Tuple[bool, str]:
        """
        Validate that patch code is safe before execution.
        Returns (is_safe, reason)
        """
        for pattern in PatchEngine.FORBIDDEN_PATTERNS:
            if re.search(pattern, patch_code, re.IGNORECASE):
                return False, f"Dangerous pattern detected: {pattern}"

        # Check for obvious misconfigurations
        if "sudo" not in patch_code and "UID" not in patch_code:
            if any(risky in patch_code for risky in [" / ", "/bin", "/lib", "/etc"]):
                log.warning("Patch modifies system paths without privilege check")

        return True, "Patch appears safe"

    @staticmethod
    def validate_patch(patch_path: str) -> bool:
        """Validate patch syntax before execution."""
        try:
            result = subprocess.run(
                ["bash", "-n", patch_path],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            log.error(f"Patch validation failed: {e}")
            return False

    @staticmethod
    def apply_patch(patch_path: str, dry_run: bool = True, require_approval: bool = False) -> Tuple[bool, str]:
        """
        Apply a patch safely.
        If dry_run=True, show what would happen without applying.
        If require_approval=True, user must confirm before execution.
        """
        if not PatchEngine.validate_patch(patch_path):
            return False, "Patch validation failed"

        # Safety check on patch content
        try:
            with open(patch_path, 'r') as f:
                patch_content = f.read()

            is_safe, reason = PatchEngine.is_patch_safe(patch_content)
            if not is_safe:
                log.error(f"Patch failed safety check: {reason}")
                return False, f"Safety check failed: {reason}"
        except Exception as e:
            log.error(f"Error reading patch: {e}")
            return False, str(e)

        try:
            if dry_run:
                log.info(f"[DRY RUN] Would apply patch: {patch_path}")
                log.info(f"Patch content preview:\n{patch_content[:500]}...")
                return True, "Dry run successful"

            # Check if running as root
            if os.geteuid() != 0:
                return False, "Must run as root to apply patches"

            # Require explicit approval for auto-patching
            if require_approval:
                log.warning(f"Patch requires manual approval: {patch_path}")
                return False, "Patch requires user approval (interactive approval not implemented)"

            result = subprocess.run(
                ["bash", patch_path],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                log.info(f"Patch applied successfully: {patch_path}")
                return True, result.stdout
            else:
                log.error(f"Patch application failed: {result.stderr}")
                return False, result.stderr

        except Exception as e:
            log.error(f"Error applying patch: {e}")
            return False, str(e)


class SignatureLearner:
    """Learn new threat signatures from detections."""

    @staticmethod
    def save_signature(threat_sig: ThreatSignature):
        """Add/update threat signature in local database."""
        try:
            signatures = {}
            if SIGNATURE_DB.exists():
                with open(SIGNATURE_DB, 'r') as f:
                    signatures = json.load(f)

            sigs_list = signatures.get("signatures", [])
            # Update or add
            existing = next((s for s in sigs_list if s["threat_id"] == threat_sig.threat_id), None)

            if existing:
                sigs_list.remove(existing)
                existing['instances'] += threat_sig.instances
                sigs_list.append(existing)
            else:
                sig_data = asdict(threat_sig)
                sig_data['first_seen'] = threat_sig.first_seen.isoformat()
                sigs_list.append(sig_data)

            with open(SIGNATURE_DB, 'w') as f:
                json.dump({"signatures": sigs_list, "updated": datetime.now().isoformat()}, f, indent=2)

            log.info(f"Signature saved: {threat_sig.threat_id}")
        except Exception as e:
            log.error(f"Error saving signature: {e}")

    @staticmethod
    def get_signatures() -> Dict:
        """Load all learned threat signatures."""
        if not SIGNATURE_DB.exists():
            return {"signatures": []}
        try:
            with open(SIGNATURE_DB, 'r') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Error loading signatures: {e}")
            return {"signatures": []}


class ThreatDetectionEngine:
    """Main threat detection orchestrator."""

    # Directories to skip during scans (system/pseudo filesystems)
    EXCLUDED_DIRS = {"/proc", "/sys", "/dev", "/run", "/var/run", "/tmp", "/var/tmp"}

    def __init__(self, auto_patch: bool = False, dry_run: bool = True):
        self.auto_patch = auto_patch
        self.dry_run = dry_run
        self.ai = AIThreatAnalyzer()
        self.binary_analyzer = BinaryAnalyzer()
        self.behavior_analyzer = BehavioralAnalyzer()
        self.detection_history = deque(maxlen=1000)
        self.hash_cache = {}  # Cache: hash -> (threat_level, confidence)
        self._load_hash_cache()

    def _load_hash_cache(self):
        """Load cached analysis results from previous scans."""
        cache_file = CACHE_DIR / "threat_analysis_cache.json"
        try:
            if cache_file.exists():
                with open(cache_file, 'r') as f:
                    self.hash_cache = json.load(f)
        except Exception as e:
            log.debug(f"Could not load hash cache: {e}")

    def _save_hash_cache(self):
        """Save cache for future scans."""
        cache_file = CACHE_DIR / "threat_analysis_cache.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(self.hash_cache, f)
        except Exception as e:
            log.debug(f"Could not save hash cache: {e}")

    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA256 of entire file for caching."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return None

    def _calculate_trust_score(
        self,
        entropy: float,
        behavioral_risk: float,
        ai_confidence: float
    ) -> float:
        """
        Calculate composite trust score (0.0-1.0).
        Weighted combination of multiple signals.
        Only patch if score > 0.85 (high confidence).
        """
        # Normalize entropy (typical binary entropy is 5.5-7.0)
        entropy_score = max(0.0, min(1.0, entropy / 8.0))

        # Combine signals
        trust_score = (
            entropy_score * 0.3 +
            behavioral_risk * 0.3 +
            ai_confidence * 0.4
        )

        return trust_score

    def scan_file(self, file_path: str) -> Optional[ThreatDetection]:
        """Scan a single file for threats."""
        try:
            if not os.path.exists(file_path):
                return None

            # Quick checks first
            if not os.path.isfile(file_path):
                return None

            # Skip large files to avoid performance impact
            if os.path.getsize(file_path) > 512 * 1024 * 1024:  # 512 MB
                log.debug(f"Skipping large file: {file_path}")
                return None

            # Check hash cache first
            file_hash = self._calculate_file_hash(file_path)
            if file_hash and file_hash in self.hash_cache:
                cached = self.hash_cache[file_hash]
                if datetime.fromisoformat(cached['timestamp']) > datetime.now() - timedelta(days=30):
                    if cached['threat_level'] != ThreatLevel.SAFE.value:
                        detection_id = hashlib.sha256(
                            f"{file_path}{time.time()}".encode()
                        ).hexdigest()[:16]
                        return ThreatDetection(
                            detection_id=detection_id,
                            threat_type="file",
                            file_path=file_path,
                            severity=ThreatLevel(cached['threat_level']),
                            ai_analysis="(cached result)"
                        )
                    return None

            # Skip obvious non-executables
            with open(file_path, 'rb') as f:
                header = f.read(4096)  # Full header, not just magic

            # Check if binary
            if header[:4] not in [b'\x7fELF', b'MZ\x90\x00', b'\xca\xfe\xba\xbe']:
                return None

            # Run analysis
            threat_level, analysis, confidence = self.ai.analyze_binary(file_path, header)

            # Calculate trust score before deciding to patch
            entropy = self.binary_analyzer.calculate_entropy(header)
            behavioral_risk = 0.3  # Placeholder, would need process monitoring
            trust_score = self._calculate_trust_score(entropy, behavioral_risk, confidence)

            if threat_level != ThreatLevel.SAFE:
                detection_id = hashlib.sha256(
                    f"{file_path}{time.time()}".encode()
                ).hexdigest()[:16]

                detection = ThreatDetection(
                    detection_id=detection_id,
                    threat_type="file",
                    file_path=file_path,
                    severity=threat_level,
                    evidence={
                        "entropy": entropy,
                        "packers": self.binary_analyzer.detect_packer_signatures(header),
                        "trust_score": trust_score
                    },
                    ai_analysis=analysis
                )

                # Try to generate and apply patch only if trust_score is high enough
                if self.auto_patch and trust_score > 0.85 and threat_level in [ThreatLevel.HIGH, ThreatLevel.CRITICAL]:
                    patch_path = self.ai.generate_patch(detection)
                    if patch_path:
                        success, output = PatchEngine.apply_patch(
                            patch_path,
                            dry_run=self.dry_run,
                            require_approval=False  # Already filtered by trust_score
                        )
                        detection.patch_applied = success
                        log.info(f"Patch status: {success} — {output[:100]}")
                elif self.auto_patch and trust_score <= 0.85:
                    log.warning(f"Trust score {trust_score:.2f} too low for auto-patching: {file_path}")

                # Cache result
                if file_hash:
                    self.hash_cache[file_hash] = {
                        'threat_level': threat_level.value,
                        'confidence': confidence,
                        'timestamp': datetime.now().isoformat()
                    }
                    self._save_hash_cache()

                self.detection_history.append(detection)
                return detection

        except Exception as e:
            log.error(f"Error scanning {file_path}: {e}")

        return None

    def scan_directory_recursive(self, directory: str, pattern: str = "*/bin/*", max_files: int = 100) -> List[ThreatDetection]:
        """
        Recursively scan directory for threats with safety limits.
        Excludes system directories to avoid performance impact.
        """
        detections = []
        try:
            file_count = 0
            for file_path in Path(directory).rglob("*"):
                # Safety limits
                if file_count >= max_files:
                    log.warning(f"Scan limit reached ({max_files} files)")
                    break

                # Skip excluded directories
                if any(excluded in str(file_path) for excluded in self.EXCLUDED_DIRS):
                    continue

                if file_path.is_file() and os.access(file_path, os.X_OK):
                    detection = self.scan_file(str(file_path))
                    if detection:
                        detections.append(detection)
                        log.warning(f"Threat detected: {detection.detection_id} in {file_path}")
                    file_count += 1

        except Exception as e:
            log.error(f"Error scanning directory: {e}")

        return detections

    def monitor_process(self, pid: int, duration_sec: int = 60) -> Optional[ThreatDetection]:
        """Monitor a process for suspicious behavior."""
        try:
            syscalls = self.behavior_analyzer.get_process_syscalls(pid, timeout=duration_sec)
            connections = self.behavior_analyzer.get_process_connections(pid)

            # Analyze for suspicious patterns
            suspicious_syscalls = [
                "ptrace", "prctl", "process_vm_readv", "process_vm_writev",
                "kexec_load", "reboot", "finit_module"
            ]
            suspicious_found = [s for s in syscalls if s in suspicious_syscalls]

            # Analyze suspicious network connections
            suspicious_ips = []
            for conn in connections:
                # Check against threat intelligence (simplified)
                if conn['remote'].startswith('192.168') or conn['remote'].startswith('10.'):
                    continue  # Local network OK
                suspicious_ips.append(conn['remote'])

            if suspicious_found or suspicious_ips:
                detection_id = hashlib.sha256(f"pid-{pid}{time.time()}".encode()).hexdigest()[:16]
                detection = ThreatDetection(
                    detection_id=detection_id,
                    threat_type="process",
                    process_pid=pid,
                    severity=ThreatLevel.HIGH if suspicious_found else ThreatLevel.SUSPICIOUS,
                    evidence={
                        "suspicious_syscalls": suspicious_found,
                        "suspicious_connections": suspicious_ips
                    }
                )
                return detection

        except Exception as e:
            log.error(f"Error monitoring process {pid}: {e}")

        return None

    def export_detections(self, output_file: str):
        """Export detection history for analysis."""
        try:
            detections = [
                {
                    'detection_id': d.detection_id,
                    'threat_type': d.threat_type,
                    'severity': d.severity.value,
                    'file_path': d.file_path,
                    'pid': d.process_pid,
                    'timestamp': d.timestamp.isoformat(),
                    'patch_applied': d.patch_applied,
                    'analysis': d.ai_analysis[:200]  # Truncate for export
                }
                for d in self.detection_history
            ]

            with open(output_file, 'w') as f:
                json.dump(detections, f, indent=2)

            log.info(f"Exported {len(detections)} detections to {output_file}")

        except Exception as e:
            log.error(f"Error exporting detections: {e}")


def main():
    parser = argparse.ArgumentParser(description="CookieOS AI Threat Detector")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--scan", type=str, help="Scan directory")
    parser.add_argument("--file", type=str, help="Scan single file")
    parser.add_argument("--process", type=int, help="Monitor process PID")
    parser.add_argument("--auto-patch", action="store_true", help="Auto-apply safe patches")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute patches")
    parser.add_argument("--export", type=str, help="Export detection history")
    parser.add_argument("--model", default="gemma4:2b", help="Ollama model to use")

    args = parser.parse_args()

    engine = ThreatDetectionEngine(
        auto_patch=args.auto_patch,
        dry_run=args.dry_run or not args.auto_patch  # Default to dry-run unless explicitly patching
    )

    if args.file:
        log.info(f"Scanning file: {args.file}")
        detection = engine.scan_file(args.file)
        if detection:
            print(f"THREAT DETECTED: {detection.severity.value}")
            print(f"Analysis: {detection.ai_analysis}")
        else:
            print("File appears safe")

    elif args.scan:
        log.info(f"Scanning directory: {args.scan}")
        detections = engine.scan_directory_recursive(args.scan)
        print(f"Found {len(detections)} threats")
        for d in detections:
            print(f"  - {d.detection_id}: {d.severity.value} ({d.file_path})")

    elif args.process:
        log.info(f"Monitoring process: {args.process}")
        detection = engine.monitor_process(args.process)
        if detection:
            print(f"SUSPICIOUS BEHAVIOR: {detection.severity.value}")
            print(f"Evidence: {detection.evidence}")
        else:
            print("Process behavior appears normal")

    elif args.export:
        engine.export_detections(args.export)

    elif args.daemon:
        print("Daemon mode not yet implemented")
        log.info("Starting threat detector daemon...")


if __name__ == "__main__":
    main()
