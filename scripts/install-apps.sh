#!/usr/bin/env bash
# CookieOS App Installer
# Automates installation of apps on CookieOS Desktop (Debian 12) and Android (via Termux).
#
# Usage:
#   ./install-apps.sh [OPTIONS]
#
# Options:
#   --all              Install everything
#   --session          Install Session messenger
#   --signal           Install Signal (Linux) or prompt for Android
#   --fdroid           Install F-Droid (Android/Termux) or equivalent repos
#   --cookieai         Install CookieAI Python app + CLI
#   --updater          Install and enable CookieOS auto-updater (daemon)
#   --tanda            Install Tanda 3D printing client deps
#   --android          Run Android-specific setup (via Termux)
#   --dry-run          Show what would be installed without doing it
#   --help             Show this help

set -euo pipefail

# ── Colour output ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*" >&2; }
section() { echo -e "\n${BOLD}── $* ──────────────────────────────────────${NC}"; }

# ── Detect environment ─────────────────────────────────────────────────────────

IS_ANDROID=false
IS_LINUX=false
IS_TERMUX=false
PKG_MANAGER=""
DRY_RUN=false
COOKIEOS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -d "/data/data/com.termux" ]] || [[ "${TERMUX_VERSION:-}" != "" ]]; then
    IS_ANDROID=true
    IS_TERMUX=true
    PKG_MANAGER="pkg"
    info "Detected: Termux on Android"
elif [[ "$(uname -s)" == "Linux" ]]; then
    IS_LINUX=true
    if command -v apt-get &>/dev/null; then
        PKG_MANAGER="apt"
        info "Detected: Debian/Ubuntu Linux"
    elif command -v pacman &>/dev/null; then
        PKG_MANAGER="pacman"
        info "Detected: Arch Linux"
    else
        warn "Unknown Linux package manager — some installs may need manual steps"
    fi
else
    error "Unsupported platform: $(uname -s)"
    exit 1
fi

# ── Helper functions ───────────────────────────────────────────────────────────

run() {
    if $DRY_RUN; then
        echo "  [DRY RUN] $*"
    else
        "$@"
    fi
}

apt_install() {
    if $DRY_RUN; then
        echo "  [DRY RUN] apt-get install -y $*"
        return
    fi
    if [[ "$PKG_MANAGER" == "apt" ]]; then
        sudo apt-get install -y "$@"
    elif [[ "$PKG_MANAGER" == "pacman" ]]; then
        sudo pacman -S --noconfirm "$@" 2>/dev/null || warn "pacman: could not install $*"
    fi
}

pkg_install() {
    # Termux pkg
    if $DRY_RUN; then
        echo "  [DRY RUN] pkg install -y $*"
        return
    fi
    pkg install -y "$@"
}

check_root() {
    if [[ $EUID -ne 0 ]] && ! $IS_TERMUX; then
        warn "Not running as root — some installs may fail. Run with sudo if needed."
    fi
}

# ── Session Messenger ──────────────────────────────────────────────────────────

install_session_linux() {
    section "Session Messenger (Linux)"
    if command -v session-desktop &>/dev/null; then
        success "Session already installed"
        return
    fi

    info "Installing Session messenger..."

    # Session AppImage (no snap/flatpak — both have telemetry issues)
    local LATEST_URL
    LATEST_URL=$(curl -sf "https://api.github.com/repos/oxen-io/session-desktop/releases/latest" \
        | python3 -c "import sys,json; data=json.load(sys.stdin); \
          assets=[a for a in data['assets'] if a['name'].endswith('.AppImage')]; \
          print(assets[0]['browser_download_url'] if assets else '')")

    if [[ -z "$LATEST_URL" ]]; then
        warn "Could not fetch Session download URL. Manual install: https://getsession.org/linux"
        return
    fi

    local DEST="/opt/session/session-desktop.AppImage"
    run sudo mkdir -p /opt/session
    run sudo curl -L "$LATEST_URL" -o "$DEST"
    run sudo chmod +x "$DEST"

    # Desktop entry
    run sudo tee /usr/share/applications/session-desktop.desktop > /dev/null <<EOF
[Desktop Entry]
Name=Session
Comment=Private messenger
Exec=/opt/session/session-desktop.AppImage --no-sandbox
Icon=session-desktop
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
EOF

    success "Session installed at $DEST"
    info "Run: /opt/session/session-desktop.AppImage"
}

install_session_android() {
    section "Session Messenger (Android)"
    info "Session for Android: install via F-Droid or direct APK"
    echo ""
    echo "  Option 1 (F-Droid — recommended):"
    echo "    1. Install F-Droid first (--fdroid)"
    echo "    2. Search 'Session' in F-Droid"
    echo ""
    echo "  Option 2 (direct APK):"
    echo "    1. Visit: https://github.com/oxen-io/session-android/releases"
    echo "    2. Download latest .apk"
    echo "    3. Enable 'Install from unknown sources' in Android settings"
    echo "    4. Install the APK"
}

# ── Signal ─────────────────────────────────────────────────────────────────────

install_signal_linux() {
    section "Signal (Linux)"
    if command -v signal-desktop &>/dev/null; then
        success "Signal already installed"
        return
    fi

    info "Adding Signal apt repository..."
    if $DRY_RUN; then
        echo "  [DRY RUN] Would add Signal repo and install signal-desktop"
        return
    fi

    # Official Signal Linux install (no Google, no snap)
    wget -qO- https://updates.signal.org/desktop/apt/keys.asc \
        | sudo gpg --dearmor -o /usr/share/keyrings/signal-desktop-keyring.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/signal-desktop-keyring.gpg] https://updates.signal.org/desktop/apt xenial main" \
        | sudo tee /etc/apt/sources.list.d/signal-xenial.list
    sudo apt-get update -q
    sudo apt-get install -y signal-desktop

    success "Signal installed"
}

# ── F-Droid (Android/Termux) ───────────────────────────────────────────────────

install_fdroid_android() {
    section "F-Droid (Android)"
    echo ""
    info "F-Droid: privacy-respecting Android app store (no Google Play)"
    echo ""
    echo "  Installation steps:"
    echo "    1. Visit: https://f-droid.org"
    echo "    2. Download the F-Droid APK"
    echo "    3. Enable 'Install from unknown sources' in Android security settings"
    echo "    4. Install the APK"
    echo ""
    echo "  Recommended F-Droid apps:"
    echo "    - Session          (private messaging)"
    echo "    - Briar            (P2P encrypted messaging)"
    echo "    - NewPipe          (YouTube without Google)"
    echo "    - Aegis            (2FA authenticator)"
    echo "    - Calyx VPN / Mullvad"
    echo "    - Nextcloud        (CookieCloud sync)"
    echo ""

    if $IS_TERMUX; then
        info "Via Termux: you can also use 'pkg install fdroidcl' for CLI access to F-Droid"
        if ! command -v fdroidcl &>/dev/null; then
            pkg_install fdroidcl 2>/dev/null || warn "fdroidcl not available in your Termux repos"
        fi
    fi
}

# ── CookieAI Python App ────────────────────────────────────────────────────────

install_cookieai_python() {
    section "CookieAI Python App"

    # Check Python
    if ! command -v python3 &>/dev/null; then
        info "Installing Python 3..."
        if $IS_TERMUX; then
            pkg_install python
        else
            apt_install python3 python3-pip python3-venv
        fi
    fi

    local PYTHON="python3"
    local PY_VERSION
    PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
    info "Python: $PY_VERSION"

    # Install pip deps
    info "Installing CookieAI dependencies..."
    local DEPS="requests"

    # Toga (GUI) only on non-Termux
    if ! $IS_TERMUX; then
        DEPS="$DEPS toga briefcase"
    fi

    run $PYTHON -m pip install --quiet --upgrade $DEPS

    # CookieAI CLI — make executable
    local CLI_PATH="$COOKIEOS_ROOT/ai/cli/cookieai-cli.py"
    if [[ -f "$CLI_PATH" ]]; then
        run chmod +x "$CLI_PATH"
        # Symlink to PATH
        if [[ -w /usr/local/bin ]] || $IS_TERMUX; then
            run ln -sf "$CLI_PATH" "${PREFIX:-/usr/local}/bin/cookieai"
            success "cookieai CLI available as: cookieai"
        else
            run sudo ln -sf "$CLI_PATH" /usr/local/bin/cookieai
            success "cookieai CLI installed to /usr/local/bin/cookieai"
        fi
    fi

    # Optional: transformers for ML injection detection
    info "Install ML safety classifier? (~400MB download, optional)"
    read -rp "  Install transformers + ML classifier? [y/N]: " INSTALL_ML
    if [[ "${INSTALL_ML,,}" == "y" ]]; then
        run $PYTHON -m pip install --quiet transformers torch --index-url https://download.pytorch.org/whl/cpu
        success "ML classifier dependencies installed"
    else
        info "Skipping ML classifier — regex-only filtering active"
    fi
}

# ── Auto-Updater Daemon ────────────────────────────────────────────────────────

install_updater() {
    section "CookieOS Auto-Updater"
    local UPDATER="$COOKIEOS_ROOT/auto-update/updater.py"

    if [[ ! -f "$UPDATER" ]]; then
        error "Updater not found at $UPDATER"
        return
    fi

    if $IS_LINUX && command -v systemctl &>/dev/null; then
        info "Installing systemd user service for auto-updater..."
        local SERVICE_DIR="$HOME/.config/systemd/user"
        run mkdir -p "$SERVICE_DIR"

        if ! $DRY_RUN; then
            cat > "$SERVICE_DIR/cookieos-updater.service" <<EOF
[Unit]
Description=CookieOS Auto-Updater
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${UPDATER} --daemon
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
EOF
            systemctl --user daemon-reload
            systemctl --user enable cookieos-updater.service
            systemctl --user start cookieos-updater.service
            success "Auto-updater service enabled and started"
            info "Check status: systemctl --user status cookieos-updater"
        else
            echo "  [DRY RUN] Would create and enable systemd user service"
        fi
    elif $IS_TERMUX; then
        info "Termux: adding auto-updater to ~/.bashrc cron-style..."
        # Termux doesn't have systemd — use a background process check
        local BASHRC="$HOME/.bashrc"
        local CHECK_LINE="python3 $UPDATER --check 2>/dev/null &"
        if ! grep -q "cookieos-updater" "$BASHRC" 2>/dev/null; then
            run echo -e "\n# CookieOS auto-updater check\n$CHECK_LINE" >> "$BASHRC"
            success "Update check added to .bashrc (runs on Termux start)"
        else
            info "Update check already in .bashrc"
        fi
    else
        warn "Could not install updater daemon. Run manually: python3 $UPDATER --daemon &"
    fi
}

# ── Tanda Client ───────────────────────────────────────────────────────────────

install_tanda() {
    section "Tanda 3D Printing Client"
    local TANDA_CLIENT="$COOKIEOS_ROOT/integrations/tanda/tanda_client.py"

    if [[ -f "$TANDA_CLIENT" ]]; then
        run chmod +x "$TANDA_CLIENT"
        if [[ -w /usr/local/bin ]] || $IS_TERMUX; then
            run ln -sf "$TANDA_CLIENT" "${PREFIX:-/usr/local}/bin/tanda"
        else
            run sudo ln -sf "$TANDA_CLIENT" /usr/local/bin/tanda
        fi
        success "tanda command available: tanda --help"
    else
        warn "Tanda client not found at $TANDA_CLIENT"
    fi
}

# ── Tailscale ──────────────────────────────────────────────────────────────────

install_tailscale() {
    section "Tailscale"
    if command -v tailscale &>/dev/null; then
        success "Tailscale already installed"
        return
    fi

    info "Installing Tailscale..."
    if $IS_TERMUX; then
        pkg_install tailscale
    elif $IS_LINUX; then
        run curl -fsSL https://tailscale.com/install.sh | sudo sh
        success "Tailscale installed. Run: sudo tailscale up"
    fi
}

# ── Ollama ─────────────────────────────────────────────────────────────────────

install_ollama() {
    section "Ollama (Local AI)"
    if command -v ollama &>/dev/null; then
        success "Ollama already installed ($(ollama --version 2>/dev/null || echo 'unknown version'))"
        return
    fi

    info "Installing Ollama..."
    if $IS_TERMUX; then
        pkg_install ollama 2>/dev/null || {
            warn "Ollama not in Termux repos. Trying direct binary..."
            curl -fsSL https://ollama.com/install.sh | sh || \
                warn "Ollama install failed on Android. Manual install required."
        }
    else
        run curl -fsSL https://ollama.com/install.sh | sh
        success "Ollama installed. Start with: ollama serve"
        info "Pull default model: ollama pull gemma3:4b"
    fi
}

# ── Parse arguments ────────────────────────────────────────────────────────────

INSTALL_SESSION=false
INSTALL_SIGNAL=false
INSTALL_FDROID=false
INSTALL_COOKIEAI=false
INSTALL_UPDATER=false
INSTALL_TANDA=false
INSTALL_TAILSCALE=false
INSTALL_OLLAMA=false
INSTALL_ALL=false

for arg in "$@"; do
    case "$arg" in
        --all)        INSTALL_ALL=true ;;
        --session)    INSTALL_SESSION=true ;;
        --signal)     INSTALL_SIGNAL=true ;;
        --fdroid)     INSTALL_FDROID=true ;;
        --cookieai)   INSTALL_COOKIEAI=true ;;
        --updater)    INSTALL_UPDATER=true ;;
        --tanda)      INSTALL_TANDA=true ;;
        --tailscale)  INSTALL_TAILSCALE=true ;;
        --ollama)     INSTALL_OLLAMA=true ;;
        --dry-run)    DRY_RUN=true; info "DRY RUN mode — nothing will be installed" ;;
        --help|-h)
            grep '^# ' "$0" | head -20 | sed 's/^# //'
            exit 0
            ;;
        *)
            warn "Unknown option: $arg"
            ;;
    esac
done

if $INSTALL_ALL; then
    INSTALL_SESSION=true
    INSTALL_SIGNAL=true
    INSTALL_FDROID=true
    INSTALL_COOKIEAI=true
    INSTALL_UPDATER=true
    INSTALL_TANDA=true
    INSTALL_TAILSCALE=true
    INSTALL_OLLAMA=true
fi

if ! $INSTALL_SESSION && ! $INSTALL_SIGNAL && ! $INSTALL_FDROID && \
   ! $INSTALL_COOKIEAI && ! $INSTALL_UPDATER && ! $INSTALL_TANDA && \
   ! $INSTALL_TAILSCALE && ! $INSTALL_OLLAMA && ! $INSTALL_ALL; then
    echo -e "\n${BOLD}CookieOS App Installer${NC}"
    echo "Usage: $0 [--all] [--session] [--signal] [--fdroid] [--cookieai] [--updater] [--tanda] [--tailscale] [--ollama] [--dry-run]"
    echo ""
    echo "Available options:"
    echo "  --all           Install everything"
    echo "  --session       Install Session messenger"
    echo "  --signal        Install Signal messenger"
    echo "  --fdroid        Install F-Droid (Android) instructions"
    echo "  --cookieai      Install CookieAI Python app + CLI"
    echo "  --updater       Install auto-updater daemon"
    echo "  --tanda         Install Tanda 3D printing client"
    echo "  --tailscale     Install Tailscale"
    echo "  --ollama        Install Ollama (local AI)"
    echo "  --dry-run       Show what would be done without executing"
    exit 0
fi

# ── Main install sequence ──────────────────────────────────────────────────────

echo -e "\n${BOLD}🍪 CookieOS App Installer${NC}"
echo -e "   Platform: ${CYAN}$(uname -s) ($([[ $IS_TERMUX == true ]] && echo Termux || echo native))${NC}"
echo -e "   CookieOS root: ${CYAN}$COOKIEOS_ROOT${NC}"
$DRY_RUN && echo -e "   ${YELLOW}DRY RUN — no changes will be made${NC}"
echo ""

check_root

$INSTALL_TAILSCALE && install_tailscale
$INSTALL_OLLAMA    && install_ollama

if $INSTALL_SESSION; then
    if $IS_ANDROID; then install_session_android
    else install_session_linux; fi
fi

if $INSTALL_SIGNAL; then
    if $IS_LINUX; then install_signal_linux
    else warn "Signal Linux only — for Android, install via F-Droid"; fi
fi

$INSTALL_FDROID    && install_fdroid_android
$INSTALL_COOKIEAI  && install_cookieai_python
$INSTALL_UPDATER   && install_updater
$INSTALL_TANDA     && install_tanda

echo ""
success "Installation complete."
echo ""
echo "  Next steps:"
echo "  1. Start Ollama:          ollama serve"
echo "  2. Pull a model:          ollama pull gemma3:4b"
echo "  3. Run CookieAI CLI:      cookieai"
echo "  4. Connect Tailscale:     sudo tailscale up"
echo ""
