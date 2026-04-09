#!/usr/bin/env bash
# CookieOS AOSP Auto-Updater
#
# Tracks upstream AOSP security tags and automatically:
#   1. Detects when Google releases a new Android security patch
#   2. Updates the local AOSP source tree
#   3. Re-applies all CookieOS patches (kernel hardening, degoogle, etc.)
#   4. Triggers a rebuild if patches apply cleanly
#   5. Notifies via CookieCloud / email if a manual review is needed
#
# This means CookieOS Mobile gets Google's security patches automatically,
# even when no CookieOS-specific release is made.
#
# Run as a cron job on your build server:
#   0 3 * * * /opt/cookieos/mobile/patches/scripts/aosp-autoupdate.sh >> /var/log/cookieos-update.log 2>&1

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(realpath "$SCRIPT_DIR/../../..")"
PATCH_DIR="$ROOT_DIR/mobile/patches"

# ── Config (edit these) ───────────────────────────────────────────────────────
AOSP_DIR="${AOSP_DIR:-$HOME/aosp/cookieos}"
TARGET_DEVICE="${TARGET_DEVICE:-generic_arm64}"
BUILD_TYPE="${BUILD_TYPE:-user}"
NOTIFY_EMAIL="${NOTIFY_EMAIL:-}"                    # Optional: email on conflict
NOTIFY_WEBHOOK="${NOTIFY_WEBHOOK:-}"                # Optional: CookieCloud webhook
STATE_FILE="${STATE_FILE:-$ROOT_DIR/mobile/.aosp-state.json}"

# AOSP branch to track (update this when bumping major Android version)
AOSP_BRANCH="${AOSP_BRANCH:-android-16.0.0_r}"     # Will auto-append latest tag

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RESET='\033[0m'
log()   { echo -e "${BLUE}[aosp-update $(date '+%H:%M:%S')]${RESET} $*"; }
ok()    { echo -e "${GREEN}[aosp-update]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[aosp-update]${RESET} $*"; }
err()   { echo -e "${RED}[aosp-update]${RESET} $*" >&2; }


# ── Load / save state ─────────────────────────────────────────────────────────

load_state() {
    if [[ -f "$STATE_FILE" ]]; then
        python3 -c "import json,sys; d=json.load(open('$STATE_FILE')); print(d.get('$1',''))"
    fi
}

save_state() {
    python3 - "$STATE_FILE" "$1" "$2" << 'PY'
import json, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(path))
except Exception:
    d = {}
d[key] = val
json.dump(d, open(path, 'w'), indent=2)
PY
}


# ── Fetch latest AOSP security tag ───────────────────────────────────────────

get_latest_aosp_tag() {
    # Fetch tag list from AOSP and find the highest revision for Android 14
    git ls-remote \
        https://android.googlesource.com/platform/manifest \
        "refs/tags/${AOSP_BRANCH}*" 2>/dev/null \
    | awk '{print $2}' \
    | sed 's|refs/tags/||' \
    | grep -E "^android-16\.0\.0_r[0-9]+$" \
    | sort -t'r' -k2 -n \
    | tail -1
}


# ── Check if update is needed ─────────────────────────────────────────────────

check_for_update() {
    log "Checking for new AOSP security patches..."
    LATEST_TAG=$(get_latest_aosp_tag)
    CURRENT_TAG=$(load_state "aosp_tag")

    if [[ -z "$LATEST_TAG" ]]; then
        warn "Could not fetch AOSP tag list. Check network connectivity."
        return 1
    fi

    log "Latest upstream AOSP tag: $LATEST_TAG"
    log "Current built tag:        ${CURRENT_TAG:-<none>}"

    if [[ "$LATEST_TAG" == "$CURRENT_TAG" ]]; then
        ok "CookieOS Mobile is up to date."
        return 1
    fi

    log "New AOSP tag available: $LATEST_TAG"
    return 0
}


# ── Sync AOSP to new tag ──────────────────────────────────────────────────────

sync_aosp() {
    local tag="$1"
    log "Syncing AOSP to tag: $tag ..."
    cd "$AOSP_DIR"

    # Re-init repo to the new tag
    repo init \
        --manifest-url   https://android.googlesource.com/platform/manifest \
        --manifest-branch "$tag" \
        --depth=1 \
        --no-clone-bundle \
        --quiet

    repo sync \
        -c \
        -j"$(nproc)" \
        --no-tags \
        --no-clone-bundle \
        --force-sync \
        --quiet

    ok "AOSP synced to $tag"
}


# ── Re-apply CookieOS patches ─────────────────────────────────────────────────

apply_patches() {
    log "Re-applying CookieOS patches..."
    cd "$AOSP_DIR"
    local failed=()

    # Kernel patches
    for patch in "$PATCH_DIR"/kernel/*.patch; do
        [[ -f "$patch" ]] || continue
        if ! git -C kernel apply --check "$patch" &>/dev/null; then
            warn "  Kernel patch needs manual review: $(basename "$patch")"
            failed+=("kernel/$(basename "$patch")")
        else
            git -C kernel apply "$patch"
            log "  ✓ kernel/$(basename "$patch")"
        fi
    done

    # Framework patches
    for patch in "$PATCH_DIR"/framework/*.patch; do
        [[ -f "$patch" ]] || continue
        if ! git -C frameworks/base apply --check "$patch" &>/dev/null; then
            warn "  Framework patch needs manual review: $(basename "$patch")"
            failed+=("framework/$(basename "$patch")")
        else
            git -C frameworks/base apply "$patch"
            log "  ✓ framework/$(basename "$patch")"
        fi
    done

    # Degoogle script (typically applies cleanly)
    log "  Running degoogle script..."
    bash "$PATCH_DIR/scripts/degoogle.sh" "$AOSP_DIR" || {
        warn "  degoogle.sh reported issues — manual review needed"
        failed+=("degoogle")
    }

    if [[ ${#failed[@]} -gt 0 ]]; then
        warn "The following patches need manual review after the AOSP update:"
        for f in "${failed[@]}"; do
            warn "  - $f"
        done
        notify_conflict "$LATEST_TAG" "${failed[@]}"
        return 1
    fi

    ok "All patches applied cleanly."
    return 0
}


# ── Trigger build ─────────────────────────────────────────────────────────────

trigger_build() {
    local tag="$1"
    log "Triggering CookieOS Mobile build for $tag..."

    export AOSP_DIR TARGET_DEVICE BUILD_TYPE COOKIEOS_VERSION="$tag-cookie"
    bash "$ROOT_DIR/build/android/build-aosp.sh" > \
        "$ROOT_DIR/out/build-$tag.log" 2>&1 &

    BUILD_PID=$!
    log "Build started in background (PID $BUILD_PID)"
    log "Log: $ROOT_DIR/out/build-$tag.log"

    # Wait for build (timeout 4 hours)
    local timeout=14400
    local elapsed=0
    while kill -0 "$BUILD_PID" 2>/dev/null; do
        sleep 60
        elapsed=$((elapsed + 60))
        if [[ $elapsed -ge $timeout ]]; then
            warn "Build timed out after $((timeout / 3600)) hours."
            kill "$BUILD_PID" 2>/dev/null || true
            return 1
        fi
    done

    wait "$BUILD_PID"
    local exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        ok "Build completed successfully for $tag"
        return 0
    else
        err "Build failed for $tag (exit code $exit_code)"
        err "See log: $ROOT_DIR/out/build-$tag.log"
        return 1
    fi
}


# ── Notifications ─────────────────────────────────────────────────────────────

notify_conflict() {
    local tag="$1"; shift
    local patches=("$@")
    local msg="CookieOS Auto-Update: AOSP $tag — ${#patches[@]} patch(es) need manual review: ${patches[*]}"

    if [[ -n "$NOTIFY_EMAIL" ]]; then
        echo "$msg" | mail -s "[CookieOS] Manual patch review needed: $tag" "$NOTIFY_EMAIL" || true
    fi

    if [[ -n "$NOTIFY_WEBHOOK" ]]; then
        curl -s -X POST "$NOTIFY_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"$msg\"}" || true
    fi

    warn "$msg"
}

notify_success() {
    local tag="$1"
    local msg="CookieOS Mobile updated to AOSP $tag successfully."

    if [[ -n "$NOTIFY_WEBHOOK" ]]; then
        curl -s -X POST "$NOTIFY_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"$msg\"}" || true
    fi

    ok "$msg"
}


# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    log "CookieOS AOSP Auto-Updater starting..."
    log "AOSP dir: $AOSP_DIR"

    # Get latest tag (set globally for use in sub-functions)
    LATEST_TAG=$(get_latest_aosp_tag)
    [[ -n "$LATEST_TAG" ]] || { err "Could not determine latest AOSP tag."; exit 1; }

    CURRENT_TAG=$(load_state "aosp_tag")

    if [[ "$LATEST_TAG" == "$CURRENT_TAG" ]]; then
        ok "Already on latest AOSP tag ($LATEST_TAG). Nothing to do."
        exit 0
    fi

    log "Updating: $CURRENT_TAG → $LATEST_TAG"

    sync_aosp "$LATEST_TAG"

    if apply_patches; then
        if trigger_build "$LATEST_TAG"; then
            save_state "aosp_tag" "$LATEST_TAG"
            save_state "last_update" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            notify_success "$LATEST_TAG"
        else
            err "Build failed. State not updated — will retry on next run."
            exit 1
        fi
    else
        warn "Patches need review. Sync complete, build NOT triggered."
        warn "Fix the patches and run: $0 --force-build $LATEST_TAG"
        exit 2
    fi
}

# Allow forcing a build of a specific tag (after manual patch fix)
if [[ "${1:-}" == "--force-build" && -n "${2:-}" ]]; then
    LATEST_TAG="$2"
    log "Force-building $LATEST_TAG..."
    trigger_build "$LATEST_TAG" && {
        save_state "aosp_tag" "$LATEST_TAG"
        save_state "last_update" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    }
else
    main
fi
