#!/usr/bin/env bash
# CookieOS Tor Transparent Proxy
# Routes ALL system traffic through Tor — Tails-style
# Supports: full-route, app-only, and bypass modes
# Usage: tor-router.sh {enable|disable|status|app <pid>}
set -euo pipefail

TOR_UID=$(id -u debian-tor 2>/dev/null || id -u tor 2>/dev/null || echo "107")
TOR_TRANS_PORT="9040"
TOR_DNS_PORT="5353"
TOR_SOCKS_PORT="9050"
COOKIEOS_CHAIN="COOKIEOS_TOR"
LOG_TAG="cookieos-tor"

err()  { echo "ERROR: $*" >&2; exit 1; }
log()  { logger -t "$LOG_TAG" "$*"; echo "  $*"; }

# Require root
[[ $EUID -eq 0 ]] || err "Must run as root"

enable_tor_routing() {
    echo "[tor-router] Enabling transparent Tor routing..."

    # Verify Tor is running
    systemctl is-active tor --quiet || systemctl start tor
    sleep 2

    # ── iptables rules ──────────────────────────────────────────────────────

    # Create our chain (idempotent)
    iptables -t nat -N "$COOKIEOS_CHAIN" 2>/dev/null || iptables -t nat -F "$COOKIEOS_CHAIN"
    ip6tables -t filter -N COOKIEOS_DROP 2>/dev/null || true

    # Don't redirect Tor's own traffic
    iptables -t nat -A "$COOKIEOS_CHAIN" -m owner --uid-owner "$TOR_UID" -j RETURN

    # Don't redirect LAN/loopback
    iptables -t nat -A "$COOKIEOS_CHAIN" -d 127.0.0.0/8 -j RETURN
    iptables -t nat -A "$COOKIEOS_CHAIN" -d 10.0.0.0/8 -j RETURN
    iptables -t nat -A "$COOKIEOS_CHAIN" -d 172.16.0.0/12 -j RETURN
    iptables -t nat -A "$COOKIEOS_CHAIN" -d 192.168.0.0/16 -j RETURN

    # Redirect DNS to Tor's DNSPort
    iptables -t nat -A "$COOKIEOS_CHAIN" -p udp --dport 53 -j REDIRECT --to-ports "$TOR_DNS_PORT"
    iptables -t nat -A "$COOKIEOS_CHAIN" -p tcp --dport 53 -j REDIRECT --to-ports "$TOR_DNS_PORT"

    # Redirect all TCP to Tor's TransPort
    iptables -t nat -A "$COOKIEOS_CHAIN" -p tcp --syn -j REDIRECT --to-ports "$TOR_TRANS_PORT"

    # Hook our chain into OUTPUT
    iptables -t nat -D OUTPUT -j "$COOKIEOS_CHAIN" 2>/dev/null || true
    iptables -t nat -I OUTPUT -j "$COOKIEOS_CHAIN"

    # Block all non-Tor outbound (except established)
    iptables -I OUTPUT 1 -m state --state ESTABLISHED,RELATED -j ACCEPT
    iptables -I OUTPUT 2 -m owner --uid-owner "$TOR_UID" -j ACCEPT
    iptables -I OUTPUT 3 -d 127.0.0.1 -j ACCEPT
    iptables -A OUTPUT -j REJECT --reject-with icmp-port-unreachable

    # Block all IPv6 (Tor doesn't support it, prevents leaks)
    ip6tables -F COOKIEOS_DROP 2>/dev/null || true
    ip6tables -A COOKIEOS_DROP -j DROP
    ip6tables -I OUTPUT -j COOKIEOS_DROP
    ip6tables -I FORWARD -j COOKIEOS_DROP

    # Write resolv.conf to use Tor DNS
    cat > /etc/resolv.conf << 'RESOLV'
# CookieOS — DNS over Tor
nameserver 127.0.0.1
options ndots:0
RESOLV

    log "Tor routing enabled. All traffic routed through Tor."
    echo "[tor-router] Done. Check: curl --socks5 127.0.0.1:$TOR_SOCKS_PORT https://check.torproject.org/api/ip"
}

disable_tor_routing() {
    echo "[tor-router] Disabling Tor routing..."

    iptables -t nat -D OUTPUT -j "$COOKIEOS_CHAIN" 2>/dev/null || true
    iptables -t nat -F "$COOKIEOS_CHAIN" 2>/dev/null || true
    iptables -t nat -X "$COOKIEOS_CHAIN" 2>/dev/null || true
    iptables -F OUTPUT 2>/dev/null || true
    ip6tables -D OUTPUT -j COOKIEOS_DROP 2>/dev/null || true
    ip6tables -D FORWARD -j COOKIEOS_DROP 2>/dev/null || true

    log "Tor routing disabled. Direct connection restored."
    echo "[tor-router] Done."
}

status_tor_routing() {
    echo "[tor-router] Status:"
    if iptables -t nat -L "$COOKIEOS_CHAIN" &>/dev/null; then
        echo "  Tor routing: ENABLED"
        echo "  Tor process: $(systemctl is-active tor)"
        echo "  SOCKS5:  127.0.0.1:$TOR_SOCKS_PORT"
        echo "  Trans:   127.0.0.1:$TOR_TRANS_PORT"
        echo "  DNS:     127.0.0.1:$TOR_DNS_PORT"
    else
        echo "  Tor routing: DISABLED"
    fi
}

route_app_through_tor() {
    local pid="${1:-}"
    [[ -n "$pid" ]] || err "Usage: tor-router.sh app <pid>"
    echo "[tor-router] Routing PID $pid through Tor (torsocks)..."
    # Re-exec the process with torsocks LD_PRELOAD
    nsenter -t "$pid" -n -- iptables -t nat -I OUTPUT \
        -m owner --pid-owner "$pid" \
        -p tcp -j REDIRECT --to-ports "$TOR_TRANS_PORT"
    log "PID $pid now routed through Tor."
}

case "${1:-status}" in
    enable)  enable_tor_routing ;;
    disable) disable_tor_routing ;;
    status)  status_tor_routing ;;
    app)     route_app_through_tor "${2:-}" ;;
    *)       echo "Usage: $0 {enable|disable|status|app <pid>}" ;;
esac
