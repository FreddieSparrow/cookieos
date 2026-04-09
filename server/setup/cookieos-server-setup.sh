#!/usr/bin/env bash
# CookieOS Server Edition — Ubuntu Server Setup Script
# Turns a fresh Ubuntu 24.04 LTS server into a CookieNet node with:
#   - Hardened Ubuntu base
#   - Docker + Portainer
#   - Fooocus image AI (with NSFW filter)
#   - Ollama + Gemma 4 (auto-sized to server RAM)
#   - CookieCloud (Nextcloud AIO)
#   - CookieHost (Pterodactyl)
#   - Nginx reverse proxy + Let's Encrypt TLS
#   - Automated backups to CookieJar (Ceph)
#   - Fail2Ban, UFW, ClamAV
#
# Usage: bash cookieos-server-setup.sh [--domain cookiecloud.example.com] [--gpu]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(realpath "$SCRIPT_DIR/../..")"

# ── Configurable ─────────────────────────────────────────────────────────────
DOMAIN="${DOMAIN:-}"
ENABLE_GPU="${ENABLE_GPU:-false}"
INSTALL_NEXTCLOUD="${INSTALL_NEXTCLOUD:-true}"
INSTALL_AI="${INSTALL_AI:-true}"
INSTALL_PTERODACTYL="${INSTALL_PTERODACTYL:-false}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@cookiehost.uk}"
COOKIEOS_VERSION="${COOKIEOS_VERSION:-1.0.0}"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RESET='\033[0m'

log()  { echo -e "${BLUE}[server-setup]${RESET} $*"; }
ok()   { echo -e "${GREEN}[server-setup]${RESET} $*"; }
warn() { echo -e "${YELLOW}[server-setup] WARNING:${RESET} $*"; }
err()  { echo -e "${RED}[server-setup] ERROR:${RESET} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Must run as root (sudo bash $0)"

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --gpu)    ENABLE_GPU=true; shift ;;
        --no-nextcloud) INSTALL_NEXTCLOUD=false; shift ;;
        --no-ai)        INSTALL_AI=false; shift ;;
        --pterodactyl)  INSTALL_PTERODACTYL=true; shift ;;
        --email)  ADMIN_EMAIL="$2"; shift 2 ;;
        *) err "Unknown option: $1" ;;
    esac
done

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║     CookieOS Server Edition v${COOKIEOS_VERSION}                  ║${RESET}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""
log "Domain:     ${DOMAIN:-<none — localhost only>}"
log "GPU mode:   $ENABLE_GPU"
log "Nextcloud:  $INSTALL_NEXTCLOUD"
log "AI stack:   $INSTALL_AI"
log "Pterodactyl: $INSTALL_PTERODACTYL"
echo ""
read -rp "Continue? [y/N]: " confirm
[[ "$confirm" == "y" ]] || err "Aborted."

# ── 1. System update + base packages ─────────────────────────────────────────
log "Updating Ubuntu system..."
apt-get update -qq
apt-get upgrade -y
apt-get install -y \
    curl wget git unzip htop vim \
    ca-certificates gnupg lsb-release \
    ufw fail2ban clamav clamav-daemon \
    apparmor apparmor-profiles apparmor-utils \
    python3 python3-pip python3-venv \
    jq netcat-openbsd \
    apt-transport-https \
    software-properties-common \
    rsync borgbackup

ok "Base packages installed."

# ── 2. Security hardening ─────────────────────────────────────────────────────
log "Applying CookieOS security hardening..."
bash "$ROOT_DIR/security/hardening/apply-hardening.sh" server

# UFW rules
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp     # HTTP (redirect to HTTPS)
ufw allow 443/tcp    # HTTPS
[[ "$INSTALL_PTERODACTYL" == "true" ]] && ufw allow 8080/tcp
ufw --force enable
ok "Firewall configured."

# Fail2Ban
cat > /etc/fail2ban/jail.d/cookieos.conf << 'F2B'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled  = true
port     = http,https

[nginx-limit-req]
enabled  = true
port     = http,https
F2B
systemctl enable fail2ban
systemctl restart fail2ban
ok "Fail2Ban configured."

# ── 3. Docker ─────────────────────────────────────────────────────────────────
log "Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$SUDO_USER" 2>/dev/null || true
fi
systemctl enable docker
ok "Docker installed."

# Docker compose plugin
if ! docker compose version &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi

# Portainer CE
log "Installing Portainer..."
docker volume create portainer_data 2>/dev/null || true
docker run -d \
    --name portainer \
    --restart=always \
    -p 127.0.0.1:9000:9000 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v portainer_data:/data \
    portainer/portainer-ce:latest 2>/dev/null || \
    docker start portainer 2>/dev/null || true
ok "Portainer running at http://127.0.0.1:9000"

# ── 4. Nginx + SSL ────────────────────────────────────────────────────────────
log "Installing Nginx..."
apt-get install -y nginx certbot python3-certbot-nginx

cat > /etc/nginx/sites-available/cookieos-base << NGINX
# CookieOS Server — base config
server {
    listen 80 default_server;
    server_name _;
    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy "no-referrer";
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()";
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';";
    return 301 https://\$host\$request_uri;
}
NGINX
ln -sf /etc/nginx/sites-available/cookieos-base /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

if [[ -n "$DOMAIN" ]]; then
    log "Requesting Let's Encrypt certificate for $DOMAIN..."
    certbot --nginx -d "$DOMAIN" --email "$ADMIN_EMAIL" \
        --agree-tos --non-interactive --redirect || \
        warn "Certbot failed — you may need to add DNS and run manually."
fi
ok "Nginx configured."

# ── 5. Nextcloud (CookieCloud) ────────────────────────────────────────────────
if [[ "$INSTALL_NEXTCLOUD" == "true" ]]; then
    log "Installing Nextcloud AIO (CookieCloud)..."
    mkdir -p /opt/cookiecloud

    cat > /opt/cookiecloud/docker-compose.yml << 'NC'
version: "3.8"
services:
  nextcloud-aio-mastercontainer:
    image: nextcloud/all-in-one:latest
    container_name: nextcloud-aio-mastercontainer
    restart: always
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - nextcloud_aio_mastercontainer:/mnt/docker-aio-config
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - APACHE_PORT=11000
      - APACHE_IP_BINDING=127.0.0.1
      - NEXTCLOUD_DATADIR=/opt/cookiecloud/data
      - SKIP_DOMAIN_VALIDATION=false
volumes:
  nextcloud_aio_mastercontainer:
NC

    docker compose -f /opt/cookiecloud/docker-compose.yml up -d
    ok "CookieCloud (Nextcloud) starting at http://127.0.0.1:8080"
    log "  → Complete setup at https://$DOMAIN:8080 after DNS is configured"
fi

# ── 6. Ollama + Gemma 4 (auto-sized) ─────────────────────────────────────────
if [[ "$INSTALL_AI" == "true" ]]; then
    log "Installing Ollama..."
    if ! command -v ollama &>/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    systemctl enable ollama
    systemctl start ollama
    sleep 3

    # Auto-select model based on available RAM
    log "Detecting server RAM to select Gemma 4 variant..."
    TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
    log "Available RAM: ${TOTAL_RAM_GB}GB"

    if   (( TOTAL_RAM_GB >= 22 )); then MODEL="gemma4:31b"
    elif (( TOTAL_RAM_GB >= 18 )); then MODEL="gemma4:26b"
    elif (( TOTAL_RAM_GB >= 10 )); then MODEL="gemma4:4b"
    else                                MODEL="gemma4:2b"
    fi

    log "Pulling $MODEL (this may take a while)..."
    ollama pull "$MODEL"
    ok "Ollama + $MODEL ready."

    # Create systemd service for CookieChat API
    cat > /etc/systemd/system/cookiechat.service << SVCEOF
[Unit]
Description=CookieChat AI API
After=ollama.service
Requires=ollama.service

[Service]
Type=simple
User=www-data
WorkingDirectory=$ROOT_DIR/ai/ollama
ExecStart=/usr/bin/python3 cookie-ollama.py --server
Restart=always
RestartSec=5
Environment=COOKIEOS_MODEL=$MODEL
Environment=OLLAMA_HOST=http://127.0.0.1:11434

[Install]
WantedBy=multi-user.target
SVCEOF
    systemctl daemon-reload
    systemctl enable cookiechat

    # GPU mode
    if [[ "$ENABLE_GPU" == "true" ]]; then
        log "Setting up NVIDIA GPU support for Ollama..."
        distribution="$(. /etc/os-release; echo "$ID$VERSION_ID")"
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
            sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -s -L "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
            sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
        apt-get update && apt-get install -y nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
        ok "NVIDIA GPU support enabled."
    fi
fi

# ── 7. Fooocus AI Image Server ─────────────────────────────────────────────────
if [[ "$INSTALL_AI" == "true" ]]; then
    log "Setting up CookieFocus (Fooocus) image generation..."
    mkdir -p /opt/cookiefocus
    python3 -m venv /opt/cookiefocus/venv
    /opt/cookiefocus/venv/bin/pip install -q \
        torch torchvision --index-url https://download.pytorch.org/whl/cpu
    /opt/cookiefocus/venv/bin/pip install -q \
        transformers accelerate pillow requests

    # Install safeguards
    cp "$ROOT_DIR/ai/safeguards/content_filter.py" /opt/cookiefocus/
    cp "$ROOT_DIR/ai/fooocus/cookie-fooocus.py" /opt/cookiefocus/

    cat > /etc/systemd/system/cookiefocus.service << FOCUSEOF
[Unit]
Description=CookieFocus Image Generation API
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/cookiefocus
ExecStart=/opt/cookiefocus/venv/bin/python cookie-fooocus.py --server --host 127.0.0.1 --port 7866
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
FOCUSEOF
    systemctl daemon-reload
    systemctl enable cookiefocus
    ok "CookieFocus service configured."
fi

# ── 8. Pterodactyl (CookieHost) ───────────────────────────────────────────────
if [[ "$INSTALL_PTERODACTYL" == "true" ]]; then
    log "Installing Pterodactyl panel (CookieHost)..."
    bash <(curl -s https://pterodactyl-installer.se) <<< "0"  # Panel install
    ok "Pterodactyl panel installed."
fi

# ── 9. Automated backup ───────────────────────────────────────────────────────
log "Configuring automated backups..."
mkdir -p /opt/cookiebackup

cat > /opt/cookiebackup/backup.sh << 'BACKUP'
#!/bin/bash
# CookieOS Server Daily Backup
BORG_PASSPHRASE="${BORG_PASSPHRASE:-change-me-in-env}"
REPO="/opt/cookiebackup/repo"
SOURCE="/opt/cookiecloud/data /etc"

borg init --encryption=repokey "$REPO" 2>/dev/null || true
borg create \
    --stats --progress \
    "$REPO::cookieos-{now}" \
    $SOURCE \
    --exclude-caches \
    --exclude '/opt/cookiecloud/data/*/files_trashbin'
borg prune --keep-daily=7 --keep-weekly=4 "$REPO"
BACKUP
chmod +x /opt/cookiebackup/backup.sh

cat > /etc/systemd/system/cookiebackup.timer << 'TIMER'
[Unit]
Description=CookieOS Daily Backup Timer

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER

cat > /etc/systemd/system/cookiebackup.service << 'BSVC'
[Unit]
Description=CookieOS Backup

[Service]
Type=oneshot
ExecStart=/opt/cookiebackup/backup.sh
BSVC
systemctl daemon-reload
systemctl enable cookiebackup.timer
ok "Daily backup configured (3:00 AM)."

# ── 10. Final report ──────────────────────────────────────────────────────────
echo ""
ok "═══════════════════════════════════════════════════"
ok " CookieOS Server Edition — setup complete!"
ok "═══════════════════════════════════════════════════"
echo ""
log "Services:"
[[ "$INSTALL_NEXTCLOUD"  == "true" ]] && log "  CookieCloud (Nextcloud):  http://127.0.0.1:8080"
[[ "$INSTALL_AI"         == "true" ]] && log "  CookieChat  (Ollama):     http://127.0.0.1:11434"
[[ "$INSTALL_AI"         == "true" ]] && log "  CookieFocus (Fooocus):    http://127.0.0.1:7866"
log "  Portainer:                http://127.0.0.1:9000"
[[ "$INSTALL_PTERODACTYL" == "true" ]] && log "  CookieHost  (Pterodactyl): http://127.0.0.1:8080"
echo ""
warn "Next steps:"
warn "  1. Set BORG_PASSPHRASE in /etc/environment for encrypted backups"
warn "  2. Configure your domain in /etc/nginx/sites-available/"
warn "  3. Run: certbot --nginx -d your.domain.com"
warn "  4. Start services: systemctl start cookiechat cookiefocus"
echo ""
