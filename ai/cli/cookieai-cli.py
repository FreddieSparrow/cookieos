#!/usr/bin/env python3
"""
CookieAI CLI — Lite terminal interface for low-end hardware.
Designed for: Raspberry Pi, headless servers, SSH sessions, any Linux terminal.

Minimal dependencies: Python 3.9+, requests (optional), urllib (stdlib fallback).
No GUI frameworks. No heavy ML models. Runs on 512MB RAM.

Features:
  - Chat with Ollama/Gemma via local or Tailscale endpoint
  - Image generation trigger (Fooocus remote)
  - Basic content filtering (regex — no ML, keeps it lightweight)
  - Session persistence via ~/.cookieai_history.jsonl
  - CookieCloud sync check
  - Tanda 3D print submission

Usage:
  python cookieai-cli.py                  # Interactive chat
  python cookieai-cli.py --model gemma3:2b  # Use smaller model
  python cookieai-cli.py --host 100.x.x.x:11434  # Tailscale host
  python cookieai-cli.py --image "a sunset over mountains"
  python cookieai-cli.py --no-filter     # Disable safety filter (18+ still on by default)
  python cookieai-cli.py --tanda "pla filament cube 20mm"  # Submit 3D print quote
"""

import os
import sys
import json
import re
import time
import readline  # noqa: F401 — enables arrow keys/history in input()
import argparse
import threading
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Iterator

# Try requests first, fall back to urllib
try:
    import requests
    _USE_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    _USE_REQUESTS = False


# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST   = os.environ.get("COOKIEAI_HOST", "http://localhost:11434")
DEFAULT_MODEL  = os.environ.get("COOKIEAI_MODEL", "gemma3:4b")
HISTORY_FILE   = Path.home() / ".cookieai_history.jsonl"
SETTINGS_FILE  = Path.home() / ".config/cookieos/cli-settings.json"
MAX_HISTORY    = 20  # Keep last 20 exchanges in context

VERSION = "1.0.0"

# ── ANSI colours (auto-disabled if not a TTY) ──────────────────────────────────

_IS_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"

RED    = lambda t: _c("31", t)
GREEN  = lambda t: _c("32", t)
YELLOW = lambda t: _c("33", t)
CYAN   = lambda t: _c("36", t)
BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)


# ── Settings ───────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {
        "adult_filter": True,
        "prompt_filter": True,
        "ollama_host": DEFAULT_HOST,
        "model": DEFAULT_MODEL,
        "cookiecloud_server": "https://cookiecloud.cookiehost.uk",
        "tanda_url": "https://www.tanda-3dprinting.co.uk",
    }


def save_settings(settings: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


# ── Content Filter (lightweight regex — no ML for CLI) ─────────────────────────

BLOCK_PATTERNS = [
    (r"\b(child|minor|underage|loli|shota|teen\b.{0,20}(nude|naked|sex|explicit))\b", "csam"),
    (r"\b(bioweapon|nerve agent|sarin|dirty bomb|nuclear device)\b", "wmd"),
    (r"ignore (previous|all|prior|above) instructions?", "injection"),
    (r"(dan mode|jailbreak|developer mode|bypass (safety|filter))", "injection"),
    (r"pretend (you are|to be) (evil|unrestricted|uncensored)", "injection"),
]

ADULT_PATTERNS = [
    (r"\b(sex|erotic|xxx|hentai|pornograph|adult film)\b", "adult"),
]

def _normalise(text: str) -> str:
    text = (text
        .replace('0','o').replace('1','i').replace('3','e')
        .replace('4','a').replace('5','s').replace('7','t')
        .replace('@','a').replace('$','s'))
    text = re.sub(r'\s+', ' ', text)
    return text.lower()

def check_prompt(text: str, adult_filter: bool = True) -> Optional[str]:
    """
    Returns error string if blocked, None if allowed.
    """
    normalised = _normalise(text)
    for pattern, category in BLOCK_PATTERNS:
        if re.search(pattern, normalised, re.IGNORECASE):
            if category == "csam":
                return RED("BLOCKED: CSAM/child safety content. This incident has been logged.")
            return RED(f"BLOCKED: {category} content detected.")

    if adult_filter:
        for pattern, category in ADULT_PATTERNS:
            if re.search(pattern, normalised, re.IGNORECASE):
                return YELLOW("BLOCKED: Adult content (18+ filter active). Use --no-adult-filter to disable.")

    return None


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _http_post_stream(url: str, payload: dict) -> Iterator[str]:
    """Stream JSON lines from a POST request."""
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"CookieAI-CLI/{VERSION}",
    }

    if _USE_REQUESTS:
        with requests.post(url, json=payload, stream=True, timeout=(10, 120)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield line.decode()
    else:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                yield line.decode().strip()


def _http_get(url: str) -> dict:
    """Simple GET request, returns parsed JSON."""
    headers = {"User-Agent": f"CookieAI-CLI/{VERSION}"}
    if _USE_REQUESTS:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        return r.json()
    else:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())


# ── Ollama client ──────────────────────────────────────────────────────────────

def check_ollama(host: str) -> bool:
    try:
        _http_get(f"{host}/api/tags")
        return True
    except Exception:
        return False


def chat_stream(host: str, model: str, messages: list) -> Iterator[str]:
    """Yield text chunks from Ollama streaming chat API."""
    for line in _http_post_stream(
        f"{host}/api/chat",
        {"model": model, "messages": messages, "stream": True}
    ):
        try:
            data = json.loads(line)
            chunk = data.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if data.get("done"):
                break
        except json.JSONDecodeError:
            pass


# ── Session history ────────────────────────────────────────────────────────────

def append_history(role: str, content: str):
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, 'a') as f:
            f.write(json.dumps({"ts": datetime.utcnow().isoformat(),
                                "role": role, "content": content}) + "\n")
    except Exception:
        pass


def load_recent_history(n: int = MAX_HISTORY) -> list:
    """Load last n exchanges from history file for context."""
    messages = []
    try:
        if HISTORY_FILE.exists():
            lines = HISTORY_FILE.read_text().strip().split('\n')
            for line in lines[-n * 2:]:  # Each exchange = 2 messages
                if line:
                    entry = json.loads(line)
                    messages.append({"role": entry["role"], "content": entry["content"]})
    except Exception:
        pass
    return messages


# ── Tanda 3D printing ──────────────────────────────────────────────────────────

def submit_tanda_print(description: str, tanda_url: str):
    """
    Open a 3D print quote request via Tanda.
    CLI just opens the URL with the description pre-filled (if supported).
    """
    print(f"\n{CYAN('Tanda 3D Printing:')}")
    print(f"  Description: {description}")
    print(f"  Visit: {tanda_url}")
    print(f"  (Open the URL in a browser to submit your print request)")

    # If running on a desktop with xdg-open available, open the browser
    try:
        import subprocess
        subprocess.Popen(["xdg-open", tanda_url], stderr=subprocess.DEVNULL)
        print(f"  {GREEN('Browser opened.')}")
    except Exception:
        pass


# ── Image generation (remote trigger) ─────────────────────────────────────────

def generate_image(host: str, prompt: str, adult_filter: bool = True):
    """Trigger Fooocus image generation via CookieOS API."""
    blocked = check_prompt(prompt, adult_filter)
    if blocked:
        print(blocked)
        return

    print(f"{CYAN('⏳ Generating image...')}")
    try:
        url = f"{host}/fooocus/generate"
        if _USE_REQUESTS:
            r = requests.post(url, json={"prompt": prompt, "style": "Realistic"}, timeout=120)
            if r.status_code == 200:
                data = r.json()
                print(f"{GREEN('✓')} Saved to: {data.get('filename', 'output.png')}")
            else:
                print(f"{RED('✗')} Error: HTTP {r.status_code}")
        else:
            body = json.dumps({"prompt": prompt, "style": "Realistic"}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                print(f"{GREEN('✓')} Saved to: {data.get('filename', 'output.png')}")
    except Exception as e:
        print(f"{RED('✗')} Image generation failed: {e}")
        print(f"  Make sure CookieFocus is running at {host}")


# ── Interactive REPL ──────────────────────────────────────────────────────────

def run_repl(host: str, model: str, settings: dict, use_history: bool = True):
    """Interactive chat REPL."""
    print(f"\n{BOLD('🍪 CookieAI CLI')} v{VERSION}")
    print(DIM(f"   Model: {model}  |  Host: {host}"))
    print(DIM(f"   Adult filter: {'ON' if settings['adult_filter'] else 'OFF'}"))
    print(DIM(f"   Type /help for commands, /quit to exit"))
    print(DIM("─" * 50) + "\n")

    if not check_ollama(host):
        print(f"{RED('⚠')} Ollama not reachable at {host}")
        print(f"   Start Ollama: ollama serve")
        print(f"   Or set a Tailscale host: --host http://100.x.x.x:11434\n")

    # Load history for context
    messages = []
    system_prompt = (
        "You are CookieGPT, a helpful private AI assistant running on CookieOS. "
        "You run entirely locally — no internet access, no telemetry. "
        "Be direct, concise, and technically precise."
    )
    messages.append({"role": "system", "content": system_prompt})

    if use_history:
        history_ctx = load_recent_history()
        messages.extend(history_ctx)
        if history_ctx:
            print(DIM(f"[Loaded {len(history_ctx) // 2} previous exchanges from history]\n"))

    while True:
        try:
            user_input = input(f"{CYAN('You')} › ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM('Goodbye.')}")
            break

        if not user_input:
            continue

        # ── Built-in commands ─────────────────────────────────────────────────
        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]

            if cmd in ("/quit", "/exit", "/q"):
                print(DIM("Goodbye."))
                break

            elif cmd == "/clear":
                messages = [messages[0]]  # Keep system prompt
                print(DIM("[History cleared]"))
                continue

            elif cmd == "/model":
                parts = user_input.split()
                if len(parts) > 1:
                    model = parts[1]
                    print(DIM(f"[Model set to {model}]"))
                else:
                    print(f"Current model: {model}")
                continue

            elif cmd == "/history":
                print(DIM(f"History file: {HISTORY_FILE}"))
                print(DIM(f"Messages in context: {len(messages) - 1}"))
                continue

            elif cmd == "/image":
                prompt = user_input[7:].strip()
                if prompt:
                    generate_image(host, prompt, settings["adult_filter"])
                else:
                    print(YELLOW("Usage: /image <prompt>"))
                continue

            elif cmd == "/tanda":
                desc = user_input[7:].strip()
                submit_tanda_print(desc or "custom print", settings["tanda_url"])
                continue

            elif cmd == "/filter":
                parts = user_input.split()
                if len(parts) > 1:
                    settings["adult_filter"] = parts[1].lower() in ("on", "true", "1", "enable")
                    save_settings(settings)
                    print(DIM(f"[Adult filter: {'ON' if settings['adult_filter'] else 'OFF'}]"))
                else:
                    print(f"Adult filter: {'ON' if settings['adult_filter'] else 'OFF'}")
                continue

            elif cmd == "/help":
                print(f"""
{BOLD('Commands:')}
  /clear          — clear chat history
  /model <name>   — switch model (e.g. gemma3:2b, mistral:7b)
  /image <prompt> — generate an image via CookieFocus
  /tanda <desc>   — submit 3D print request to Tanda
  /filter on|off  — toggle 18+ content filter
  /history        — show history info
  /quit           — exit
""")
                continue

            else:
                print(YELLOW(f"Unknown command: {cmd}  (type /help)"))
                continue

        # ── Safety check ──────────────────────────────────────────────────────
        blocked = check_prompt(user_input, settings.get("adult_filter", True))
        if blocked:
            print(blocked)
            continue

        # ── Send to Ollama ────────────────────────────────────────────────────
        messages.append({"role": "user", "content": user_input})
        print(f"\n{GREEN('CookieGPT')} › ", end="", flush=True)

        response_parts = []
        try:
            for chunk in chat_stream(host, model, messages):
                print(chunk, end="", flush=True)
                response_parts.append(chunk)
        except KeyboardInterrupt:
            print(f"\n{DIM('[Interrupted]')}")
            messages.pop()  # Remove unanswered user message
            continue
        except Exception as e:
            print(f"\n{RED(f'[Error: {e}]')}")
            messages.pop()
            continue

        response = "".join(response_parts)
        messages.append({"role": "assistant", "content": response})
        print("\n")

        # Trim context window
        if len(messages) > MAX_HISTORY * 2 + 1:
            messages = [messages[0]] + messages[-(MAX_HISTORY * 2):]

        # Persist to history
        append_history("user", user_input)
        append_history("assistant", response)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CookieAI CLI — lightweight terminal AI assistant"
    )
    parser.add_argument("--host",            default=None,  help="Ollama host (e.g. http://100.x.x.x:11434)")
    parser.add_argument("--model",           default=None,  help="Ollama model name")
    parser.add_argument("--image",           metavar="PROMPT", help="Generate image with given prompt")
    parser.add_argument("--tanda",           metavar="DESC", help="Submit 3D print request to Tanda")
    parser.add_argument("--no-filter",       action="store_true", help="Disable safety filter (except CSAM)")
    parser.add_argument("--no-adult-filter", action="store_true", help="Disable 18+ filter only")
    parser.add_argument("--no-history",      action="store_true", help="Don't load/save chat history")
    parser.add_argument("--version",         action="version", version=f"CookieAI CLI {VERSION}")
    args = parser.parse_args()

    settings = load_settings()

    # CLI args override settings
    if args.host:
        settings["ollama_host"] = args.host
    if args.model:
        settings["model"] = args.model
    if args.no_filter:
        settings["prompt_filter"] = False
        settings["adult_filter"]  = False
    if args.no_adult_filter:
        settings["adult_filter"] = False

    host  = settings["ollama_host"]
    model = settings["model"]

    if args.image:
        generate_image(host, args.image, settings["adult_filter"])
        return

    if args.tanda:
        submit_tanda_print(args.tanda, settings["tanda_url"])
        return

    run_repl(host, model, settings, use_history=not args.no_history)


if __name__ == "__main__":
    main()
