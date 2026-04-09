# CookieOS Mobile — App Store Alternatives

**Like GrapheneOS, CookieOS supports installation of alternative app stores without Google Play Services.**

---

## Recommended App Stores

### 🔒 F-Droid (Recommended)
**Privacy-first, open-source app store**

- ✅ No user tracking
- ✅ Only open-source apps
- ✅ Source code verified
- ✅ Available offline

**Installation:**

```bash
# 1. Download F-Droid APK (offline)
wget https://f-droid.org/F-Droid.apk

# 2. Install via adb
adb install F-Droid.apk

# 3. Or sideload manually
# - Copy APK to phone
# - Settings → Apps → Install Unknown Apps → File Manager (toggle ON)
# - Open Files, tap F-Droid.apk

# 4. Enable in-app updates
# F-Droid → Settings → Updates → Automatic updates (ON)
```

**Key Apps:**
```
- Signal (encrypted messaging)
- AOSP Dialer (native phone calls)
- Blokada (system-wide ad blocking)
- AnySoftKeyboard (privacy keyboard)
- Nextcloud (CookieCloud client)
- K-9 Mail (encrypted email)
```

---

### 🟦 Aurora Store + microG (GrapheneOS Style)
**Semi-proprietary, but de-Googled version of Play Store**

**What you get:**
- Access to most apps from Play Store ecosystem
- NO user tracking (uses microG instead of Play Services)
- NO forced login required
- Lighter than Google Play (~15MB vs 100MB+)

**Installation:**

```bash
# 1. Install microG (Google Services replacement)
adb install microg.apk

# 2. Install Aurora Store
adb install aurora-store.apk

# 3. Grant permissions to microG
# Setting → Apps → microG → Permissions → Grant all

# 4. First launch of Aurora
# Skip login (can use anonymously)
# Search for apps and install

# 5. For automatic updates
# Aurora Store → Settings → Installation method → ✓ Installer
```

**Trade-offs:**
```
✅ More apps available
✅ Lightweight
✅ No account required
❌ Google account OR anonymous access (less recommended if your phone is already de-Googled)
❌ App verification not as strict as F-Droid
```

---

### 🟦 Google Play Store (If You Must)
**We don't recommend this, but some apps only exist here**

**Why we discourage it:**
- Contains Google Play Services (35+ background trackers)
- Play Protect constantly phones home
- ~100-200MB bloat
- Can override SELinux policies

**Installation only if absolutely necessary:**

```bash
# This requires pre-built AOSP with Play Store
# Not recommended for CookieOS build

# If already installed via OTA:
adb uninstall com.android.vending  # Remove Play Store
adb uninstall com.google.android.gms  # Remove Play Services
```

---

## Sideloading Apps Directly

When you want fine-grained control (no app store needed):

### Step 1: Enable Installation from Unknown Sources

```bash
adb shell settings put secure install_non_market_apps 1
```

### Step 2: Generate or Download APK

```bash
# Option A: Extract APK you already own
adb pull /data/app/com.app.name/base.apk ./MyApp.apk

# Option B: Find APK on F-Droid / APKMirror / GitHub
wget https://github.com/example/app/releases/app.apk
```

### Step 3: Verify APK Signature (Important!)

```bash
# Verify against F-Droid's key (if available)
jarsigner -verify -verbose -certs MyApp.apk

# Or check SHA256 against official source
sha256sum MyApp.apk
# Compare with https://example.com/app-sha256.txt
```

### Step 4: Install

```bash
adb install MyApp.apk
# Or locate file and tap it from Files app
```

---

## App Installation via CookieOS Package Manager

**CookieOS includes a package manager for trusted apps:**

```bash
# Install from CookieOS-curated repo
cookieos-app install signal
cookieos-app install nextcloud
cookieos-app install blokada

# List available
cookieos-app search

# Auto-verify signatures
cookieos-app verify signal
# Output: ✓ Signature valid (F-Droid key)
```

---

## Threat Detection for App Installation

**Before any app runs, CookieOS scans it:**

```bash
# When installing via F-Droid:
1. Download
2. [AUTOMATIC] Threat Detector scans APK
   - Checks permissions
   - Analyzes entropy
   - AI verification via Ollama
3. If HIGH/CRITICAL: Quarantined (won't run)
4. If SAFE/SUSPICIOUS: Allowed, monitor
5. Install proceeds

# View threat scan results
adb shell cat /data/local/tmp/threat_detections.json
```

**Never bypassed** — even manually sideloaded APKs go through threat detection.

---

## Recommended Apps for CookieOS Mobile

### Essentials
```
• Signal (encrypted messaging) — F-Droid
• Nextcloud (CookieCloud client) — F-Droid
• AOSP Dialer (calls) — Built-in
• Simple Contacts — F-Droid
• K-9 Mail (email) — F-Droid
```

### Privacy & Security
```
• ProtonVPN (already configured for Tailscale) — F-Droid
• Blokada (system ad-blocker) — F-Droid
• Bitwarden (password manager) — F-Droid or Aurora
• AnySoftKeyboard (no tracking) — F-Droid
• Exodus Privacy (app permission checker) — F-Droid
```

### Media & Productivity
```
• NewPipe (YouTube frontend, no ads) — F-Droid
• Krita (drawing app) — F-Droid
• OpenOffice (documents) — F-Droid
• Syncthing (file sync) — F-Droid
• Libretorrent (torrenting) — F-Droid
```

### Optional: CookieOS Services
```
• CookieCloud (sync, storage) — Built-in / CookieCloud repo
• CookieChat (local AI, Gemma 2B) — F-Droid
• CookieVPN (Tailscale) — System app
• Threat Detector (security) — System app (auto-runs)
```

---

## Comparing App Stores

| Feature | F-Droid | Aurora + microG | Play Store |
|---------|---------|-----------------|-----------|
| **Tracking** | None | None | Heavy (Google) |
| **User Account** | Not required | Not required | Google account |
| **App Count** | ~3,000 (curated) | 500k+ (full Play) | 3.5M+ |
| **Open Source % | 100% | ~10% | ~0% |
| **Offline Use** | Yes | No | No |
| **Download Size** | 5-15MB | 40-50MB | 100-180MB |
| **Auto-Updates** | Yes | Yes | Yes |
| **Safety Checks** | Automated builds | Trust Google's  | Play Protect |

---

## Removing Google Play Services

If your CookieOS build was pre-loaded with Google Play:

```bash
# Disable Google Play Store
adb shell pm disable-user --user 0 com.android.vending

# Disable Google Play Services
adb shell pm disable-user --user 0 com.google.android.gms

# Disable Google Services Framework
adb shell pm disable-user --user 0 com.google.android.gsf

# Disable Chrome (optional, keep if needed)
# adb shell pm disable-user --user 0 com.android.chrome

# Verify they're gone
adb shell pm list packages | grep google
# (Should return empty)
```

---

## Installation Best Practices

✅ **DO:**
- Install F-Droid first (gateway to privacy apps)
- Verify APK signatures before installing
- Keep app store updated for security fixes
- Use Nextcloud for cloud sync instead of Google Drive
- Check app permissions before first run

❌ **DON'T:**
- Install Play Store on CookieOS (defeats privacy model)
- Sideload random APKs without verification
- Grant all permissions requested by apps
- Use apps asking for "admin" access (AppOps) unnecessarily
- Trust app reviews without checking source code

---

## Troubleshooting

### F-Droid Won't Download Apps

```bash
# Check network (should be Tailscale)
adb shell am broadcast -a android.intent.action.TIME_TICK

# Restart F-Droid
adb shell pm clear org.fdroid.fdroid

# Check F-Droid server status
# Visit: https://status.f-droid.org
```

### Aurora Store Keeps Asking for Google Account

```bash
# That's expected — skip by tapping "Continue as Anonymous"
# Or use microG + Aurora combo (better experience)
```

### APK Install Blocked by Threat Detector

```bash
# Check quarantine folder
adb shell ls /cache/quarantine/

# Review threat detection result
adb shell cat /data/local/tmp/threat_detections.json

# If false positive, contact support@techtesting.tech with:
# - APK name
# - Threat detection output
# - Source (link to F-Droid / GitHub release)
```

### "Install Unknown Apps" Still Denied

```bash
# Grant per-app permission
adb shell appops set android.permission.REQUEST_INSTALL_PACKAGES

# Or enable globally
adb shell pm grant org.fdroid.fdroid android.permission.REQUEST_INSTALL_PACKAGES
```

---

## Why No Google Play by Default?

**CookieOS Philosophy:**

> *Your device. Your data. Your control.*

Google Play Services:
- 🚫 Runs 35+ background services
- 🚫 Collects precise location (even when disabled)
- 🚫 Reads your contacts, calendars, app list
- 🚫 Reports to Google every 30 minutes
- 🚫 Phones home when you unlock your phone

**CookieOS Alternative:**
- ✅ F-Droid: Privacy-first, verified source code
- ✅ Aurora + microG: Lightweight, optional
- ✅ Sideload: Full control, no middleman
- ✅ Tailscale: Private server access, not cloud

---

## Support

**Questions about app installation?**

📧 Email: support@techtesting.tech  
🌐 CookieCloud: https://cookiecloud.techtesting.tech  

**For specific app issues:**
- F-Droid: https://forum.f-droid.org
- Aurora: https://gitlab.com/AuroraOSS/AuroraStore/-/issues
- Signal: https://support.signal.org
