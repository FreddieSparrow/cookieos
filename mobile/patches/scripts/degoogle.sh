#!/usr/bin/env bash
# CookieOS Degoogle Script
# Removes Google Play Services dependencies and surveillance components from AOSP.
# Inspired by GrapheneOS degoogling approach.
# Usage: degoogle.sh <aosp-root>
set -euo pipefail

AOSP_ROOT="${1:-$PWD}"
cd "$AOSP_ROOT"

log() { echo "[degoogle] $*"; }
ok()  { echo "[degoogle] ✓ $*"; }

log "Removing Google Play Services and surveillance from AOSP..."

# ── Remove GMS/Play Services packages ────────────────────────────────────────
GMS_PACKAGES=(
    "vendor/google/packages/PrebuiltGmsCoreQt"
    "vendor/google/packages/GoogleServicesFramework"
    "vendor/google/packages/Phonesky"          # Play Store
    "vendor/google/apps/Maps"
    "vendor/google/apps/YouTube"
    "vendor/google/apps/Gmail"
    "vendor/google/apps/Chrome"
    "packages/apps/GoogleSearch"
    "packages/apps/QuickSearchBox"
)

for pkg in "${GMS_PACKAGES[@]}"; do
    if [[ -d "$pkg" ]]; then
        rm -rf "$pkg"
        log "  Removed: $pkg"
    fi
done
ok "GMS packages removed."

# ── Patch device-specific makefiles to remove GMS references ─────────────────
log "Patching product makefiles..."
find device/ vendor/ -name "*.mk" -type f 2>/dev/null | while read -r mk; do
    sed -i \
        -e '/GmsCore\|GoogleServices\|PrebuiltGms\|Phonesky\|PlayStore/d' \
        -e '/com\.google\.android\.gms/d' \
        -e '/gms_mandatory/d' \
        "$mk" 2>/dev/null || true
done
ok "Product makefiles patched."

# ── Disable Google Analytics / Firebase in framework ─────────────────────────
log "Disabling Firebase / Crashlytics hooks..."
FIREBASE_DIRS=(
    "external/firebase"
    "external/crashlytics"
    "external/google-breakpad"
)
for d in "${FIREBASE_DIRS[@]}"; do
    [[ -d "$d" ]] && rm -rf "$d" && log "  Removed: $d"
done

# ── Remove Google SafetyNet / Play Integrity hooks ───────────────────────────
log "Removing Play Integrity / SafetyNet..."
find frameworks/ -name "*.java" -type f | xargs grep -l "SafetyNet\|PlayIntegrity\|Attestation" \
    2>/dev/null | while read -r f; do
    sed -i '/SafetyNet\|PlayIntegrity\|Attestation/d' "$f" 2>/dev/null || true
done

# ── Disable captive portal check (phones home to Google) ─────────────────────
log "Redirecting captive portal check to neutral server..."
SETTINGS_DB_PATCH="frameworks/base/packages/SettingsProvider/res/values/defaults.xml"
if [[ -f "$SETTINGS_DB_PATCH" ]]; then
    # Replace Google's captive portal URL with a neutral one (returns 204)
    sed -i \
        's|http://connectivitycheck.gstatic.com/generate_204|http://captiveportal.cookiehost.uk/generate_204|g' \
        "$SETTINGS_DB_PATCH" 2>/dev/null || true
    ok "Captive portal URL redirected."
fi

# ── Remove hardcoded Google DNS fallbacks ────────────────────────────────────
log "Removing hardcoded Google DNS servers..."
find frameworks/ system/ -name "*.java" -o -name "*.cpp" | \
    xargs grep -l "8\.8\.8\.8\|8\.8\.4\.4\|2001:4860:4860" 2>/dev/null | \
    while read -r f; do
        sed -i 's/8\.8\.8\.8/1\.1\.1\.1/g; s/8\.8\.4\.4/1\.0\.0\.1/g' "$f" || true
    done
ok "Google DNS references replaced with Cloudflare (1.1.1.1)."

# ── Remove Google NTP server ──────────────────────────────────────────────────
log "Replacing Google NTP server..."
find system/ -name "*.xml" | xargs grep -l "time.google.com" 2>/dev/null | \
    while read -r f; do
        sed -i 's/time\.google\.com/pool\.ntp\.org/g' "$f" || true
    done
ok "NTP server replaced with pool.ntp.org."

# ── Privacy-hardened hosts file ───────────────────────────────────────────────
log "Injecting privacy hosts file..."
HOSTS_DEST="system/core/rootdir/etc/hosts"
HOSTS_SRC="$(dirname "$0")/../../../../privacy/network/hosts"
[[ -f "$HOSTS_SRC" ]] && cp "$HOSTS_SRC" "$HOSTS_DEST" && ok "Hosts file injected."

# ── Remove ADB debugging in user builds ──────────────────────────────────────
log "Configuring secure build properties..."
cat >> build/make/core/main.mk << 'PROPS'
# CookieOS: Disable ADB in user builds
PRODUCT_PROPERTY_OVERRIDES += \
    persist.service.adb.enable=0 \
    persist.adb.notify=0 \
    ro.secure=1 \
    ro.debuggable=0
PROPS

ok "Degoogling complete."
log "The following are kept (user can install manually if desired):"
log "  - Standard AOSP apps (Calculator, Clock, Contacts, Camera)"
log "  - F-Droid can be added as app store alternative"
