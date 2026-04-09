#!/usr/bin/env bash
# CookieOS Full-Disk Encryption Setup
# LUKS2 with Argon2id KDF — similar to GrapheneOS/Tails encrypted storage
# Usage: luks-setup.sh {format|open|close|enroll-tpm2} <device>
set -euo pipefail

CONTAINER_NAME="cookieos-crypt"
MOUNT_POINT="/mnt/cookieos-data"
ARGON2_TIME=4
ARGON2_MEM=1048576   # 1 GiB
ARGON2_THREADS=4

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[luks] $*"; }

[[ $EUID -eq 0 ]] || err "Must run as root"

cmd="${1:-help}"
device="${2:-}"

case "$cmd" in
# ── Format new encrypted volume ──────────────────────────────────────────────
format)
    [[ -n "$device" ]] || err "Usage: luks-setup.sh format <device>"
    [[ -b "$device" ]] || err "Not a block device: $device"

    log "Formatting $device with LUKS2 (Argon2id)..."
    log "WARNING: This will ERASE all data on $device"
    read -rp "Type 'yes' to confirm: " confirm
    [[ "$confirm" == "yes" ]] || err "Aborted."

    # Secure wipe first 4MiB (header area)
    dd if=/dev/urandom of="$device" bs=1M count=4 status=progress

    cryptsetup luksFormat \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha512 \
        --pbkdf argon2id \
        --pbkdf-time "$ARGON2_TIME" \
        --pbkdf-memory "$ARGON2_MEM" \
        --pbkdf-parallel "$ARGON2_THREADS" \
        --sector-size 4096 \
        --label "COOKIEOS-DATA" \
        "$device"

    log "LUKS2 container created on $device"
    log "Use 'luks-setup.sh open $device' to unlock it"
    ;;

# ── Open/unlock ──────────────────────────────────────────────────────────────
open)
    [[ -n "$device" ]] || err "Usage: luks-setup.sh open <device>"
    log "Unlocking $device..."
    cryptsetup luksOpen "$device" "$CONTAINER_NAME"
    mkdir -p "$MOUNT_POINT"
    mount /dev/mapper/"$CONTAINER_NAME" "$MOUNT_POINT"
    log "Mounted at $MOUNT_POINT"
    ;;

# ── Close/lock ───────────────────────────────────────────────────────────────
close)
    log "Closing encrypted container..."
    umount "$MOUNT_POINT" 2>/dev/null || true
    cryptsetup luksClose "$CONTAINER_NAME"
    log "Container locked."
    ;;

# ── Enroll TPM2 for auto-unlock (desktop) ────────────────────────────────────
enroll-tpm2)
    [[ -n "$device" ]] || err "Usage: luks-setup.sh enroll-tpm2 <device>"
    command -v systemd-cryptenroll &>/dev/null || err "systemd-cryptenroll not available"

    log "Enrolling TPM2 PCRs 0+1+2+3+7 for auto-unlock on $device..."
    # PCR 7 = Secure Boot state; PCR 0/1/2/3 = firmware/config
    systemd-cryptenroll \
        --tpm2-device=auto \
        --tpm2-pcrs="0+1+2+3+7" \
        "$device"
    log "TPM2 enrollment complete. Disk will auto-unlock if boot chain is unchanged."
    log "If Secure Boot is violated, you will be prompted for password."
    ;;

# ── Create encrypted swap ────────────────────────────────────────────────────
setup-swap)
    [[ -n "$device" ]] || err "Usage: luks-setup.sh setup-swap <device>"
    log "Setting up encrypted swap on $device..."
    cat >> /etc/crypttab << EOF
cryptswap  $device  /dev/urandom  swap,cipher=aes-xts-plain64,size=512,hash=sha512
EOF
    cat >> /etc/fstab << EOF
/dev/mapper/cryptswap  none  swap  sw,pri=10  0  0
EOF
    log "Encrypted swap configured (ephemeral key per boot)"
    ;;

# ── Create persistent encrypted home ─────────────────────────────────────────
setup-home)
    local size="${3:-50G}"
    log "Creating encrypted home container (${size})..."
    mkdir -p /home/cookie
    fallocate -l "$size" /home/cookie/.home-crypt
    cryptsetup luksFormat \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --pbkdf argon2id \
        /home/cookie/.home-crypt
    log "Encrypted home created. Run 'luks-setup.sh open /home/cookie/.home-crypt' to use."
    ;;

help|*)
    echo "CookieOS LUKS Encryption Manager"
    echo ""
    echo "Usage: luks-setup.sh <command> [device]"
    echo ""
    echo "  format       <device>    Format device with LUKS2/Argon2id"
    echo "  open         <device>    Unlock and mount container"
    echo "  close                    Lock and unmount container"
    echo "  enroll-tpm2  <device>    Enable TPM2 auto-unlock"
    echo "  setup-swap   <device>    Configure ephemeral encrypted swap"
    echo "  setup-home               Create encrypted home volume"
    ;;
esac
