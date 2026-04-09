#!/usr/bin/env bash
# CookieOS Mobile — AOSP Build Script
# Based on AOSP with GrapheneOS-style hardening patches
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(realpath "$SCRIPT_DIR/../..")"
AOSP_DIR="${AOSP_DIR:-$HOME/aosp/cookieos}"
COOKIEOS_VERSION="${COOKIEOS_VERSION:-1.0.0}"
TARGET_DEVICE="${TARGET_DEVICE:-generic_arm64}"
BUILD_TYPE="${BUILD_TYPE:-user}"          # user | userdebug | eng

# Colours
RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'

log()  { echo -e "${BLUE}[android]${RESET} $*"; }
ok()   { echo -e "${GREEN}[android]${RESET} $*"; }
err()  { echo -e "${RED}[android] ERROR:${RESET} $*" >&2; exit 1; }

# ── Prerequisite check ──────────────────────────────────────────────────────
check_deps() {
    log "Checking AOSP build dependencies..."
    local missing=()
    for cmd in repo python3 make git curl java openjdk-17; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    [[ ${#missing[@]} -gt 0 ]] && err "Missing: ${missing[*]}. Run: scripts/setup-aosp-deps.sh"
    ok "All dependencies present."
}

# ── Sync AOSP sources ───────────────────────────────────────────────────────
sync_aosp() {
    log "Syncing AOSP sources (Android 16 / API 36)..."
    mkdir -p "$AOSP_DIR"
    cd "$AOSP_DIR"

    if [[ ! -f .repo/manifest.xml ]]; then
        repo init \
            --manifest-url https://android.googlesource.com/platform/manifest \
            --manifest-branch android-16.0.0_r1 \
            --depth=1 \
            --no-clone-bundle
    fi

    repo sync -c -j"$(nproc)" --no-tags --no-clone-bundle --force-sync
    ok "AOSP sync complete."
}

# ── Apply CookieOS patches ───────────────────────────────────────────────────
apply_patches() {
    log "Applying CookieOS security and privacy patches..."
    cd "$AOSP_DIR"

    local patch_dir="$ROOT_DIR/mobile/patches"

    # Kernel hardening patches
    for patch in "$patch_dir"/kernel/*.patch; do
        [[ -f "$patch" ]] || continue
        log "  Applying kernel patch: $(basename "$patch")"
        git -C kernel apply "$patch" || log "  (already applied or skipped)"
    done

    # Framework privacy patches (disable advertising ID, hardened permissions)
    for patch in "$patch_dir"/framework/*.patch; do
        [[ -f "$patch" ]] || continue
        log "  Applying framework patch: $(basename "$patch")"
        git -C frameworks/base apply "$patch" || log "  (already applied or skipped)"
    done

    # Remove Google Play Services stubs (degoogled build)
    log "  Removing Google Services dependencies..."
    bash "$patch_dir/scripts/degoogle.sh" "$AOSP_DIR"

    ok "Patches applied."
}

# ── Copy CookieOS apps and configs ──────────────────────────────────────────
inject_cookieos() {
    log "Injecting CookieOS apps and configuration..."
    cd "$AOSP_DIR"

    # CookieCloud client APK (pre-built from cookiecloud/client)
    cp "$ROOT_DIR/out/packages/CookieCloud.apk" \
       packages/apps/CookieCloud/CookieCloud.apk 2>/dev/null || true

    # Privacy-focused hosts file (blocks ads/trackers)
    cp "$ROOT_DIR/privacy/network/hosts" \
       system/core/rootdir/etc/hosts

    # SELinux policies
    cp -r "$ROOT_DIR/security/selinux/cookieos/." \
       system/sepolicy/private/

    # Cookie-branded bootanimation
    cp "$ROOT_DIR/mobile/apps/bootanimation/bootanimation.zip" \
       frameworks/base/data/sounds/ 2>/dev/null || true

    ok "CookieOS assets injected."
}

# ── Configure build ──────────────────────────────────────────────────────────
configure_build() {
    log "Configuring AOSP build for $TARGET_DEVICE ($BUILD_TYPE)..."
    cd "$AOSP_DIR"

    # Source envsetup
    # shellcheck disable=SC1091
    source build/envsetup.sh

    lunch "${TARGET_DEVICE}-${BUILD_TYPE}"
    ok "Build configured."
}

# ── Build image ─────────────────────────────────────────────────────────────
build_image() {
    log "Starting AOSP build (this will take a while)..."
    cd "$AOSP_DIR"

    # shellcheck disable=SC1091
    source build/envsetup.sh
    lunch "${TARGET_DEVICE}-${BUILD_TYPE}"

    m -j"$(nproc)" \
        systemimage \
        vendorimage \
        bootimage \
        vbmetaimage

    ok "AOSP image build complete."
    log "Output: $AOSP_DIR/out/target/product/$TARGET_DEVICE/"
}

# ── Copy final images to CookieOS out/ ──────────────────────────────────────
collect_artifacts() {
    local img_src="$AOSP_DIR/out/target/product/$TARGET_DEVICE"
    local img_dst="$ROOT_DIR/out/images/android"
    mkdir -p "$img_dst"

    for img in system.img vendor.img boot.img vbmeta.img; do
        [[ -f "$img_src/$img" ]] && cp "$img_src/$img" "$img_dst/"
    done
    ok "Images collected at: $img_dst"
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    log "CookieOS Mobile Build — v$COOKIEOS_VERSION"
    log "Device: $TARGET_DEVICE | Type: $BUILD_TYPE"
    echo ""

    check_deps
    sync_aosp
    apply_patches
    inject_cookieos
    configure_build
    build_image
    collect_artifacts

    ok "Done! CookieOS Mobile image ready."
}

main "$@"
