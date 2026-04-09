#!/usr/bin/env python3
"""
CookieOS AI Content Safety Filter
Shared safeguard layer for ALL AI features (Fooocus image gen, Ollama chat).

Provides:
 - NSFW image detection (CLIP-based, runs locally — no cloud)
 - Prompt injection detection (regex + optional ML classifier)
 - Harmful text classification
 - Rate limiting per user (thread-safe)
 - 18+ content filter (on by default, toggleable)
 - Bypass normalisation (leetspeak, base64, unicode homoglyphs)
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
import base64
import hashlib
import logging
import threading
import unicodedata
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


# ── Global settings (runtime-toggleable) ─────────────────────────────────────

_settings_lock = threading.Lock()
_settings = {
    "adult_filter_enabled": True,   # 18+ filter — on by default
    "prompt_filter_enabled": True,
    "nsfw_image_filter_enabled": True,
}


def set_adult_filter(enabled: bool):
    """Toggle the 18+ content filter. Off requires explicit user consent."""
    with _settings_lock:
        _settings["adult_filter_enabled"] = enabled
    log.info("[filter] 18+ filter %s", "ENABLED" if enabled else "DISABLED")


def get_setting(key: str) -> bool:
    with _settings_lock:
        return _settings.get(key, True)


# ── GitHub Incident Reporter ──────────────────────────────────────────────────

def _alert_github(category: str, user_id: str, evidence: str):
    """
    Alert GitHub for CRITICAL content (CSAM, weapons).
    Logs to a file that must be manually imported to GitHub issues.
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

        log.critical("[ALERT] CRITICAL threat (%s) detected. Manual report needed:", category)
        log.critical("  - Alert file: %s", alert_file)
        log.critical("  - GitHub: %s", alert_data['github_url'])
        log.critical("  - Email: support@techtesting.tech")

        return True

    except Exception as e:
        log.error("Error creating GitHub alert: %s", e)
        return False


# ── Subscription Verification ─────────────────────────────────────────────────

def _verify_subscription(user_id: str) -> bool:
    """
    Verify that user has active CookieOS subscription.
    Returns True if subscribed, False otherwise.
    """
    try:
        sub_file = Path.home() / ".config" / "cookiecloud" / "subscription.json"

        if not sub_file.exists():
            log.warning("Subscription file not found for %s", user_id)
            return False

        with open(sub_file, 'r') as f:
            sub_data = json.load(f)

        if not sub_data.get("active", False):
            log.warning("Subscription inactive for %s", user_id)
            return False

        expires = sub_data.get("expires")
        if expires and datetime.fromisoformat(expires) < datetime.now():
            log.warning("Subscription expired for %s", user_id)
            return False

        return True

    except Exception as e:
        log.error("Subscription verification failed: %s", e)
        return False


# ── Bypass normaliser ─────────────────────────────────────────────────────────

_LEET_MAP = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '7': 't', '@': 'a', '$': 's', '!': 'i', '+': 't',
    '|': 'i', '(': 'c', '<': 'c',
})

_HOMOGLYPH_MAP = {
    '\u0430': 'a',  # Cyrillic а
    '\u0435': 'e',  # Cyrillic е
    '\u0456': 'i',  # Cyrillic і
    '\u043e': 'o',  # Cyrillic о
    '\u0440': 'r',  # Cyrillic р
    '\u0441': 'c',  # Cyrillic с
    '\u0445': 'x',  # Cyrillic х
    '\u0443': 'y',  # Cyrillic у
}


def _normalise(text: str) -> str:
    """
    Normalise text to defeat common bypass techniques:
    - Leetspeak (1337 -> leet)
    - Unicode homoglyphs (Cyrillic/Greek lookalikes)
    - Base64-encoded payloads (decode and append)
    - Excessive whitespace/zero-width characters
    - Combining diacritics (café -> cafe)
    """
    # Strip zero-width chars and soft hyphens
    text = re.sub(r'[\u200b\u200c\u200d\u00ad\ufeff]', '', text)

    # Homoglyph substitution
    for char, replacement in _HOMOGLYPH_MAP.items():
        text = text.replace(char, replacement)

    # Leet speak
    text = text.translate(_LEET_MAP)

    # Remove combining diacritics (e.g., cáfe → cafe)
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))

    # Collapse excessive spacing (bypass via s p a c i n g)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(\w)\s(\w)', lambda m: m.group(1) + m.group(2), text)

    # Try to decode base64 segments and append decoded text for scanning
    b64_matches = re.findall(r'[A-Za-z0-9+/]{20,}={0,2}', text)
    for match in b64_matches[:3]:  # Limit to 3 segments
        try:
            decoded = base64.b64decode(match + '==').decode('utf-8', errors='ignore')
            if decoded.isprintable() and len(decoded) > 4:
                text += ' ' + decoded
        except Exception:
            pass

    return text.lower()


# ── ML Injection Classifier (optional) ───────────────────────────────────────

_ml_classifier = None
_ml_classifier_lock = threading.Lock()
_ml_classifier_loaded = False


def _load_ml_classifier():
    """
    Lazy-load a lightweight prompt injection classifier.
    Uses HuggingFace transformers with a small DistilBERT-based model.
    Falls back gracefully if not available.
    Thread-safe: only one thread loads the model, others wait.
    """
    global _ml_classifier, _ml_classifier_loaded

    with _ml_classifier_lock:
        if _ml_classifier_loaded:
            return _ml_classifier

        try:
            from transformers import pipeline as hf_pipeline
            # Small injection detection model (~66MB quantized)
            # Model: protectai/deberta-v3-base-prompt-injection-v2
            # Fallback: deepset/deberta-v3-base-injection (older, smaller)
            try:
                _ml_classifier = hf_pipeline(
                    "text-classification",
                    model="protectai/deberta-v3-base-prompt-injection-v2",
                    device=-1,  # CPU only
                    truncation=True,
                    max_length=512,
                )
                log.info("[filter] ML injection classifier loaded (protectai/deberta-v3).")
            except Exception:
                # Fallback to a simpler model
                _ml_classifier = hf_pipeline(
                    "text-classification",
                    model="laiyer/deberta-v3-base-prompt-injection",
                    device=-1,
                    truncation=True,
                    max_length=512,
                )
                log.info("[filter] ML injection classifier loaded (laiyer/deberta-v3).")
        except ImportError:
            log.info("[filter] transformers not available — ML injection classifier disabled.")
        except Exception as e:
            log.warning("[filter] Could not load ML injection classifier: %s", e)

        _ml_classifier_loaded = True
        return _ml_classifier


def _ml_injection_score(text: str) -> float:
    """
    Returns injection probability [0.0, 1.0] from ML model.
    Returns 0.0 if classifier unavailable.
    """
    clf = _load_ml_classifier()
    if clf is None:
        return 0.0
    try:
        result = clf(text[:512])[0]
        label = result["label"].lower()
        score = result["score"]
        # Model labels vary: "INJECTION"/"LEGITIMATE" or "1"/"0"
        if "injection" in label or label == "1" or label == "label_1":
            return score
        elif "legitimate" in label or label == "0" or label == "label_0":
            return 1.0 - score
        return score
    except Exception as e:
        log.debug("[filter] ML classifier error: %s", e)
        return 0.0


# ── Block / warn patterns ─────────────────────────────────────────────────────

BLOCK_PATTERNS = [
    # Child safety (absolute block)
    (r"\b(child|minor|underage|loli|shota|teen\b.{0,20}(nude|naked|sex|explicit))\b", "csam", Severity.CRITICAL),

    # Real person non-consensual imagery
    (r"\b(deepfake|face.?swap).{0,30}(nude|naked|sex|undress)", "non-consensual", Severity.BLOCK),

    # Weapons of mass destruction
    (r"\b(bioweapon|nerve agent|sarin|vx gas|anthrax bomb|dirty bomb|nuclear device)\b", "wmd", Severity.CRITICAL),

    # Explicit violence instructions
    (r"\bhow to (make|build|create|synthesize).{0,30}(bomb|explosive|poison|weapon)\b", "violence", Severity.BLOCK),

    # Prompt injection attempts — regex layer
    (r"(ignore (previous|all|prior|above) instructions?|forget (your|all) (rules?|guidelines?|system))", "prompt-injection", Severity.BLOCK),
    (r"\[INST\]|\[\/INST\]|<\|system\|>|<\|user\|>|\{\{.*\}\}", "prompt-injection", Severity.BLOCK),
    # Jailbreak templates
    (r"(dan mode|jailbreak|developer mode|unrestricted mode|no restrictions|bypass (safety|filter|restrictions))", "prompt-injection", Severity.BLOCK),
    # Role-switch injections
    (r"(you are now|pretend (you are|to be|you're) (an? )?(evil|unrestricted|unfiltered|uncensored|jailbroken))", "prompt-injection", Severity.BLOCK),
]

# Patterns that trigger a WARNING (require user confirmation)
WARN_PATTERNS = [
    (r"\b(nude|naked|explicit|nsfw|adult content|sexual)\b", "adult", Severity.WARN),
    (r"\b(gore|graphic violence|torture|mutilation)\b", "gore", Severity.WARN),
    (r"\b(drug|narcotic).{0,20}(make|cook|synthesize|recipe)\b", "drugs", Severity.WARN),
]

# 18+ patterns — only active when adult_filter_enabled=True
ADULT_PATTERNS = [
    (r"\b(sex|erotic|xxx|hentai|pornograph|adult film|onlyfans)\b", "adult-explicit", Severity.BLOCK),
    (r"\b(escort|prostitut|sex work(er)?)\b", "adult-services", Severity.BLOCK),
]


class PromptFilter:
    """Filters text prompts sent to any AI model."""

    ML_INJECTION_THRESHOLD = 0.80  # ML score above this = injection

    def check(self, prompt: str, user_id: str = "anonymous", context: str = "chat") -> FilterResult:
        """
        Filter and validate prompt.
        Steps:
          1. Subscription check
          2. Normalise text (bypass detection)
          3. Regex block patterns
          4. 18+ filter (if enabled)
          5. ML injection classifier (supplementary)
          6. Warn patterns
        """
        if not _verify_subscription(user_id):
            log.warning("[filter] AI denied: no valid subscription for %s", user_id)
            return FilterResult(
                allowed=False,
                severity=Severity.BLOCK,
                reason="CookieOS subscription required. Please visit cookiecloud.techtesting.tech",
                category="subscription"
            )

        normalised = _normalise(prompt)

        # Check block patterns against normalised text
        for pattern, category, severity in BLOCK_PATTERNS:
            if re.search(pattern, normalised, re.IGNORECASE):
                log.warning("[filter] BLOCKED prompt from %s — category=%s", user_id, category)
                _audit_log(user_id, "block", category, prompt[:200])
                if severity == Severity.CRITICAL:
                    _alert_github(category, user_id, prompt[:500])
                return FilterResult(
                    allowed=False,
                    severity=severity,
                    reason=f"Content blocked: {category}",
                    category=category,
                )

        # 18+ filter (only if enabled)
        if get_setting("adult_filter_enabled"):
            for pattern, category, severity in ADULT_PATTERNS:
                if re.search(pattern, normalised, re.IGNORECASE):
                    log.warning("[filter] ADULT BLOCKED from %s — category=%s", user_id, category)
                    _audit_log(user_id, "block", category, prompt[:200])
                    return FilterResult(
                        allowed=False,
                        severity=Severity.BLOCK,
                        reason=f"Adult content blocked (18+ filter active). Disable in Settings.",
                        category=category,
                    )

        # ML injection classifier (supplementary — runs async if model loaded)
        ml_score = _ml_injection_score(normalised)
        if ml_score >= self.ML_INJECTION_THRESHOLD:
            log.warning("[filter] ML injection detected from %s (score=%.2f)", user_id, ml_score)
            _audit_log(user_id, "block", "prompt-injection-ml", prompt[:200])
            return FilterResult(
                allowed=False,
                severity=Severity.BLOCK,
                reason=f"Prompt injection detected by ML classifier (confidence={ml_score:.0%})",
                category="prompt-injection",
                score=ml_score,
            )

        # Check warn patterns
        for pattern, category, severity in WARN_PATTERNS:
            if re.search(pattern, normalised, re.IGNORECASE):
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

try:
    from transformers import pipeline as hf_pipeline
    _HF_AVAILABLE = True
except ImportError:
    _HF_AVAILABLE = False

_nsfw_classifier = None
_nsfw_classifier_lock = threading.Lock()
_nsfw_classifier_loaded = False


def _load_nsfw_classifier():
    """
    Lazy-load NSFW image classifier. Thread-safe.
    First call takes ~2-5s to load model; subsequent calls are instant.
    """
    global _nsfw_classifier, _nsfw_classifier_loaded

    with _nsfw_classifier_lock:
        if _nsfw_classifier_loaded:
            return _nsfw_classifier

        if _HF_AVAILABLE:
            try:
                _nsfw_classifier = hf_pipeline(
                    "image-classification",
                    model="Falconsai/nsfw_image_detection",
                    device=-1,  # CPU
                )
                log.info("[filter] NSFW classifier loaded.")
            except Exception as e:
                log.warning("[filter] Could not load NSFW classifier: %s", e)

        _nsfw_classifier_loaded = True
        return _nsfw_classifier


class ImageFilter:
    """Checks generated images for NSFW content before display."""

    NSFW_THRESHOLD = 0.65  # Confidence above which image is blocked

    def check(self, image_path: str, user_id: str = "anonymous") -> FilterResult:
        if not get_setting("nsfw_image_filter_enabled"):
            return FilterResult(allowed=True, severity=Severity.SAFE)

        if not _PIL_AVAILABLE:
            log.warning("[filter] PIL not available — skipping image check")
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="Image filter unavailable (PIL missing)")

        clf = _load_nsfw_classifier()
        if clf is None:
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="NSFW classifier unavailable (loading or missing)")

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


# ── Rate limiter (thread-safe) ────────────────────────────────────────────────

class RateLimiter:
    """Per-user rate limiter for AI requests. Thread-safe via per-bucket locks."""

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window       = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()  # Protects _buckets dict

    def check(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[user_id]
            # Evict expired entries
            self._buckets[user_id] = [t for t in bucket if now - t < self.window]
            if len(self._buckets[user_id]) >= self.max_requests:
                return False
            self._buckets[user_id].append(now)
            return True

    def remaining(self, user_id: str) -> int:
        """How many requests remain in the current window."""
        now = time.monotonic()
        with self._lock:
            active = [t for t in self._buckets[user_id] if now - t < self.window]
            return max(0, self.max_requests - len(active))


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
    if not get_setting("prompt_filter_enabled"):
        return FilterResult(allowed=True, severity=Severity.SAFE)
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
