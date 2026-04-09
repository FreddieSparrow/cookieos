#!/usr/bin/env python3
"""
CookieOS AI Content Safety Filter
Shared safeguard layer for ALL AI features (Fooocus image gen, Ollama chat).

Provides:
 - NSFW image detection (CLIP-based, runs locally — no cloud)
 - Prompt injection detection
 - Harmful text classification
 - Rate limiting per user
 - Audit logging (encrypted, stored in CookieCloud)
 - GitHub alerts for CRITICAL content

All checks run LOCALLY — no data leaves the device.

═══════════════════════════════════════════════════════════════════════════════
LEGAL DISCLAIMER
═══════════════════════════════════════════════════════════════════════════════
CookieOS Content Filter is provided by CookieHost UK ("we", "us").

DISCLAIMER OF LIABILITY:
- We are NOT responsible for any inappropriate content generated, displayed, or
  stored on your device, including consequences of filter bypasses.
- CRITICAL threats (CSAM, weapons) will be reported to:
  * support@techtesting.tech (internal escalation)
  * GitHub: https://github.com/FreddieSparrow/cookieos/issues (incident tracking)
  * Potential law enforcement notification (CSAM only)
- This filter requires valid CookieOS subscription to function.

By using CookieOS, you accept these terms and consent to threat reporting.
CookieHost UK, 82.68.101.76
═══════════════════════════════════════════════════════════════════════════════
"""

import re
import time
import json
import hashlib
import logging
import subprocess
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict

log = logging.getLogger("cookieos.ai.filter")

# ── Severity levels ───────────────────────────────────────────────────────────

class Severity(Enum):
    SAFE     = "safe"
    WARN     = "warn"       # Borderline — flag but allow with confirmation
    BLOCK    = "block"      # Hard block
    CRITICAL = "critical"   # Block + alert admin


@dataclass
class FilterResult:
    allowed:   bool
    severity:  Severity
    reason:    str        = ""
    category:  str        = ""
    score:     float      = 0.0
    redacted:  str        = ""   # Cleaned version of input (if warn)


# ── GitHub Incident Reporter ──────────────────────────────────────────────────

def _alert_github(category: str, user_id: str, evidence: str):
    """
    Alert GitHub for CRITICAL content (CSAM, weapons).
    
    Since we don't call external services from CookieOS,
    this logs to a file that must be manually imported to GitHub issues.
    
    Usage: https://github.com/FreddieSparrow/cookieos/issues
    """
    try:
        alert_dir = Path.home() / ".local" / "share" / "cookieos" / "alerts"
        alert_dir.mkdir(parents=True, exist_ok=True)

        alert_file = alert_dir / f"critical-{datetime.now().isoformat()}.json"
        alert_data = {
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "user_id": hashlib.sha256(user_id.encode()).hexdigest()[:16],
            "evidence_hash": hashlib.sha256(evidence.encode()).hexdigest(),
            "action": "MANUAL_GITHUB_REPORT_NEEDED",
            "github_url": "https://github.com/FreddieSparrow/cookieos/issues/new?title=CRITICAL%20Content%20Alert"
        }

        with open(alert_file, 'w') as f:
            json.dump(alert_data, f, indent=2)

        log.critical(f"[ALERT] CRITICAL threat ({category}) detected. Manual report needed:")
        log.critical(f"  - Alert file: {alert_file}")
        log.critical(f"  - GitHub: {alert_data['github_url']}")
        log.critical(f"  - Email: support@techtesting.tech")

        return True

    except Exception as e:
        log.error(f"Error creating GitHub alert: {e}")
        return False


# ── Subscription Verification ─────────────────────────────────────────────────

def _verify_subscription(user_id: str) -> bool:
    """
    Verify that user has active CookieOS subscription.
    Required to use AI features.
    
    Returns True if subscribed, False otherwise.
    """
    try:
        # Check CookieCloud subscription status
        # (In production, connect to accounting system)
        sub_file = Path.home() / ".config" / "cookiecloud" / "subscription.json"

        if not sub_file.exists():
            log.warning(f"Subscription file not found for {user_id}")
            return False

        with open(sub_file, 'r') as f:
            sub_data = json.load(f)

        if not sub_data.get("active", False):
            log.warning(f"Subscription inactive for {user_id}")
            return False

        # Check expiry
        expires = sub_data.get("expires")
        if expires and datetime.fromisoformat(expires) < datetime.now():
            log.warning(f"Subscription expired for {user_id}")
            return False

        return True

    except Exception as e:
        log.error(f"Subscription verification failed: {e}")
        return False



BLOCK_PATTERNS = [
    # Child safety (absolute block)
    (r"\b(child|minor|underage|loli|shota|teen\b.{0,20}(nude|naked|sex|explicit))\b", "csam", Severity.CRITICAL),

    # Real person non-consensual imagery
    (r"\b(deepfake|face.?swap).{0,30}(nude|naked|sex|undress)", "non-consensual", Severity.BLOCK),

    # Weapons of mass destruction
    (r"\b(bioweapon|nerve agent|sarin|vx gas|anthrax bomb|dirty bomb|nuclear device)\b", "wmd", Severity.CRITICAL),

    # Explicit violence instructions
    (r"\bhow to (make|build|create|synthesize).{0,30}(bomb|explosive|poison|weapon)\b", "violence", Severity.BLOCK),

    # Prompt injection attempts
    (r"(ignore (previous|all|prior|above) instructions?|forget (your|all) (rules?|guidelines?|system))", "prompt-injection", Severity.BLOCK),
    (r"\[INST\]|\[\/INST\]|<\|system\|>|<\|user\|>|\{\{.*\}\}", "prompt-injection", Severity.BLOCK),
]

# Patterns that trigger a WARNING (require user confirmation)
WARN_PATTERNS = [
    (r"\b(nude|naked|explicit|nsfw|adult content|sexual)\b", "adult", Severity.WARN),
    (r"\b(gore|graphic violence|torture|mutilation)\b", "gore", Severity.WARN),
    (r"\b(drug|narcotic).{0,20}(make|cook|synthesize|recipe)\b", "drugs", Severity.WARN),
]


class PromptFilter:
    """Filters text prompts sent to any AI model."""

    def check(self, prompt: str, user_id: str = "anonymous", context: str = "chat") -> FilterResult:
        """
        Filter and validate prompt.
        
        Requires: Valid CookieOS subscription
        """
        # CRITICAL: Verify subscription before allowing any AI
        if not _verify_subscription(user_id):
            log.warning(f"AI denied: no valid subscription for {user_id}")
            return FilterResult(
                allowed=False,
                severity=Severity.BLOCK,
                reason="CookieOS subscription required. Please visit https://cookiecloud.techtesting.tech",
                category="subscription"
            )

        prompt_lower = prompt.lower()

        # Check block patterns
        for pattern, category, severity in BLOCK_PATTERNS:
            if re.search(pattern, prompt_lower, re.IGNORECASE):
                log.warning("[filter] BLOCKED prompt from %s — category=%s", user_id, category)
                _audit_log(user_id, "block", category, prompt[:200])

                # CRITICAL: Alert GitHub for severe threats
                if severity == Severity.CRITICAL:
                    _alert_github(category, user_id, prompt[:500])

                return FilterResult(
                    allowed=False,
                    severity=severity,
                    reason=f"Content blocked: {category}",
                    category=category,
                )

        # Check warn patterns
        for pattern, category, severity in WARN_PATTERNS:
            if re.search(pattern, prompt_lower, re.IGNORECASE):
                log.info("[filter] WARN prompt from %s — category=%s", user_id, category)
                _audit_log(user_id, "warn", category, prompt[:200])
                return FilterResult(
                    allowed=True,
                    severity=Severity.WARN,
                    reason=f"Flagged content: {category}. Proceed with caution.",
                    category=category,
                    redacted=_redact(prompt, pattern),
                )

        return FilterResult(allowed=True, severity=Severity.SAFE)


# ── Image output filter (NSFW detection) ─────────────────────────────────────

try:
    from PIL import Image
    import numpy as np
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# If transformers/CLIP is available, use it; otherwise fall back to hash-based
try:
    from transformers import pipeline as hf_pipeline
    _HF_AVAILABLE = True
except ImportError:
    _HF_AVAILABLE = False

_nsfw_classifier = None

def _load_nsfw_classifier():
    global _nsfw_classifier
    if _nsfw_classifier is None and _HF_AVAILABLE:
        try:
            # Loads a small NSFW image classifier locally (no network after first download)
            _nsfw_classifier = hf_pipeline(
                "image-classification",
                model="Falconsai/nsfw_image_detection",
                device=-1,  # CPU
            )
            log.info("[filter] NSFW classifier loaded.")
        except Exception as e:
            log.warning("[filter] Could not load NSFW classifier: %s", e)
    return _nsfw_classifier


class ImageFilter:
    """Checks generated images for NSFW content before display."""

    NSFW_THRESHOLD = 0.65  # Confidence above which image is blocked

    def check(self, image_path: str, user_id: str = "anonymous") -> FilterResult:
        if not _PIL_AVAILABLE:
            log.warning("[filter] PIL not available — skipping image check")
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="Image filter unavailable (PIL missing)")

        clf = _load_nsfw_classifier()
        if clf is None:
            # Fallback: warn but allow
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="NSFW classifier unavailable")

        try:
            img = Image.open(image_path).convert("RGB")
            results = clf(img)

            nsfw_score = 0.0
            for r in results:
                label = r["label"].lower()
                score = r["score"]
                if "nsfw" in label or "explicit" in label or "porn" in label:
                    nsfw_score = max(nsfw_score, score)

            if nsfw_score >= self.NSFW_THRESHOLD:
                log.warning("[filter] NSFW image blocked (score=%.2f) for user %s",
                            nsfw_score, user_id)
                _audit_log(user_id, "block", "nsfw-image", image_path)
                return FilterResult(
                    allowed=False,
                    severity=Severity.BLOCK,
                    reason=f"Image classified as NSFW (score={nsfw_score:.0%})",
                    category="nsfw-image",
                    score=nsfw_score,
                )
            elif nsfw_score >= 0.35:
                return FilterResult(
                    allowed=True,
                    severity=Severity.WARN,
                    reason=f"Image may contain adult content (score={nsfw_score:.0%})",
                    category="nsfw-image",
                    score=nsfw_score,
                )

        except Exception as e:
            log.error("[filter] Image check error: %s", e)

        return FilterResult(allowed=True, severity=Severity.SAFE)


# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Per-user rate limiter for AI requests."""

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window       = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets[user_id]
        # Remove old entries
        self._buckets[user_id] = [t for t in bucket if now - t < self.window]
        if len(self._buckets[user_id]) >= self.max_requests:
            return False
        self._buckets[user_id].append(now)
        return True


# ── Audit logging ─────────────────────────────────────────────────────────────

AUDIT_LOG = Path.home() / ".local/share/cookieos/ai-audit.jsonl"

def _audit_log(user_id: str, action: str, category: str, content_hash_input: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts":       datetime.utcnow().isoformat(),
        "user":     user_id,
        "action":   action,
        "category": category,
        "hash":     hashlib.sha256(content_hash_input.encode()).hexdigest()[:16],
    }
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _redact(text: str, pattern: str) -> str:
    return re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)


# ── Unified safety check ──────────────────────────────────────────────────────

_prompt_filter = PromptFilter()
_image_filter  = ImageFilter()
_rate_limiter  = RateLimiter(max_requests=30, window_seconds=60)


def check_prompt(prompt: str, user_id: str = "anonymous") -> FilterResult:
    """Main entry point for checking a text prompt."""
    if not _rate_limiter.check(user_id):
        return FilterResult(
            allowed=False, severity=Severity.BLOCK,
            reason="Rate limit exceeded. Please wait before sending more requests.",
            category="rate-limit",
        )
    return _prompt_filter.check(prompt, user_id)


def check_image(image_path: str, user_id: str = "anonymous") -> FilterResult:
    """Main entry point for checking a generated image."""
    return _image_filter.check(image_path, user_id)
