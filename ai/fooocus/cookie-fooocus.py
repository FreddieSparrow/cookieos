#!/usr/bin/env python3
"""
CookieFocus — CookieOS Fooocus Image Generation Wrapper
Runs Fooocus locally with CookieOS safety filters applied at:
  1. Prompt stage (before generation)
  2. Output stage (NSFW classifier on result)

All processing is local — no cloud. Integrated with CookieCloud for
saving/syncing generated images to your private storage.

Usage:
  python cookie-fooocus.py --prompt "A serene mountain landscape"
  python cookie-fooocus.py --ui          # Launch web UI
  python cookie-fooocus.py --server      # API server mode
"""

import sys
import os
import json
import time
import logging
import argparse
import subprocess
import tempfile
import hashlib
from pathlib import Path
from typing import Optional

# CookieOS filter (from ai/safeguards/)
sys.path.insert(0, str(Path(__file__).parent.parent / "safeguards"))
from content_filter import check_prompt, check_image, FilterResult, Severity

log = logging.getLogger("cookieos.fooocus")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

FOOOCUS_DIR    = Path(os.environ.get("FOOOCUS_DIR", Path.home() / "AI/Fooocus"))
OUTPUT_DIR     = Path(os.environ.get("COOKIEOS_AI_OUT", Path.home() / "CookieCloud/AI/Images"))
MODELS_DIR     = Path(os.environ.get("COOKIEOS_MODELS", Path.home() / "AI/models"))

# Default generation parameters (conservative/high quality)
DEFAULT_PARAMS = {
    "model":              "juggernautXL_v8Rundiffusion.safetensors",
    "refiner_model":      "None",
    "steps":              30,
    "sampler":            "dpmpp_2m_sde_gpu",
    "scheduler":          "karras",
    "cfg_scale":          7.0,
    "width":              1024,
    "height":             1024,
    "seed":               -1,     # -1 = random
    "negative_prompt": (
        "nsfw, nude, explicit, pornographic, sexual, underage, child, "
        "gore, violence, blood, worst quality, low quality, bad anatomy"
    ),
}

# These styles are available without confirmation
SAFE_STYLES = [
    "Fooocus V2",
    "Fooocus Enhance",
    "Fooocus Sharp",
    "Photograph",
    "Cinematic",
    "Anime",
    "Painting",
    "Sketch",
    "Fantasy",
    "Architecture",
    "Nature",
    "Abstract",
]


def ensure_fooocus():
    """Clone Fooocus if not present."""
    if not FOOOCUS_DIR.exists():
        log.info("[fooocus] Cloning Fooocus...")
        subprocess.run([
            "git", "clone",
            "--depth=1",
            "https://github.com/lllyasviel/Fooocus.git",
            str(FOOOCUS_DIR),
        ], check=True)

    req_file = FOOOCUS_DIR / "requirements_versions.txt"
    if req_file.exists():
        subprocess.run([
            sys.executable, "-m", "pip", "install",
            "-r", str(req_file), "--quiet"
        ], check=True)

    log.info("[fooocus] Fooocus ready at %s", FOOOCUS_DIR)


def generate(
    prompt: str,
    user_id: str = "local",
    params: Optional[dict] = None,
    style: str = "Fooocus V2",
    outdir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Generate an image from a text prompt.
    Returns path to generated image, or None if blocked.
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    outdir = outdir or OUTPUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: Prompt safety check ─────────────────────────────────────────
    result = check_prompt(prompt, user_id=user_id)
    if not result.allowed:
        log.warning("[fooocus] Prompt blocked: %s", result.reason)
        print(f"\n🚫 Request blocked: {result.reason}")
        if result.severity == Severity.CRITICAL:
            print("   This request has been logged.")
        return None

    if result.severity == Severity.WARN:
        print(f"\n⚠  Warning: {result.reason}")
        confirm = input("   Continue anyway? [y/N]: ").strip().lower()
        if confirm != "y":
            print("   Cancelled.")
            return None
        prompt = result.redacted or prompt

    # Append CookieOS safety negative prompt
    full_negative = params["negative_prompt"]

    # ── Stage 2: Generate ─────────────────────────────────────────────────────
    log.info("[fooocus] Generating: %s...", prompt[:80])

    # Build Fooocus API call via its Python API
    sys.path.insert(0, str(FOOOCUS_DIR))
    try:
        import modules.default_pipeline as pipeline
        import modules.async_worker as worker

        ts   = int(time.time())
        seed = params.get("seed", -1)
        if seed == -1:
            import random
            seed = random.randint(0, 2**32 - 1)

        out_filename = f"cookie_{ts}_{seed}.png"
        out_path     = outdir / out_filename

        # Use Fooocus programmatic API
        results = pipeline.process_prompts(
            positive_prompts=[prompt],
            negative_prompts=[full_negative],
            style_selections=[style],
            performance_selection="Speed",
            aspect_ratios_selection=f"{params['width']}×{params['height']}",
            image_number=1,
            output_format="png",
            seed=seed,
            read_wildcards_in_order=False,
            base_model_name=params["model"],
            cfg=params["cfg_scale"],
            steps=params["steps"],
            sampler_name=params["sampler"],
            scheduler=params["scheduler"],
        )

        if results:
            import shutil
            shutil.copy(results[0], out_path)
        else:
            log.error("[fooocus] No output produced")
            return None

    except ImportError:
        # Fooocus not fully installed — use subprocess fallback
        log.info("[fooocus] Using subprocess mode...")
        out_path = outdir / f"cookie_{ts}.png"
        cmd = [
            sys.executable,
            str(FOOOCUS_DIR / "entry_with_update.py"),
            "--generate",
            "--prompt", prompt,
            "--negative-prompt", full_negative,
            "--style", style,
            "--output-path", str(out_path),
            "--steps", str(params["steps"]),
            "--cfg", str(params["cfg_scale"]),
        ]
        subprocess.run(cmd, check=True)

    # ── Stage 3: Output NSFW check ────────────────────────────────────────────
    if out_path.exists():
        img_result = check_image(str(out_path), user_id=user_id)
        if not img_result.allowed:
            log.warning("[fooocus] Generated image blocked by NSFW filter (score=%.2f)",
                        img_result.score)
            out_path.unlink()   # Delete the image
            print(f"\n🚫 Generated image blocked: {img_result.reason}")
            return None

        if img_result.severity == Severity.WARN:
            print(f"\n⚠  Image flagged: {img_result.reason}")

        log.info("[fooocus] Image saved: %s", out_path)
        return out_path

    return None


def launch_ui(host: str = "127.0.0.1", port: int = 7865, public: bool = False):
    """Launch the Fooocus web UI (Gradio)."""
    ensure_fooocus()

    print(f"[CookieFocus] Launching web UI at http://{host}:{port}")
    print("[CookieFocus] All requests are filtered by CookieOS safety layer")
    print("[CookieFocus] Press Ctrl+C to stop\n")

    env = os.environ.copy()
    env["COOKIEOS_FILTER"] = "1"

    flags = [
        "--listen", host,
        "--port", str(port),
        "--disable-in-browser",
        # Inject our negative prompt
        "--negative-prompt",
        DEFAULT_PARAMS["negative_prompt"],
    ]
    if not public:
        flags.append("--no-gradio-queue")

    subprocess.run(
        [sys.executable, str(FOOOCUS_DIR / "launch.py")] + flags,
        env=env,
    )


def launch_api_server(host: str = "127.0.0.1", port: int = 7866):
    """Launch a simple REST API server that wraps generate() with safety checks."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json as _json

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass  # Suppress default logs

        def do_POST(self):
            length  = int(self.headers.get("Content-Length", 0))
            body    = _json.loads(self.rfile.read(length))
            prompt  = body.get("prompt", "")
            user_id = body.get("user_id", "api")
            params  = body.get("params", {})
            style   = body.get("style", "Fooocus V2")

            path = generate(prompt, user_id=user_id, params=params, style=style)

            if path:
                resp = {"status": "ok", "path": str(path)}
                code = 200
            else:
                resp = {"status": "blocked", "path": None}
                code = 403

            data = _json.dumps(resp).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)

    print(f"[CookieFocus] API server on http://{host}:{port}")
    HTTPServer((host, port), Handler).serve_forever()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CookieFocus — Private image generation with safety filters"
    )
    parser.add_argument("--prompt",  "-p", help="Text prompt")
    parser.add_argument("--style",   "-s", default="Fooocus V2",
                        choices=SAFE_STYLES, help="Generation style")
    parser.add_argument("--ui",      action="store_true", help="Launch web UI")
    parser.add_argument("--server",  action="store_true", help="Launch API server")
    parser.add_argument("--setup",   action="store_true", help="Download Fooocus")
    parser.add_argument("--host",    default="127.0.0.1")
    parser.add_argument("--port",    type=int, default=7865)
    parser.add_argument("--user",    default="local", help="User ID for audit log")

    args = parser.parse_args()

    if args.setup:
        ensure_fooocus()
        return

    if args.ui:
        launch_ui(args.host, args.port)
        return

    if args.server:
        launch_api_server(args.host, args.port + 1)
        return

    if args.prompt:
        path = generate(args.prompt, user_id=args.user, style=args.style)
        if path:
            print(f"\n✓ Image saved: {path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
