# CookieOS Kernel Build
KERNEL_VERSION := 6.6.30
KERNEL_SRC     ?= $(HOME)/src/linux-$(KERNEL_VERSION)
ARCH           ?= x86_64
CROSS_COMPILE  ?=
OUT_DIR        := $(CURDIR)/../../out/kernel
CONFIG_FILE    := $(CURDIR)/kernel.config
PATCH_DIR      := $(CURDIR)/patches

KBUILD_FLAGS := \
    ARCH=$(ARCH) \
    CROSS_COMPILE=$(CROSS_COMPILE) \
    O=$(OUT_DIR)

.PHONY: all download patch config build install clean

all: download patch config build

download:
	@echo "[kernel] Downloading Linux $(KERNEL_VERSION)..."
	@mkdir -p $(HOME)/src
	@if [ ! -d "$(KERNEL_SRC)" ]; then \
	    curl -L https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$(KERNEL_VERSION).tar.xz \
	        -o /tmp/linux-$(KERNEL_VERSION).tar.xz; \
	    tar -xf /tmp/linux-$(KERNEL_VERSION).tar.xz -C $(HOME)/src; \
	fi

patch:
	@echo "[kernel] Applying CookieOS hardening patches..."
	@for p in $(PATCH_DIR)/*.patch; do \
	    [ -f "$$p" ] || continue; \
	    echo "  Applying: $$(basename $$p)"; \
	    git -C $(KERNEL_SRC) apply "$$p" 2>/dev/null || echo "  (skip — already applied)"; \
	done

config:
	@echo "[kernel] Merging CookieOS config..."
	@mkdir -p $(OUT_DIR)
	$(MAKE) $(KBUILD_FLAGS) -C $(KERNEL_SRC) defconfig
	cd $(KERNEL_SRC) && scripts/kconfig/merge_config.sh \
	    -O $(OUT_DIR) \
	    $(OUT_DIR)/.config \
	    $(CONFIG_FILE)

build:
	@echo "[kernel] Building kernel..."
	$(MAKE) $(KBUILD_FLAGS) -C $(KERNEL_SRC) -j$(shell nproc) bzImage modules

install:
	@echo "[kernel] Installing kernel..."
	$(MAKE) $(KBUILD_FLAGS) -C $(KERNEL_SRC) modules_install INSTALL_MOD_PATH=$(OUT_DIR)/modules
	cp $(OUT_DIR)/arch/$(ARCH)/boot/bzImage $(OUT_DIR)/vmlinuz-$(KERNEL_VERSION)-cookie

clean:
	@rm -rf $(OUT_DIR)
