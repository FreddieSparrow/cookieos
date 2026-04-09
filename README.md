# CookieOS

**Private. Secure. Yours.**

CookieOS is a privacy-first, security-hardened operating system family — part of the **[CookieNet](https://cookiehost.uk)** ecosystem, a distributed infrastructure providing secure cloud storage, self-hosted AI, and private networking.

> *"Keep your data under your control."*

CookieOS combines the best of GrapheneOS (hardened security), Linux Mint (usability), and Tails OS (anonymity). It integrates natively with CookieCloud, CookieHost, and on-device AI — the full CookieNet stack. All AI runs **entirely locally on your device**. No telemetry, no cloud dependency, no data ever leaves your hardware.

**Need help?** Email: **support@techtesting.tech** | **CookieCloud:** https://cookiecloud.techtesting.tech

---

## About CookieNet

CookieOS is part of the wider **CookieNet** ecosystem — a bedroom datacenter running Docker, Kubernetes, and Ubiquiti networking on a leased-line uplink with a public IPv4 range and a /46 IPv6 block:

| Service | What it does |
|---------|-------------|
| **CookieCloud** | Self-hosted private cloud (Nextcloud): storage, meetings, messaging, passwords, photo management |
| **CookieHost** | Pterodactyl-based server hosting — game servers, web, bots, code, databases |
| **CookieGPT** | Self-trained LLM running on CookieNet — the only AI that never touches your data |
| **CookieOven** | Kubernetes cluster for AI workloads |
| **CookieJar** | Ceph distributed storage cluster |
| **Netform** | Home network design service using Ubiquiti |
| **T&A 3D Printing** | 3D printing business (F1 in Schools award winner) |

---

## Variants

| Variant | Base | Target |
|---------|------|--------|
| **CookieOS Desktop** | Debian 12 (Bookworm) | x86_64 laptops & desktops |
| **CookieOS Mobile** | AOSP (Android 16) — degoogled | ARM64 phones & tablets |
| **CookieOS Server** | Ubuntu 24.04 LTS | Servers, refurbished hardware, mini PCs |
| **CookieAI App** | Toga/Briefcase (cross-platform) | Linux · Windows · macOS · Android |

---

## Key Features

### Privacy
- **Tor routing** — one-click route all traffic through Tor (Tails-style)
- **MAC address randomisation** — per connection, auto-configured via NetworkManager
- **Browser fingerprint protection** — Canvas, WebGL, AudioContext, font list, UA spoofing
- **System-wide tracker blocking** — hardened `/etc/hosts` (Microsoft, Google, Meta, Amazon telemetry all blocked)
- **IPv6 privacy extensions** — enabled by default

### Security
- **Hardened kernel** — CFI, KASLR, ASLR, shadow call stack, IOMMU, Retpoline, PTI, stackprotector
- **Full-disk encryption** — LUKS2 with Argon2id KDF (1GB memory cost); optional TPM2 auto-unlock
- **Encrypted swap** — ephemeral random key per boot
- **AppArmor enforcement** — profiles for all system apps
- **SELinux** — on Android/Mobile variant
- **Verified boot** — dm-verity (mobile), UEFI Secure Boot (desktop)
- **Automatic security updates** — unattended-upgrades for security packages only

### BackupCloud & CookieCloud Integration
- **Multi-provider backups** — CookieCloud, UGreen NAS (SMB/NFS), S3 (Wasabi, AWS), Backblaze, custom providers
- **Automatic incremental backups** — Desktop & Mobile both support scheduled backups to multiple providers
- **Native sync client** — two-way delta sync with your CookieCloud instance
- **Encrypted transport over Tailscale** — WireGuard VPN between all your devices
- **Offline-capable** — fully usable without server connection

**Backup Support:**
- 🖥️ **Desktop (Linux)** — `cookiecloud/sync/backup-manager.py` — backup to any provider
- 📱 **Android** — `mobile/apps/threat-detector-android/` — automatic daily APK/data backups
- ⛅ **Providers:** CookieCloud, SMB/NFS shares (UGreen), S3-compatible (Wasabi, DigitalOcean), B2, SFTP

### AI (fully local — no cloud)

| Feature | Model | Requirement |
|---------|-------|-------------|
| **CookieChat** | Gemma 4 (auto-selected by device) | Any device |
| **CookieFocus** | Fooocus / SDXL | 8GB+ RAM |
| **CookieVideo** | Stable Diffusion Video (SVD) | **12GB+ VRAM** |
| **Phone AI** | Gemma 3 2B on Android | 4GB+ RAM phone |

All AI features include:
- **Prompt safety filter** (CSAM, WMD, prompt injection — absolute hard block)
- **NSFW image/video output filter** (CLIP-based, runs locally)
- **Per-user rate limiting**
- **Encrypted local audit log**
- **AI-powered threat detection** — auto-analyzes binaries/APKs, generates patches
- **Safety tools are protected** — cannot be deleted before AI execution

---

## AI Model Auto-Selection

CookieOS detects available RAM/VRAM and the user's chosen power level, then selects the best Gemma 4 variant:

| Device RAM | Power: Fast | Power: Balanced | Power: Quality |
|-----------|-------------|-----------------|----------------|
| < 8 GB | gemma4:2b (7.2GB) | gemma4:2b | gemma4:2b |
| 8–14 GB | gemma4:2b | **gemma4:4b** (9.6GB) | gemma4:4b |
| 15–21 GB | gemma4:4b | **gemma4:26b** (18GB) | gemma4:26b |
| 22 GB+ | gemma4:26b | **gemma4:31b** (20GB) | gemma4:31b |

**Phone (Android):** always uses `gemma3:2b` (~1.7GB), served over Tailscale.

```bash
# Check what model will be selected for your device
python3 ai/ollama/model-selector.py --power balanced

# Phone recommendation
python3 ai/ollama/model-selector.py --phone
```

---

## Tailscale — Private Network Between All Devices

All CookieOS/CookieAI devices join the same **Tailscale tailnet** (private WireGuard mesh). This means:

- Phone AI server reachable from desktop at `http://100.x.x.x:11434` from anywhere
- CookieCloud sync works across networks without port-forwarding
- All AI and CookieHost services are **Tailscale-only** (not exposed to public internet)

```bash
# Desktop
sudo bash mobile/apps/phone-ai-server/tailscale-setup.sh desktop

# Server
sudo bash mobile/apps/phone-ai-server/tailscale-setup.sh server

# Android (Termux)
bash mobile/apps/phone-ai-server/tailscale-setup.sh phone

# Start phone AI server (Gemma 2B, serves to tailnet)
python3 mobile/apps/phone-ai-server/phone_ai_server.py start

# Auto-discover phone AI servers on the tailnet
python3 mobile/apps/phone-ai-server/phone_ai_server.py scan
```

---

## CookieAI — Standalone App (no CookieOS required)

Works on **Linux, Windows, macOS, and Android** — install without running CookieOS.

```bash
pip install briefcase
cd ai/ui/cookieai-app

briefcase run           # Run directly
briefcase build         # Native app binary
briefcase package       # Installer package
briefcase build android # Android APK
```

**Features:**
- 💬 Chat with Gemma 4 (local Ollama, auto model selection)
- 🖼 Image generation (CookieFocus / Fooocus — local SDXL)
- 🎬 Video generation (SVD — 12GB+ VRAM devices only)
- 📁 **Personal file context (RAG)** — drop any document/PDF/code into AI context
- ☁ Optional CookieCloud sync
- All safety filters active

---

## CookieVideo — AI Video Generation

Requires **12GB+ VRAM**. On lower-spec devices, CookieOS will tell you clearly and suggest using a CookieHost GPU server instead.

```bash
# Check your GPU compatibility
python3 ai/fooocus/cookie-video.py check

# Animate an image (12GB+ VRAM)
python3 ai/fooocus/cookie-video.py img2vid photo.png --frames 25 --motion 150

# Generate from text prompt (runs Fooocus first to make keyframe, then SVD)
python3 ai/fooocus/cookie-video.py txt2vid "A red panda exploring a forest at dawn"
```

---

## AI Automation + YouTube Pipeline *(Optional)*

An optional component that installs **n8n** (self-hosted workflow automation) and a complete AI-to-YouTube pipeline:

1. Gemma 4 writes a video script
2. Fooocus generates visuals
3. CookieOS safety filters check everything
4. Uploads automatically to YouTube

```bash
# Optional install — asks for explicit consent + password
sudo bash automation/install-automation.sh

# Manual pipeline run
python3 automation/youtube/yt-upload.py --auth          # First-time YouTube auth
python3 automation/youtube/yt-upload.py --test --topic "How Tailscale works"
python3 automation/youtube/yt-upload.py --topic "Building a home datacenter"
```

n8n UI accessible **only via Tailscale** at `http://<your-tailscale-ip>:5678`.

---

## Desktop Build

```bash
# Install build deps
sudo bash scripts/check-deps.sh

# Full desktop ISO
make -C build desktop
make -C build iso

# Amnesic live image (Tails-style — no writes to disk)
make -C build live
```

---

## Mobile Build (AOSP / degoogled Android)

```bash
export TARGET_DEVICE=generic_arm64
export BUILD_TYPE=user
bash build/android/build-aosp.sh
```

The build script:
- Syncs Android 16 AOSP source
- Applies CookieOS security patches + degoogle script (removes all Google Play Services dependencies)
- Injects CookieCloud client, privacy hosts file, SELinux policies
- Produces flashable `system.img`, `vendor.img`, `boot.img`, `vbmeta.img`

---

## Server Setup

```bash
# Fresh Ubuntu 24.04 server
sudo bash server/setup/cookieos-server-setup.sh \
    --domain cookiecloud.yourdomain.com \
    --email you@example.com

# With NVIDIA GPU (for AI)
sudo bash server/setup/cookieos-server-setup.sh \
    --domain your.domain.com \
    --gpu

# With CookieHost (Pterodactyl game/web hosting)
sudo bash server/setup/cookieos-server-setup.sh \
    --domain your.domain.com \
    --pterodactyl
```

**What gets installed automatically:**
- Hardened Ubuntu base (sysctl hardening, AppArmor, Fail2Ban, UFW)
- Docker + Portainer CE
- Nextcloud AIO (CookieCloud)
- Ollama + auto-selected Gemma 4 model
- CookieFocus (Fooocus image generation API)
- Tailscale
- Nginx + Let's Encrypt TLS
- Automated Borg backups (daily 3 AM)

---

## Project Structure

```
cookieos/
├── build/
│   ├── Makefile                        Master build system
│   ├── linux/                          Debian rootfs build (Desktop)
│   └── android/build-aosp.sh          AOSP build + CookieOS patches
├── core/
│   ├── kernel/kernel.config            Hardened kernel configuration
│   └── kernel/Makefile                 Kernel build (LLVM/Clang)
├── desktop/
│   └── shell/cookiebar/cookiebar.py    CookieBar GTK4 panel
├── mobile/
│   ├── patches/                        AOSP security + degoogle patches
│   ├── apps/phone-ai-server/           Gemma 2B phone server + Tailscale
│   └── apps/threat-detector-android/   Kotlin APK scanner + SELinux patches
├── privacy/
│   ├── network/tor-router.sh           Transparent Tor routing
│   ├── network/hosts                   Privacy hosts file
│   ├── storage/luks-setup.sh           LUKS2 / TPM2 encryption
│   └── identity/fingerprint-spoof.py  Browser fingerprint protection
├── security/
│   ├── hardening/apply-hardening.sh   sysctl, PAM, AppArmor, UFW
│   ├── ai-defense/threat-detector.py  AI-powered virus detection + auto-patching
│   └── ai-defense/cookie-shield.py    Real-time threat monitoring
├── cookiecloud/
│   ├── client/cookiecloud-client.py   Native CookieCloud sync client
│   └── sync/backup-manager.py         Multi-provider backup orchestration
├── ai/
│   ├── safeguards/content-filter.py   Shared safety layer (all AI)
│   ├── ollama/cookie-ollama.py        CookieChat (Gemma 4 + RAG)
│   ├── ollama/model-selector.py       Device-aware model selection
│   ├── fooocus/cookie-fooocus.py      CookieFocus image generation
│   ├── fooocus/cookie-video.py        CookieVideo (SVD, 12GB+ VRAM)
│   └── ui/cookieai-app/               Cross-platform standalone app
├── server/
│   └── setup/cookieos-server-setup.sh Ubuntu server setup
└── automation/
    ├── install-automation.sh          Optional: n8n + YouTube pipeline
    ├── youtube/yt-upload.py           AI content → YouTube
    └── workflows/youtube-ai-pipeline.json  n8n workflow
```

---

## CookieNet Active & Planned Projects

| Project | Status | Description |
|---------|--------|-------------|
| CookieCloud | ✅ Active | Private Nextcloud for family & friends |
| CookieHost | ✅ Active | Game, web, bot, code, database hosting |
| CookieOS | ✅ This repo | Private OS family |
| Netform | ✅ Active | Home network design (Ubiquiti) |
| T&A 3D Printing | ✅ Active | 3D printing (F1 in Schools award winner) |
| CookieGPT | 🔨 Building | Fine-tuned LLM on CookieOven K8s cluster |
| CookieJar | 🔨 Planned | Ceph distributed storage cluster |
| CookieOven | 🔨 Planned | Kubernetes cluster for AI workloads |
| Project Lifeboat | 🔒 | |
| Project Tardigrade | 🔒 | |
| Project Redstone | 🔒 | |
| Project Vault | 🔒 | |
| Project Eve | 🔒 | |
| Project Cortex | 🔒 | |
| Project Switchboard | 🔒 | |

---

*Built by Adam — 16, networking addict, red panda admirer, Minecraft nerd, Beat Saber enthusiast, and die-hard chocoholic running infrastructure that probably shouldn't exist in a bedroom.*

*Nothing is getting in the way of CookieNet.*
