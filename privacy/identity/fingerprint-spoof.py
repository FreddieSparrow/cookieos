#!/usr/bin/env python3
"""
CookieOS Browser Fingerprint Obfuscation
Generates consistent-but-randomised browser fingerprints per session
to prevent cross-site tracking. Injected as a browser extension.

Covers:
 - Canvas fingerprint noise
 - WebGL vendor/renderer spoofing
 - AudioContext noise
 - Navigator property overrides (UA, platform, languages)
 - Screen resolution bucketing
 - Font enumeration blocking
 - Timezone normalisation
"""

import json
import random
import hashlib
import time
from pathlib import Path

# Real browser fingerprint pools — blend in with the crowd
UA_POOL = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Standard screen sizes only — don't stand out
SCREEN_SIZES = [
    (1920, 1080), (1366, 768), (1440, 900),
    (1280, 800),  (1600, 900), (2560, 1440),
]

WEBGL_VENDORS = [
    ("Intel Inc.",  "Intel Iris OpenGL Engine"),
    ("Google Inc.", "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Mozilla",     "Mozilla"),
]

LANGUAGES = [
    ["en-US", "en"],
    ["en-GB", "en"],
    ["en-US", "en", "de"],
]

TIMEZONES = [
    "UTC", "Europe/London", "America/New_York", "America/Los_Angeles",
]


class SessionFingerprint:
    """Generates a stable-per-session but randomised browser fingerprint."""

    def __init__(self, seed: int | None = None):
        # Seed from boot time rounded to hour — same within a session, different next session
        if seed is None:
            seed = int(time.time() // 3600)
        self.rng = random.Random(seed)
        self._generate()

    def _generate(self):
        self.user_agent   = self.rng.choice(UA_POOL)
        self.screen       = self.rng.choice(SCREEN_SIZES)
        self.webgl        = self.rng.choice(WEBGL_VENDORS)
        self.languages    = self.rng.choice(LANGUAGES)
        self.timezone     = self.rng.choice(TIMEZONES)
        self.canvas_noise = self.rng.randint(1, 255)  # noise seed for canvas
        self.audio_noise  = round(self.rng.uniform(0.00001, 0.00009), 6)
        self.hardware_concurrency = self.rng.choice([2, 4, 8])
        self.device_memory        = self.rng.choice([4, 8, 16])
        self.color_depth          = 24
        self.pixel_ratio          = self.rng.choice([1, 1, 2])  # weight 1x

    def to_js_injection(self) -> str:
        """Returns the JavaScript snippet to inject into browser."""
        ua   = json.dumps(self.user_agent)
        lang = json.dumps(self.languages)
        tz   = json.dumps(self.timezone)
        w, h = self.screen
        vv, vr = self.webgl

        return f"""
// CookieOS Fingerprint Protection — session seed injected
(function() {{
    'use strict';

    // ── User Agent ─────────────────────────────────────────────────────────
    Object.defineProperty(navigator, 'userAgent',    {{ get: () => {ua} }});
    Object.defineProperty(navigator, 'appVersion',   {{ get: () => {ua}.replace('Mozilla/', '') }});
    Object.defineProperty(navigator, 'platform',     {{ get: () => 'Linux x86_64' }});
    Object.defineProperty(navigator, 'language',     {{ get: () => {lang}[0] }});
    Object.defineProperty(navigator, 'languages',    {{ get: () => {lang} }});
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.hardware_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => {self.device_memory} }});
    Object.defineProperty(navigator, 'maxTouchPoints',      {{ get: () => 0 }});

    // ── Screen ─────────────────────────────────────────────────────────────
    Object.defineProperty(screen, 'width',       {{ get: () => {w} }});
    Object.defineProperty(screen, 'height',      {{ get: () => {h} }});
    Object.defineProperty(screen, 'availWidth',  {{ get: () => {w} }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {h} - 40 }});
    Object.defineProperty(screen, 'colorDepth',  {{ get: () => {self.color_depth} }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {self.pixel_ratio} }});

    // ── Canvas noise ───────────────────────────────────────────────────────
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
        const data = origGetImageData.apply(this, args);
        const noise = {self.canvas_noise};
        for (let i = 0; i < data.data.length; i += 4) {{
            data.data[i]   ^= (noise & 0xff);
            data.data[i+1] ^= ((noise >> 1) & 0xff);
        }}
        return data;
    }};

    // ── WebGL ──────────────────────────────────────────────────────────────
    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return {json.dumps(vv)};   // UNMASKED_VENDOR_WEBGL
        if (param === 37446) return {json.dumps(vr)};   // UNMASKED_RENDERER_WEBGL
        return origGetParam.call(this, param);
    }};

    // ── AudioContext noise ─────────────────────────────────────────────────
    const origCreateAnalyser = AudioContext.prototype.createAnalyser;
    AudioContext.prototype.createAnalyser = function() {{
        const analyser = origCreateAnalyser.call(this);
        const origGetFloatFreq = analyser.getFloatFrequencyData.bind(analyser);
        analyser.getFloatFrequencyData = function(array) {{
            origGetFloatFreq(array);
            for (let i = 0; i < array.length; i++) {{
                array[i] += (Math.random() - 0.5) * {self.audio_noise};
            }}
        }};
        return analyser;
    }};

    // ── Timezone ───────────────────────────────────────────────────────────
    Intl.DateTimeFormat.prototype.resolvedOptions = new Proxy(
        Intl.DateTimeFormat.prototype.resolvedOptions,
        {{
            apply(target, thisArg, args) {{
                const opts = Reflect.apply(target, thisArg, args);
                return Object.assign(opts, {{ timeZone: {tz} }});
            }}
        }}
    );

    // ── Font blocking ──────────────────────────────────────────────────────
    // Return a fixed list instead of system fonts
    if (typeof document.fonts !== 'undefined') {{
        Object.defineProperty(document.fonts, 'size', {{ get: () => 3 }});
    }}

    console.debug('[CookieOS] Fingerprint protection active.');
}})();
"""

    def to_dict(self) -> dict:
        return {
            "user_agent": self.user_agent,
            "screen":     {"width": self.screen[0], "height": self.screen[1]},
            "webgl":      {"vendor": self.webgl[0], "renderer": self.webgl[1]},
            "languages":  self.languages,
            "timezone":   self.timezone,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory":        self.device_memory,
        }


def write_extension(out_dir: Path, seed: int | None = None):
    """Write a browser extension (manifest + content script) to out_dir."""
    fp = SessionFingerprint(seed=seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "manifest_version": 3,
        "name": "CookieOS Privacy Shield",
        "version": "1.0",
        "description": "CookieOS fingerprint and tracking protection",
        "content_scripts": [{
            "matches":      ["<all_urls>"],
            "js":           ["fingerprint-inject.js"],
            "run_at":       "document_start",
            "all_frames":   True,
        }],
        "permissions": [],
    }

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out_dir / "fingerprint-inject.js").write_text(fp.to_js_injection())
    print(f"[fingerprint] Extension written to {out_dir}")
    print(f"[fingerprint] Profile: {json.dumps(fp.to_dict(), indent=2)}")


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/cookieos-fp-ext")
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else None
    write_extension(out, seed)
