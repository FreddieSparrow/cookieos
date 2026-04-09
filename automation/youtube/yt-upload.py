#!/usr/bin/env python3
"""
CookieOS YouTube AI Pipeline
Generates AI content (script + images/video) and uploads to YouTube.
All content passes through CookieOS safety filters before upload.

Pipeline:
  1. Ollama (Gemma 4) writes a video script on a given topic
  2. CookieFocus generates thumbnail + key frame images
  3. CookieVideo assembles a video (if 12GB+ VRAM available)
     OR moviepy creates a slideshow from images
  4. Safety check on all generated content
  5. Upload to YouTube via Data API v3

Auth:
  - Run with --auth on first use to authenticate with YouTube
  - OAuth token stored locally in ~/.config/cookieos/yt-token.json
  - Never sent to CookieNet servers

Usage:
  python yt-upload.py --auth
  python yt-upload.py --topic "The history of the internet" --schedule
  python yt-upload.py --test  (dry run — generate content, don't upload)
"""

import os
import sys
import json
import time
import logging
import argparse
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("cookieos.youtube")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR       = Path(__file__).resolve().parent.parent.parent
SAFEGUARDS_DIR = ROOT_DIR / "ai" / "safeguards"
OLLAMA_DIR     = ROOT_DIR / "ai" / "ollama"
FOOOCUS_DIR    = ROOT_DIR / "ai" / "fooocus"

sys.path.insert(0, str(SAFEGUARDS_DIR))
sys.path.insert(0, str(OLLAMA_DIR))
sys.path.insert(0, str(FOOOCUS_DIR))

from content_filter import check_prompt, check_image, Severity
from cookie_ollama  import ChatSession, DEFAULT_MODEL
from cookie_fooocus import generate as fooocus_generate

OUTPUT_DIR    = Path(os.environ.get("COOKIEOS_YT_OUT", "/opt/cookieos-automation/youtube-output"))
TOKEN_FILE    = Path.home() / ".config/cookieos/yt-token.json"
CRED_FILE     = Path(os.environ.get("COOKIEOS_YT_CREDS",
                                    "/opt/cookieos-automation/client_secret.json"))
SCOPES        = ["https://www.googleapis.com/auth/youtube.upload"]


# ── YouTube auth ──────────────────────────────────────────────────────────────

def authenticate() -> object:
    """Authenticate with YouTube Data API v3 using OAuth2. Stores token locally."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import pickle

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    creds = None

    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CRED_FILE.exists():
                raise FileNotFoundError(
                    f"YouTube credentials not found at {CRED_FILE}.\n"
                    "Download from console.cloud.google.com (YouTube Data API v3)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
        TOKEN_FILE.chmod(0o600)
        log.info("YouTube token saved to %s", TOKEN_FILE)

    return creds


def get_youtube_client():
    from googleapiclient.discovery import build
    creds = authenticate()
    return build("youtube", "v3", credentials=creds)


# ── Script generation ─────────────────────────────────────────────────────────

def generate_script(topic: str, duration_minutes: int = 5) -> Optional[str]:
    """Use Ollama/Gemma 4 to write a YouTube video script."""
    prompt = (
        f"Write a YouTube video script about: {topic}\n\n"
        f"Requirements:\n"
        f"- Approximately {duration_minutes} minutes when read aloud\n"
        f"- Engaging intro that hooks viewers in the first 15 seconds\n"
        f"- Clear structure: intro, 3-5 main points, conclusion\n"
        f"- Include [PAUSE] markers for natural breaks\n"
        f"- Include [IMAGE: description] markers where to show visuals\n"
        f"- End with a call to subscribe\n"
        f"- Family-friendly, informative, and engaging\n\n"
        f"Write only the script, no meta-commentary."
    )

    result = check_prompt(prompt, user_id="automation")
    if not result.allowed:
        log.error("Script prompt blocked: %s", result.reason)
        return None

    session = ChatSession(model=DEFAULT_MODEL, user_id="automation")
    script  = session.send(prompt, print_stream=False)
    log.info("Script generated (%d chars)", len(script))
    return script


def extract_image_prompts(script: str) -> list[str]:
    """Extract [IMAGE: description] markers from script."""
    import re
    return re.findall(r"\[IMAGE:\s*([^\]]+)\]", script)


# ── Thumbnail generation ───────────────────────────────────────────────────────

def generate_thumbnail(topic: str, outdir: Path) -> Optional[Path]:
    """Generate YouTube thumbnail using Fooocus."""
    prompt = (
        f"YouTube video thumbnail for topic: {topic}. "
        "Eye-catching, high contrast, professional, text-free, "
        "cinematic photography style, 16:9 aspect ratio"
    )
    path = fooocus_generate(
        prompt,
        user_id="automation",
        params={"width": 1280, "height": 720},
        style="Photograph",
        outdir=outdir,
    )
    return path


# ── Video assembly ────────────────────────────────────────────────────────────

def assemble_slideshow(
    images:    list[Path],
    script:    str,
    outdir:    Path,
    fps:       int = 24,
    img_duration: float = 4.0,   # seconds per image
) -> Optional[Path]:
    """
    Create a video from a list of images + script (read via TTS or text overlay).
    Uses moviepy — no GPU required.
    """
    try:
        from moviepy.editor import (
            ImageClip, concatenate_videoclips,
            TextClip, CompositeVideoClip,
        )
    except ImportError:
        log.error("moviepy not installed. Run: pip install moviepy")
        return None

    if not images:
        log.error("No images to assemble")
        return None

    clips = []
    for img_path in images:
        clip = ImageClip(str(img_path), duration=img_duration)
        clip = clip.resize((1280, 720))
        clips.append(clip)

    video    = concatenate_videoclips(clips, method="compose")
    out_path = outdir / f"cookie_yt_{int(time.time())}.mp4"
    video.write_videofile(
        str(out_path),
        fps=fps,
        codec="libx264",
        audio=False,
        logger=None,
    )
    log.info("Slideshow assembled: %s", out_path)
    return out_path


# ── Safety check before upload ────────────────────────────────────────────────

def safety_check_all(script: str, images: list[Path], user_id: str = "automation") -> bool:
    """Check script text + all images. Returns False if anything is blocked."""
    # Check script
    result = check_prompt(script[:2000], user_id=user_id)
    if not result.allowed:
        log.error("Script failed safety check: %s", result.reason)
        return False

    # Check all images
    for img in images:
        img_result = check_image(str(img), user_id=user_id)
        if not img_result.allowed:
            log.error("Image %s failed safety check: %s", img.name, img_result.reason)
            return False

    return True


# ── YouTube upload ─────────────────────────────────────────────────────────────

def upload_to_youtube(
    video_path:    Path,
    title:         str,
    description:   str,
    thumbnail:     Optional[Path] = None,
    tags:          list[str] = None,
    category_id:   str = "28",    # Science & Technology
    privacy:       str = "public",
    dry_run:       bool = False,
) -> Optional[str]:
    """Upload video to YouTube. Returns video ID."""
    if dry_run:
        log.info("[DRY RUN] Would upload: %s", video_path)
        log.info("[DRY RUN] Title: %s", title)
        return "dry-run-video-id"

    from googleapiclient.http import MediaFileUpload

    yt = get_youtube_client()

    body = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        (tags or [])[:500],
            "categoryId":  category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10MB chunks
    )

    log.info("Uploading %s to YouTube...", video_path.name)
    request = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"\r  Upload progress: {pct}%", end="", flush=True)

    video_id = response["id"]
    print()
    log.info("Uploaded! Video ID: %s", video_id)
    log.info("URL: https://youtube.com/watch?v=%s", video_id)

    # Set thumbnail
    if thumbnail and thumbnail.exists():
        yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail), mimetype="image/png"),
        ).execute()
        log.info("Thumbnail set.")

    return video_id


# ── Full pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    topic:    str,
    title:    Optional[str] = None,
    privacy:  str = "public",
    dry_run:  bool = False,
    schedule: bool = False,
) -> Optional[str]:
    """
    Full AI → YouTube pipeline:
    generate script → images → video → safety check → upload
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts      = int(time.time())
    job_dir = OUTPUT_DIR / f"job_{ts}"
    job_dir.mkdir()

    log.info("=== CookieOS YouTube Pipeline ===")
    log.info("Topic:   %s", topic)
    log.info("Privacy: %s | Dry run: %s", privacy, dry_run)

    # 1. Generate script
    log.info("Step 1/5: Generating script...")
    script = generate_script(topic)
    if not script:
        log.error("Script generation failed.")
        return None
    (job_dir / "script.txt").write_text(script)

    # 2. Generate images for each IMAGE marker
    log.info("Step 2/5: Generating images...")
    image_prompts = extract_image_prompts(script)
    if not image_prompts:
        # Fall back to topic-based images
        image_prompts = [f"{topic}, cinematic, beautiful", f"{topic}, close-up detail"]

    images: list[Path] = []
    for i, p in enumerate(image_prompts[:8]):  # Max 8 images
        log.info("  Image %d/%d: %s", i + 1, min(len(image_prompts), 8), p[:60])
        img = fooocus_generate(p, user_id="automation",
                               params={"width": 1280, "height": 720},
                               style="Cinematic",
                               outdir=job_dir)
        if img:
            images.append(img)
        else:
            log.warning("  Image %d blocked or failed.", i + 1)

    if not images:
        log.error("No images generated.")
        return None

    # 3. Generate thumbnail
    log.info("Step 3/5: Generating thumbnail...")
    thumbnail = generate_thumbnail(topic, job_dir)

    # 4. Safety check
    log.info("Step 4/5: Safety check...")
    if not safety_check_all(script, images):
        log.error("Content failed safety check. Aborting.")
        return None
    log.info("  All content passed safety check.")

    # 5. Assemble video
    log.info("Step 5/5: Assembling video...")
    video = assemble_slideshow(images, script, job_dir)
    if not video:
        log.error("Video assembly failed.")
        return None

    # 6. Upload
    video_title = title or f"{topic} | CookieAI"
    description = (
        f"{script[:800]}...\n\n"
        "Generated with CookieAI — private, local AI. cookiehost.uk"
    )
    tags = topic.lower().split() + ["AI", "CookieAI", "CookieNet"]

    video_id = upload_to_youtube(
        video, video_title, description,
        thumbnail=thumbnail,
        tags=tags,
        privacy=privacy,
        dry_run=dry_run,
    )

    # Save job summary
    summary = {
        "ts":       datetime.utcnow().isoformat(),
        "topic":    topic,
        "title":    video_title,
        "video_id": video_id,
        "images":   len(images),
        "dry_run":  dry_run,
    }
    (job_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Job summary saved to %s", job_dir / "summary.json")

    return video_id


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CookieOS YouTube AI Pipeline"
    )
    parser.add_argument("--auth",     action="store_true",
                        help="Authenticate with YouTube (first-time setup)")
    parser.add_argument("--topic",    "-t", help="Video topic")
    parser.add_argument("--title",    help="Custom video title")
    parser.add_argument("--privacy",  default="public",
                        choices=["public", "unlisted", "private"])
    parser.add_argument("--test",     action="store_true",
                        help="Dry run — generate content but don't upload")
    parser.add_argument("--list-jobs", action="store_true",
                        help="List recent pipeline runs")

    args = parser.parse_args()

    if args.auth:
        log.info("Starting YouTube authentication flow...")
        authenticate()
        ok_msg = "Authentication complete. Token saved."
        print(f"\n✓ {ok_msg}")
        return

    if args.list_jobs:
        jobs = sorted(OUTPUT_DIR.glob("job_*"), reverse=True)[:10]
        print(f"Recent pipeline runs ({len(jobs)}):")
        for j in jobs:
            s = j / "summary.json"
            if s.exists():
                d = json.loads(s.read_text())
                print(f"  {d['ts'][:16]}  {d['title'][:50]}  id={d['video_id']}")
        return

    if args.topic:
        video_id = run_pipeline(
            args.topic,
            title=args.title,
            privacy=args.privacy,
            dry_run=args.test,
        )
        if video_id:
            if args.test:
                print(f"\n✓ Dry run complete. (would be: https://youtube.com/watch?v={video_id})")
            else:
                print(f"\n✓ Uploaded: https://youtube.com/watch?v={video_id}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
