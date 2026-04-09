#!/usr/bin/env bash
# CookieOS Build Dependency Checker
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}  [✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}  [!]${RESET} $*"; }
fail() { echo -e "${RED}  [✗]${RESET} $*"; MISSING=1; }

MISSING=0
echo "Checking CookieOS build dependencies..."
echo ""

check() {
    local cmd="$1" pkg="${2:-$1}"
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd"
    else
        fail "$cmd (install: apt-get install $pkg)"
    fi
}

check debootstrap
check xorriso
check mksquashfs squashfs-tools
check grub-mkrescue grub-efi-amd64-bin
check python3
check pip3 python3-pip
check git
check make
check curl
check gpg gnupg2
check dpkg
check docker
check ollama "see: https://ollama.com/install.sh"

echo ""
if [[ "$MISSING" -eq 0 ]]; then
    echo -e "${GREEN}All dependencies satisfied.${RESET}"
else
    echo -e "${RED}Missing dependencies above. Install them and re-run.${RESET}"
    exit 1
fi
