# CookieOS Linux Desktop Build
# Produces a Debian-based system with hardened kernel + Cookie Desktop Environment

ARCH     ?= x86_64
DIST     := bookworm
OUT_DIR  := $(CURDIR)/../../out
ROOTFS   := $(OUT_DIR)/rootfs-desktop
MIRROR   := https://deb.debian.org/debian

.PHONY: all rootfs packages desktop-env cookieshell cleanup

all: rootfs packages desktop-env cookieshell
	@echo "[linux] Desktop build complete."

rootfs:
	@echo "[linux] Bootstrapping Debian $(DIST) rootfs..."
	@mkdir -p $(ROOTFS)
	debootstrap --arch=$(ARCH) --variant=minbase $(DIST) $(ROOTFS) $(MIRROR)
	@cp $(CURDIR)/configs/apt-sources.list $(ROOTFS)/etc/apt/sources.list
	@cp $(CURDIR)/configs/locale.conf $(ROOTFS)/etc/locale.conf
	@cp $(CURDIR)/configs/hostname $(ROOTFS)/etc/hostname

packages:
	@echo "[linux] Installing base packages..."
	chroot $(ROOTFS) /bin/bash -c "apt-get update && apt-get install -y \
		systemd systemd-sysv udev dbus \
		linux-firmware iproute2 net-tools \
		openssh-client gnupg2 curl wget \
		ca-certificates apt-transport-https \
		sudo locales tzdata \
		pipewire wireplumber alsa-utils \
		mesa-vulkan-drivers mesa-utils \
		xorg xserver-xorg-input-evdev \
		wayland-protocols libwayland-dev \
		flatpak appstream \
		tor torsocks onionshare \
		torbrowser-launcher \
		apparmor apparmor-profiles apparmor-utils \
		firejail \
		cryptsetup luks-utils veracrypt-fuse \
		network-manager-gnome \
		fail2ban ufw \
		keepassxc kleopatra \
		thunderbird libreoffice \
		vlc mpv \
		chromium chromium-sandbox \
		firefox-esr"
	@echo "[linux] Setting Tor as default auto-start service..."
	chroot $(ROOTFS) /bin/bash -c "systemctl enable tor"
	@echo "[linux] Configuring Chromium defaults (DuckDuckGo, no Google sync)..."
	@cp $(CURDIR)/configs/chromium-master-preferences.json \
	    $(ROOTFS)/etc/chromium/master_preferences 2>/dev/null || \
	    mkdir -p $(ROOTFS)/etc/chromium && \
	    cp $(CURDIR)/configs/chromium-master-preferences.json \
	       $(ROOTFS)/etc/chromium/master_preferences

desktop-env:
	@echo "[linux] Installing Cookie Desktop Environment..."
	chroot $(ROOTFS) /bin/bash -c "apt-get install -y \
		cinnamon-desktop-environment \
		nemo nemo-fileroller \
		plank \
		rofi dunst picom \
		gtk3-engines-breeze \
		papirus-icon-theme"
	@cp -r $(CURDIR)/../../desktop/themes/cookie $(ROOTFS)/usr/share/themes/
	@cp -r $(CURDIR)/../../desktop/shell/config $(ROOTFS)/etc/skel/.config/

cookieshell:
	@echo "[linux] Installing Cookie Shell components..."
	@cp -r $(CURDIR)/../../desktop/shell/cookiebar $(ROOTFS)/usr/share/cookieos/
	@cp $(CURDIR)/../../desktop/shell/cookiebar/cookiebar.py $(ROOTFS)/usr/bin/cookiebar
	@chmod +x $(ROOTFS)/usr/bin/cookiebar

cleanup:
	@echo "[linux] Cleaning up rootfs..."
	chroot $(ROOTFS) /bin/bash -c "apt-get clean && rm -rf /var/cache/apt/archives/*"
