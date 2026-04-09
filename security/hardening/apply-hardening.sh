#!/usr/bin/env bash
# CookieOS System Hardening Script
# Applies sysctl, file permissions, service hardening, AppArmor, and
# firewall rules. Inspired by GrapheneOS, Tails, and linux-hardened.
set -euo pipefail

TARGET="${1:-desktop}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS="${ROOTFS:-}"   # Set when building rootfs; empty = live system

apply() {
    if [[ -n "$ROOTFS" ]]; then
        # Writing into a rootfs
        local dest="$ROOTFS$1"
        mkdir -p "$(dirname "$dest")"
        cat > "$dest"
    else
        # Running on a live system
        sudo tee "$1" > /dev/null
    fi
}

run() {
    if [[ -n "$ROOTFS" ]]; then
        chroot "$ROOTFS" /bin/bash -c "$*"
    else
        sudo bash -c "$*"
    fi
}

echo "[hardening] CookieOS System Hardening — target: $TARGET"
echo ""

# ── sysctl hardening ─────────────────────────────────────────────────────────
echo "[hardening] Writing sysctl config..."
apply /etc/sysctl.d/99-cookieos.conf << 'EOF'
# CookieOS sysctl hardening

# Kernel pointer hiding
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.printk = 3 3 3 3

# Prevent kernel symbol reading
kernel.perf_event_paranoid = 3
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2

# Disable core dumps for suid programs
fs.suid_dumpable = 0

# Prevent ptrace of any process
kernel.yama.ptrace_scope = 2

# ASLR - maximum randomization
kernel.randomize_va_space = 2

# Restrict /proc
kernel.kexec_load_disabled = 1
kernel.modules_disabled = 1

# Network hardening
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.tcp_timestamps = 0

# IPv6 privacy extensions
net.ipv6.conf.all.use_tempaddr = 2
net.ipv6.conf.default.use_tempaddr = 2

# TCP BBR
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Disable IP forwarding (unless gateway mode)
net.ipv4.ip_forward = 0
net.ipv6.conf.all.forwarding = 0

# VM hardening
vm.swappiness = 1
vm.mmap_rnd_bits = 32
vm.mmap_rnd_compat_bits = 16
EOF

# ── File permissions hardening ───────────────────────────────────────────────
echo "[hardening] Restricting sensitive file permissions..."
apply /etc/permissions.d/cookieos.conf << 'EOF'
/etc/shadow                0:0  0000
/etc/gshadow               0:0  0000
/etc/passwd                0:0  0644
/etc/group                 0:0  0644
/boot                      0:0  0700
/proc/kcore                --   ----
/proc/kallsyms             --   ----
EOF

# ── Login hardening ──────────────────────────────────────────────────────────
echo "[hardening] Hardening login settings..."
apply /etc/login.defs << 'EOF'
PASS_MAX_DAYS   90
PASS_MIN_DAYS   1
PASS_WARN_AGE   14
PASS_MIN_LEN    16
ENCRYPT_METHOD  YESCRYPT
YESCRYPT_COST_FACTOR 11
LOGIN_RETRIES   3
LOGIN_TIMEOUT   60
UMASK           027
EOF

apply /etc/security/pwquality.conf << 'EOF'
minlen = 16
dcredit = -1
ucredit = -1
lcredit = -1
ocredit = -1
maxrepeat = 2
gecoscheck = 1
badwords = password cookie admin root
EOF

# ── PAM hardening ────────────────────────────────────────────────────────────
echo "[hardening] Configuring PAM..."
apply /etc/pam.d/common-password << 'EOF'
password  requisite   pam_pwquality.so retry=3
password  sufficient  pam_unix.so sha512 shadow use_authtok
password  required    pam_deny.so
EOF

# ── AppArmor enforcement ─────────────────────────────────────────────────────
echo "[hardening] Enabling AppArmor enforcement..."
if [[ -n "$ROOTFS" ]]; then
    mkdir -p "$ROOTFS/etc/apparmor.d"
    cp -r "$SCRIPT_DIR/../../security/apparmor/profiles/." "$ROOTFS/etc/apparmor.d/"
else
    sudo cp -r "$SCRIPT_DIR/../../security/apparmor/profiles/." /etc/apparmor.d/
    sudo systemctl enable apparmor
    sudo aa-enforce /etc/apparmor.d/*
fi

# ── UFW firewall ─────────────────────────────────────────────────────────────
echo "[hardening] Configuring firewall (ufw)..."
apply /etc/ufw/ufw.conf << 'EOF'
ENABLED=yes
LOGLEVEL=low
EOF

# ── Disable unneeded services ────────────────────────────────────────────────
echo "[hardening] Masking unneeded services..."
MASK_SERVICES=(
    "avahi-daemon"
    "cups"
    "bluetooth"
    "ModemManager"
    "wpa_supplicant"   # Replaced by iwd
    "rpcbind"
    "nfs-server"
    "rpc-statd"
    "apport"
)
for svc in "${MASK_SERVICES[@]}"; do
    run "systemctl mask $svc 2>/dev/null || true"
done

# ── Secure shared memory ─────────────────────────────────────────────────────
echo "[hardening] Securing shared memory..."
apply /etc/fstab.d/shm.conf << 'EOF'
tmpfs  /dev/shm  tmpfs  defaults,noexec,nosuid,nodev  0  0
tmpfs  /tmp      tmpfs  defaults,noexec,nosuid,nodev,size=2G  0  0
EOF

# ── MAC address randomisation ────────────────────────────────────────────────
echo "[hardening] Configuring MAC address randomisation..."
apply /etc/NetworkManager/conf.d/99-mac-random.conf << 'EOF'
[device]
wifi.scan-rand-mac-address=yes

[connection]
wifi.cloned-mac-address=random
ethernet.cloned-mac-address=random
connection.stable-id=${CONNECTION}/${BOOT}
EOF

# ── Automatic security updates ───────────────────────────────────────────────
echo "[hardening] Enabling unattended security upgrades..."
run "apt-get install -y unattended-upgrades apt-listchanges 2>/dev/null || true"
apply /etc/apt/apt.conf.d/50unattended-upgrades << 'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

echo ""
echo "[hardening] Done. CookieOS hardening applied to: $TARGET"
