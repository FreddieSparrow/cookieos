# CookieOS — Claude Project Context

## Project owner
Sir, 16 — creator of CookieNet. Expert-level Linux, networking (Ubiquiti), Docker, K8s.
Talk to him like a senior dev. No hand-holding. Direct and technical.

## What this project is
CookieOS is a multi-variant private OS family:
- **Desktop**: Debian 12, Cookie Desktop Environment, hardened kernel
- **Mobile**: AOSP Android 16, degoogled, SELinux hardened
- **Server**: Ubuntu 24.04 automated setup
- **CookieAI App**: Cross-platform standalone (Toga/Briefcase)

## Key principles
1. **Everything local** — AI, storage, sync all run on the user's own hardware
2. **Security by default** — hardened kernel, AppArmor, LUKS2, Tor routing
3. **CookieNet integration** — CookieCloud (Nextcloud), CookieHost (Pterodactyl), Tailscale
4. **No telemetry** — zero external phone-home anywhere

## AI stack
- Gemma 4 variants (2b/4b/26b/31b) auto-selected by device RAM/VRAM + user power level
- Phone: Gemma 3 2B on Android (Termux), served over Tailscale
- Fooocus for image generation; SVD for video (12GB+ VRAM only)
- Shared safety filter (content-filter.py) wraps ALL AI features
- n8n + YouTube pipeline (optional, consent + password required to install)

## Networking
All inter-device connectivity via **Tailscale** WireGuard — phone AI, CookieCloud sync, server access.

## AOSP auto-update
mobile/patches/scripts/aosp-autoupdate.sh tracks upstream AOSP tags and auto-applies CookieOS patches when Google releases security updates — no manual CookieOS release needed.

## Style
- Python for AI/tools, Bash for system scripts, Makefile for builds
- No unnecessary abstractions — keep it direct
- Safety checks are mandatory on all AI paths, not optional
- All server-side services bind to Tailscale interface only (not public internet)
