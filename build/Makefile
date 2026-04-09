# CookieOS Master Build System
# Supports: x86_64, aarch64 (desktop/server), ARM64 (mobile/AOSP)
# Copyright (C) CookieNet

COOKIEOS_VERSION := 1.0.0
COOKIEOS_CODENAME := Biscuit

# Build targets
TARGET ?= desktop
ARCH   ?= x86_64

# Output directories
OUT_DIR   := $(CURDIR)/../out
ISO_DIR   := $(OUT_DIR)/iso
IMG_DIR   := $(OUT_DIR)/images
PKG_DIR   := $(OUT_DIR)/packages

# Kernel version
KERNEL_VERSION := 6.6.30-cookie

# Colours
RED    := \033[0;31m
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
CYAN   := \033[0;36m
RESET  := \033[0m

.PHONY: all clean desktop mobile iso android help deps kernel security cookiecloud

all: deps kernel security cookiecloud
	@echo "$(GREEN)[CookieOS] Build complete — $(COOKIEOS_VERSION) $(COOKIEOS_CODENAME)$(RESET)"

help:
	@echo "$(CYAN)CookieOS Build System v$(COOKIEOS_VERSION) ($(COOKIEOS_CODENAME))$(RESET)"
	@echo ""
	@echo "  make desktop     — Build desktop (Linux Mint-style) variant"
	@echo "  make mobile      — Build mobile (AOSP-based) variant"
	@echo "  make android     — Build full AOSP image"
	@echo "  make iso         — Create bootable ISO (desktop)"
	@echo "  make kernel      — Build hardened kernel"
	@echo "  make security    — Apply security hardening"
	@echo "  make cookiecloud — Build CookieCloud client stack"
	@echo "  make live        — Build Tails-style amnesic live image"
	@echo "  make deps        — Install build dependencies"
	@echo "  make clean       — Clean build artifacts"
	@echo ""
	@echo "  TARGET=mobile ARCH=aarch64 make all"

deps:
	@echo "$(BLUE)[deps] Checking build dependencies...$(RESET)"
	@bash $(CURDIR)/../scripts/check-deps.sh

kernel:
	@echo "$(BLUE)[kernel] Building hardened kernel $(KERNEL_VERSION)...$(RESET)"
	@$(MAKE) -C $(CURDIR)/../core/kernel TARGET=$(TARGET) ARCH=$(ARCH)

security:
	@echo "$(BLUE)[security] Applying CookieOS security hardening...$(RESET)"
	@bash $(CURDIR)/../security/hardening/apply-hardening.sh $(TARGET)

cookiecloud:
	@echo "$(BLUE)[cookiecloud] Building CookieCloud client...$(RESET)"
	@$(MAKE) -C $(CURDIR)/../cookiecloud/client

desktop: kernel security cookiecloud
	@echo "$(BLUE)[desktop] Building CookieOS Desktop...$(RESET)"
	@$(MAKE) -C $(CURDIR)/linux TARGET=desktop ARCH=$(ARCH)

mobile: kernel security cookiecloud
	@echo "$(BLUE)[mobile] Building CookieOS Mobile...$(RESET)"
	@$(MAKE) -C $(CURDIR)/android ARCH=$(ARCH)

iso: desktop
	@echo "$(BLUE)[iso] Creating bootable ISO...$(RESET)"
	@bash $(CURDIR)/../installer/live/make-iso.sh $(COOKIEOS_VERSION) $(ARCH)

live: desktop
	@echo "$(BLUE)[live] Building amnesic live image (Tails-style)...$(RESET)"
	@bash $(CURDIR)/../installer/live/make-live.sh $(COOKIEOS_VERSION) $(ARCH)

android: mobile
	@echo "$(BLUE)[android] Building AOSP image...$(RESET)"
	@bash $(CURDIR)/android/build-aosp.sh

clean:
	@echo "$(RED)[clean] Removing build artifacts...$(RESET)"
	@rm -rf $(OUT_DIR)
	@$(MAKE) -C $(CURDIR)/../core/kernel clean 2>/dev/null || true
	@$(MAKE) -C $(CURDIR)/../cookiecloud/client clean 2>/dev/null || true
