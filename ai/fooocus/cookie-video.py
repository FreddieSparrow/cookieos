#!/usr/bin/env python3
"""
CookieVideo — CookieOS AI Video Generation
Uses Stable Diffusion Video (SVD / SVD-XT) to generate short video clips
from an image or text prompt — entirely local, no cloud.

Hardware requirement: 12GB+ VRAM (NVIDIA or AMD ROCm)
Falls back gracefully with a clear error if VRAM is insufficient.

Safety: all input images and prompts pass through CookieOS content filter.
Generated frames are individually checked before the video is assembled.

Models:
  stabilityai/stable-video-diffusion-img2vid        (14 frames, 576x1024)
  stabilityai/stable-video-diffusion-img2vid-xt     (25 frames, 576x1024)
"""

import sys
import os
import json
import time
import logging
import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookieos.video")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# CookieOS filters
sys.path.insert(0, str(Path(__file__).parent.parent / "safeguards"))
from content_filter import check_prompt, check_image, FilterResult, Severity

VIDEO_OUT_DIR  = Path.home() / "CookieCloud/AI/Videos"
MODELS_DIR     = Path(os.environ.get("COOKIEOS_MODELS", Path.home() / "AI/models"))

SVD_MODEL      = "stabilityai/stable-video-diffusion-img2vid"
SVD_XT_MODEL   = "stabilityai/stable-video-diffusion-img2vid-xt"

MIN_VRAM_GB    = 12.0   # Hard requirement
IDEAL_VRAM_GB  = 16.0   # For SVD-XT (25 frames)


# ── VRAM check ────────────────────────────────────────────────────────────────

def get_vram_gb() -> tuple[float, str]:
    """Returns (vram_gb, gpu_name). Returns (0, '') if no GPU found."""
    # NVIDIA
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
        ).decode().strip().split("\n")[0]
        parts = out.split(", ")
        return float(parts[1]) / 1024, parts[0].strip()
    except Exception:
        pass

    # AMD ROCm
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
            stderr=subprocess.DEVNULL,
        ).decode()
        for line in out.splitlines():
            if "VRAM Total Memory" in line:
                gb = float(line.split(",")[-1]) / (1024**3)
                return gb, "AMD GPU"
    except Exception:
        pass

    # Apple Silicon (MPS)
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"],
                stderr=subprocess.DEVNULL,
            ).decode()
            if "Apple M" in out:
                import re
                # Parse unified memory
                m = re.search(r"Memory:\s+(\d+)\s*GB", out)
                if m:
                    return float(m.group(1)), "Apple Silicon"
        except Exception:
            pass

    return 0.0, ""


def check_vram_requirement() -> tuple[float, str]:
    """Raises RuntimeError if VRAM is insufficient."""
    vram_gb, gpu_name = get_vram_gb()
    if vram_gb < MIN_VRAM_GB:
        raise RuntimeError(
            f"CookieVideo requires {MIN_VRAM_GB}GB+ VRAM. "
            f"Detected: {vram_gb:.1f}GB ({gpu_name or 'no GPU'}). "
            f"Connect to a CookieNet server with a compatible GPU, or "
            f"upgrade hardware."
        )
    return vram_gb, gpu_name


# ── Dependencies ──────────────────────────────────────────────────────────────

def ensure_deps():
    """Install required packages if missing."""
    required = [
        "torch", "torchvision", "transformers", "diffusers",
        "accelerate", "safetensors", "pillow", "imageio",
        "imageio-ffmpeg", "opencv-python-headless",
    ]
    try:
        import diffusers  # noqa
    except ImportError:
        log.info("[video] Installing dependencies...")
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--quiet"
        ] + required, check=True)


# ── Image-to-video generation ─────────────────────────────────────────────────

def generate_video(
    input_image: Path,
    user_id:     str       = "local",
    num_frames:  int       = 14,
    fps:         int       = 7,
    motion:      float     = 127.0,   # Motion bucket ID (0–255)
    decode_chunk: int      = 4,       # Lower = less VRAM but slower
    seed:        Optional[int] = None,
    outdir:      Optional[Path] = None,
) -> Optional[Path]:
    """
    Generate a video clip from an input image.
    Returns path to output .mp4, or None if blocked/failed.
    """
    outdir = outdir or VIDEO_OUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    # ── VRAM gate ─────────────────────────────────────────────────────────────
    try:
        vram_gb, gpu_name = check_vram_requirement()
        log.info("[video] GPU: %s (%.1f GB VRAM)", gpu_name, vram_gb)
    except RuntimeError as e:
        print(f"\n🚫 {e}")
        return None

    # ── Safety: check input image ─────────────────────────────────────────────
    img_check = check_image(str(input_image), user_id=user_id)
    if not img_check.allowed:
        print(f"\n🚫 Input image blocked: {img_check.reason}")
        return None
    if img_check.severity == Severity.WARN:
        print(f"\n⚠  Input image flagged: {img_check.reason}")
        confirm = input("   Continue? [y/N]: ").strip().lower()
        if confirm != "y":
            return None

    ensure_deps()

    import torch
    from diffusers import StableVideoDiffusionPipeline
    from diffusers.utils import load_image, export_to_video
    from PIL import Image

    # ── Choose model ──────────────────────────────────────────────────────────
    model_id = SVD_XT_MODEL if (vram_gb >= IDEAL_VRAM_GB and num_frames > 14) else SVD_MODEL
    log.info("[video] Loading %s...", model_id)

    # Load with float16 + CPU offload to stay within VRAM
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        variant="fp16",
        cache_dir=str(MODELS_DIR),
    )
    pipe.enable_model_cpu_offload()
    pipe.unet.enable_forward_chunking()   # Reduce peak VRAM

    # ── Generate ──────────────────────────────────────────────────────────────
    img = load_image(str(input_image))
    img = img.resize((1024, 576))   # SVD native resolution

    generator = torch.manual_seed(seed) if seed is not None else None

    log.info("[video] Generating %d frames at %d fps...", num_frames, fps)
    with torch.autocast("cuda", dtype=torch.float16):
        frames = pipe(
            img,
            num_frames=num_frames,
            num_inference_steps=25,
            decode_chunk_size=decode_chunk,
            motion_bucket_id=int(motion),
            generator=generator,
        ).frames[0]

    # ── Safety: check each frame ──────────────────────────────────────────────
    log.info("[video] Checking generated frames for safety...")
    with tempfile.TemporaryDirectory() as tmpdir:
        blocked_frames = 0
        for i, frame in enumerate(frames):
            frame_path = Path(tmpdir) / f"frame_{i:04d}.png"
            frame.save(frame_path)
            fr_check = check_image(str(frame_path), user_id=user_id)
            if not fr_check.allowed:
                blocked_frames += 1

        if blocked_frames > 0:
            log.warning("[video] %d/%d frames blocked by NSFW filter",
                        blocked_frames, len(frames))
            print(f"\n🚫 Video blocked: {blocked_frames} frames failed safety filter.")
            return None

    # ── Export ────────────────────────────────────────────────────────────────
    ts       = int(time.time())
    out_path = outdir / f"cookie_video_{ts}.mp4"
    export_to_video(frames, str(out_path), fps=fps)
    log.info("[video] Video saved: %s", out_path)
    return out_path


# ── Text-to-video (via Fooocus + SVD pipeline) ───────────────────────────────

def text_to_video(
    prompt:   str,
    user_id:  str       = "local",
    **kwargs,
) -> Optional[Path]:
    """
    Generate a video from a text prompt by:
    1. Using CookieFocus (Fooocus) to generate a keyframe image.
    2. Using SVD to animate that image into a video.
    """
    # Safety check prompt
    result = check_prompt(prompt, user_id=user_id)
    if not result.allowed:
        print(f"\n🚫 Prompt blocked: {result.reason}")
        return None
    if result.severity == Severity.WARN:
        print(f"\n⚠  {result.reason}")
        confirm = input("   Continue? [y/N]: ").strip().lower()
        if confirm != "y":
            return None

    # Generate keyframe with Fooocus
    log.info("[video] Generating keyframe from prompt...")
    sys.path.insert(0, str(Path(__file__).parent))
    from cookie_fooocus import generate as fooocus_generate

    keyframe = fooocus_generate(
        prompt,
        user_id=user_id,
        params={"width": 1024, "height": 576},   # SVD aspect ratio
        style="Cinematic",
    )
    if keyframe is None:
        log.error("[video] Keyframe generation failed.")
        return None

    log.info("[video] Keyframe: %s", keyframe)
    return generate_video(keyframe, user_id=user_id, **kwargs)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CookieVideo — Local AI video generation (12GB+ VRAM required)"
    )
    sub = parser.add_subparsers(dest="cmd")

    # Image to video
    p_i2v = sub.add_parser("img2vid", help="Animate an image into a video")
    p_i2v.add_argument("image",        help="Input image path")
    p_i2v.add_argument("--frames",     type=int,   default=14, help="Number of frames (14 or 25)")
    p_i2v.add_argument("--fps",        type=int,   default=7)
    p_i2v.add_argument("--motion",     type=float, default=127.0,
                       help="Motion intensity 0–255 (127=medium)")
    p_i2v.add_argument("--seed",       type=int,   default=None)
    p_i2v.add_argument("--user",       default="local")

    # Text to video
    p_t2v = sub.add_parser("txt2vid", help="Generate video from text prompt")
    p_t2v.add_argument("prompt")
    p_t2v.add_argument("--frames",     type=int,   default=14)
    p_t2v.add_argument("--fps",        type=int,   default=7)
    p_t2v.add_argument("--motion",     type=float, default=127.0)
    p_t2v.add_argument("--seed",       type=int,   default=None)
    p_t2v.add_argument("--user",       default="local")

    # Hardware check
    sub.add_parser("check", help="Check GPU compatibility")

    args = parser.parse_args()

    if args.cmd == "check":
        vram_gb, gpu_name = get_vram_gb()
        print(f"\n🎮 GPU: {gpu_name or 'None detected'}")
        print(f"   VRAM: {vram_gb:.1f} GB")
        if vram_gb >= IDEAL_VRAM_GB:
            print(f"   ✓ Compatible with SVD-XT (25 frames, high quality)")
        elif vram_gb >= MIN_VRAM_GB:
            print(f"   ✓ Compatible with SVD (14 frames)")
        else:
            print(f"   ✗ Insufficient VRAM (need {MIN_VRAM_GB}GB+)")
            print(f"     Connect to a CookieNet GPU server instead.")
        return

    if args.cmd == "img2vid":
        path = generate_video(
            Path(args.image),
            user_id=args.user,
            num_frames=args.frames,
            fps=args.fps,
            motion=args.motion,
            seed=args.seed,
        )
        if path:
            print(f"\n✓ Video saved: {path}")
        return

    if args.cmd == "txt2vid":
        path = text_to_video(
            args.prompt,
            user_id=args.user,
            num_frames=args.frames,
            fps=args.fps,
            motion=args.motion,
            seed=args.seed,
        )
        if path:
            print(f"\n✓ Video saved: {path}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
