#!/usr/bin/env bash
# CookieOS AI Automation Stack — Optional Install
# Installs:
#   - n8n (workflow automation)
#   - CookieAI YouTube pipeline (Ollama script + Fooocus + auto-upload)
#   - All configured as Docker services, accessible via Tailscale
#
# This is an OPTIONAL component. You must explicitly consent to install it.
# A password is required to protect the n8n admin panel.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(realpath "$SCRIPT_DIR/..")"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RESET='\033[0m'
log()  { echo -e "${BLUE}[automation]${RESET} $*"; }
ok()   { echo -e "${GREEN}[automation]${RESET} $*"; }
warn() { echo -e "${YELLOW}[automation]${RESET} $*"; }
err()  { echo -e "${RED}[automation] ERROR:${RESET} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Must run as root (sudo bash $0)"

# ── Consent gate ─────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${YELLOW}║  CookieOS AI Automation — Optional Component                ║${RESET}"
echo -e "${YELLOW}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${YELLOW}║  This will install:                                          ║${RESET}"
echo -e "${YELLOW}║   • n8n workflow automation (self-hosted)                   ║${RESET}"
echo -e "${YELLOW}║   • YouTube AI content pipeline (Ollama + Fooocus)          ║${RESET}"
echo -e "${YELLOW}║   • Docker services + Tailscale-only access                 ║${RESET}"
echo -e "${YELLOW}║                                                              ║${RESET}"
echo -e "${YELLOW}║  AI-generated content will pass through CookieOS safety     ║${RESET}"
echo -e "${YELLOW}║  filters before upload. You retain full control.            ║${RESET}"
echo -e "${YELLOW}║                                                              ║${RESET}"
echo -e "${YELLOW}║  YouTube API credentials and n8n workflows are stored       ║${RESET}"
echo -e "${YELLOW}║  locally — nothing is sent to CookieNet servers.            ║${RESET}"
echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

read -rp "Do you want to install the AI Automation stack? [yes/no]: " consent
[[ "$consent" == "yes" ]] || { echo "Installation cancelled."; exit 0; }

echo ""
echo -e "${BLUE}Set a password for the n8n admin panel:${RESET}"
while true; do
    read -rsp "Password: " N8N_PASS; echo
    read -rsp "Confirm:  " N8N_PASS2; echo
    [[ "$N8N_PASS" == "$N8N_PASS2" ]] && break
    warn "Passwords do not match. Try again."
done
[[ ${#N8N_PASS} -ge 12 ]] || err "Password must be at least 12 characters."

N8N_USER="${N8N_USER:-cookieadmin}"
N8N_PORT=5678
INSTALL_DIR=/opt/cookieos-automation
TS_IP=$(tailscale ip -4 2>/dev/null || echo "127.0.0.1")

echo ""
log "Installing CookieOS AI Automation..."

# ── Docker check ──────────────────────────────────────────────────────────────
command -v docker &>/dev/null || err "Docker is required. Run server setup first."
docker compose version &>/dev/null || apt-get install -y docker-compose-plugin

# ── Create install directory ──────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"/{n8n-data,workflows,youtube-output}
chmod 700 "$INSTALL_DIR"

# ── Copy automation files ─────────────────────────────────────────────────────
cp -r "$SCRIPT_DIR/workflows/"*.json   "$INSTALL_DIR/workflows/" 2>/dev/null || true
cp    "$SCRIPT_DIR/youtube/yt-upload.py" "$INSTALL_DIR/"

# ── Write docker-compose ──────────────────────────────────────────────────────
cat > "$INSTALL_DIR/docker-compose.yml" << COMPOSE
version: "3.8"

services:
  # ── n8n automation engine ──────────────────────────────────────────────────
  n8n:
    image: n8nio/n8n:latest
    container_name: cookieos-n8n
    restart: unless-stopped
    ports:
      - "${TS_IP}:${N8N_PORT}:5678"    # Only accessible via Tailscale
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=${N8N_USER}
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASS}
      - N8N_HOST=${TS_IP}
      - N8N_PORT=${N8N_PORT}
      - N8N_PROTOCOL=http
      - WEBHOOK_URL=http://${TS_IP}:${N8N_PORT}/
      - GENERIC_TIMEZONE=Europe/London
      - N8N_LOG_LEVEL=warn
      # Disable external module loading for security
      - NODE_FUNCTION_ALLOW_EXTERNAL=
      - N8N_DISABLE_UI=false
      # CookieOS integration — point at local Ollama + Fooocus
      - COOKIEOS_OLLAMA_URL=http://host.docker.internal:11434
      - COOKIEOS_FOOOCUS_URL=http://host.docker.internal:7866
    volumes:
      - n8n-data:/home/node/.n8n
      - ${INSTALL_DIR}/workflows:/workflows:ro
      - ${INSTALL_DIR}/youtube-output:/output
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:5678/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  n8n-data:
COMPOSE

# ── UFW: allow n8n only via Tailscale ────────────────────────────────────────
TS_IFACE=$(ip -o link show | awk '{print $2}' | grep tailscale | head -1 | tr -d ':' || true)
if [[ -n "$TS_IFACE" ]]; then
    ufw allow in on "$TS_IFACE" to any port "$N8N_PORT" comment "n8n via Tailscale"
    ufw reload
    ok "n8n accessible only via Tailscale ($TS_IFACE)"
fi

# ── Start services ────────────────────────────────────────────────────────────
log "Starting n8n..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d

# ── Install Python deps for YouTube pipeline ─────────────────────────────────
log "Installing YouTube upload dependencies..."
python3 -m pip install -q \
    google-api-python-client \
    google-auth-httplib2 \
    google-auth-oauthlib \
    moviepy \
    pydub \
    requests

# ── Import default workflows into n8n ────────────────────────────────────────
sleep 5   # Wait for n8n to start
log "Importing CookieOS workflows into n8n..."
for wf in "$INSTALL_DIR/workflows/"*.json; do
    [[ -f "$wf" ]] || continue
    curl -s -X POST \
        -u "${N8N_USER}:${N8N_PASS}" \
        -H "Content-Type: application/json" \
        -d "@$wf" \
        "http://127.0.0.1:${N8N_PORT}/api/v1/workflows" > /dev/null && \
        log "  Imported: $(basename "$wf")" || \
        warn "  Could not import $(basename "$wf") — import manually via UI"
done

# ── Write credentials reminder ────────────────────────────────────────────────
cat > "$INSTALL_DIR/SETUP-REQUIRED.txt" << NOTES
CookieOS AI Automation — First-Time Setup Required
====================================================

n8n is running. To complete setup:

1. Open n8n: http://${TS_IP}:${N8N_PORT}
   Login: ${N8N_USER} / <password you set>

2. YouTube credentials:
   - Go to console.cloud.google.com
   - Create OAuth 2.0 credentials (YouTube Data API v3)
   - Download client_secret.json → $INSTALL_DIR/client_secret.json
   - Run: python3 $INSTALL_DIR/yt-upload.py --auth
   - This stores your token locally (never leaves this server)

3. Activate workflows in n8n:
   - "CookieAI YouTube — Daily AI Video"
   - Configure schedule and topic list in workflow settings

4. Test run:
   python3 $INSTALL_DIR/yt-upload.py --test

NOTES

echo ""
ok "═══════════════════════════════════════════════════"
ok " CookieOS AI Automation installed!"
ok "═══════════════════════════════════════════════════"
log "n8n UI:    http://${TS_IP}:${N8N_PORT}"
log "User:      ${N8N_USER}"
log "See:       $INSTALL_DIR/SETUP-REQUIRED.txt"
