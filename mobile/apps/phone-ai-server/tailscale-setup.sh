#!/usr/bin/env bash
# CookieOS Tailscale Setup — Phone AI Server + Desktop/Server nodes
#
# Tailscale gives every CookieNet device a stable 100.x.x.x IP over WireGuard.
# The phone AI server becomes available at http://100.x.x.x:11434 from any
# device in the tailnet, regardless of which network they're on.
#
# Usage:
#   Android (Termux): bash tailscale-setup.sh phone
#   Linux desktop:    sudo bash tailscale-setup.sh desktop
#   Ubuntu server:    sudo bash tailscale-setup.sh server
#
# After setup, all devices in the CookieNet tailnet can reach each other.
set -euo pipefail

ROLE="${1:-desktop}"
TAILNET_NAME="${TAILNET_NAME:-cookienet}"
# Use Tailscale's magic DNS — devices register as:
#   phone.cookienet.ts.net
#   desktop.cookienet.ts.net
#   cookieos-server.cookienet.ts.net

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'
log() { echo -e "${BLUE}[tailscale]${RESET} $*"; }
ok()  { echo -e "${GREEN}[tailscale]${RESET} $*"; }
err() { echo -e "${RED}[tailscale] ERROR:${RESET} $*" >&2; exit 1; }

install_linux() {
    log "Installing Tailscale on Linux..."
    curl -fsSL https://tailscale.com/install.sh | sh
}

install_termux() {
    log "Installing Tailscale in Termux (Android)..."
    # Tailscale has an official Android app, but Termux users can use the CLI
    pkg install -y wget
    # Download pre-built Tailscale binary for Android arm64
    ARCH=$(uname -m)
    case "$ARCH" in
        aarch64) TS_ARCH="arm64" ;;
        armv7l)  TS_ARCH="arm"   ;;
        x86_64)  TS_ARCH="amd64" ;;
        *)        err "Unsupported arch: $ARCH" ;;
    esac
    TS_VERSION="1.66.3"
    TS_URL="https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_${TS_ARCH}.tgz"
    log "Downloading Tailscale $TS_VERSION for $TS_ARCH..."
    wget -q "$TS_URL" -O /tmp/tailscale.tgz
    tar -xzf /tmp/tailscale.tgz -C /tmp
    cp "/tmp/tailscale_${TS_VERSION}_${TS_ARCH}/tailscale"  "$PREFIX/bin/"
    cp "/tmp/tailscale_${TS_VERSION}_${TS_ARCH}/tailscaled" "$PREFIX/bin/"
    rm -rf /tmp/tailscale.tgz "/tmp/tailscale_${TS_VERSION}_${TS_ARCH}"
    ok "Tailscale binaries installed to $PREFIX/bin/"

    log "Creating Termux tailscaled startup script..."
    mkdir -p "$PREFIX/var/service/tailscaled"
    cat > "$PREFIX/var/service/tailscaled/run" << 'TSRUN'
#!/data/data/com.termux/files/usr/bin/sh
exec tailscaled --tun=userspace-networking --statedir=$PREFIX/var/tailscale 2>&1
TSRUN
    chmod +x "$PREFIX/var/service/tailscaled/run"
    ok "tailscaled service configured."
    log "Start with: sv start tailscaled"
}

configure_phone() {
    log "Configuring Tailscale for phone AI server role..."
    HOSTNAME="cookie-phone-$(hostname | tr '[:upper:]' '[:lower:]' | tr -dc 'a-z0-9-')"

    if [[ -n "${ANDROID_ROOT:-}" ]]; then
        # Android/Termux — userspace networking
        tailscale up \
            --hostname="$HOSTNAME" \
            --accept-dns=true \
            --accept-routes=false \
            --advertise-tags=tag:cookieos-ai
    else
        sudo tailscale up \
            --hostname="$HOSTNAME" \
            --accept-dns=true
    fi

    TS_IP=$(tailscale ip -4 2>/dev/null || echo "pending")
    ok "Phone AI server Tailscale IP: $TS_IP"
    log "From CookieOS or CookieAI App, connect to: http://${TS_IP}:11434"
    log "Or use MagicDNS: http://${HOSTNAME}.${TAILNET_NAME}.ts.net:11434"
}

configure_desktop() {
    log "Configuring Tailscale for CookieOS Desktop..."
    sudo tailscale up \
        --hostname="cookieos-$(hostname | tr '[:upper:]' '[:lower:]')" \
        --accept-dns=true \
        --accept-routes=true

    TS_IP=$(tailscale ip -4)
    ok "Desktop Tailscale IP: $TS_IP"

    # Update CookieAI app config to show phone discovery note
    CC_CONFIG="$HOME/.config/cookiecloud/config.json"
    if [[ -f "$CC_CONFIG" ]]; then
        python3 -c "
import json, sys
cfg = json.load(open('$CC_CONFIG'))
cfg['tailscale_enabled'] = True
json.dump(cfg, open('$CC_CONFIG', 'w'), indent=2)
print('[tailscale] Updated CookieCloud config')
"
    fi
}

configure_server() {
    log "Configuring Tailscale for CookieOS Server..."
    sudo tailscale up \
        --hostname="cookieos-server-$(hostname | tr '[:upper:]' '[:lower:]')" \
        --accept-dns=true \
        --advertise-tags=tag:cookieos-server \
        --ssh  # Enable Tailscale SSH

    TS_IP=$(tailscale ip -4)
    ok "Server Tailscale IP: $TS_IP"
    log "Tailscale SSH enabled — connect with: ssh root@${TS_IP}"

    # Restrict Ollama to Tailscale interface only (more secure)
    TS_IFACE=$(ip -o link show | awk '{print $2}' | grep tailscale | head -1 | tr -d ':')
    if [[ -n "$TS_IFACE" ]]; then
        log "Binding Ollama to Tailscale interface ($TS_IFACE)..."
        sed -i "s/OLLAMA_HOST=.*/OLLAMA_HOST=${TS_IP}:11434/" \
            /etc/systemd/system/cookiechat.service 2>/dev/null || true
        systemctl daemon-reload
        systemctl restart ollama 2>/dev/null || true
        ok "Ollama now only accessible via Tailscale ($TS_IP:11434)"
    fi

    # UFW: allow Tailscale subnet
    ufw allow in on "$TS_IFACE" to any port 11434 comment "Ollama via Tailscale"
    ufw allow in on "$TS_IFACE" to any port 7866  comment "Fooocus via Tailscale"
    ufw reload
}

print_qr() {
    # Print a QR code of the Tailscale auth URL for easy phone enrollment
    AUTHURL=$(tailscale up --qr 2>&1 | grep "https://" | head -1)
    if command -v qrencode &>/dev/null; then
        echo "$AUTHURL" | qrencode -t ANSI256
    else
        log "Auth URL: $AUTHURL"
        log "  (Install qrencode for QR code display)"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "$ROLE" in
phone)
    if [[ -n "${ANDROID_ROOT:-}" ]]; then
        install_termux
    fi
    configure_phone
    print_qr
    ;;
desktop)
    install_linux
    configure_desktop
    print_qr
    ;;
server)
    install_linux
    configure_server
    print_qr
    ;;
*)
    echo "Usage: $0 {phone|desktop|server}"
    echo "  phone    — Install on Android (Termux)"
    echo "  desktop  — Install on CookieOS Desktop"
    echo "  server   — Install on CookieOS Server"
    ;;
esac

echo ""
ok "Tailscale setup complete!"
log "All CookieNet devices on the same tailnet can now reach each other."
log "Phone AI (Gemma 2B) is reachable from desktop/app at:"
log "  http://<phone-tailscale-ip>:11434"
log "  or http://cookie-phone-<name>.<tailnet>.ts.net:11434"
