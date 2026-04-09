# CookieCloud Client Build
VENV    := $(CURDIR)/.venv
PYTHON  := $(VENV)/bin/python3
PIP     := $(VENV)/bin/pip
PYINST  := $(VENV)/bin/pyinstaller
OUT_DIR := $(CURDIR)/../../out/packages

.PHONY: all venv install build deb clean

all: venv install build

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip wheel

install:
	$(PIP) install \
		requests \
		cryptography \
		keyring \
		pyinstaller \
		watchdog

build:
	$(PYINST) \
		--onefile \
		--name cookiecloud \
		--icon ../../desktop/themes/cookie/cookie-icon.png \
		cookiecloud-client.py
	mkdir -p $(OUT_DIR)
	cp dist/cookiecloud $(OUT_DIR)/cookiecloud

deb: build
	mkdir -p deb-pkg/usr/bin deb-pkg/usr/share/applications deb-pkg/DEBIAN
	cp dist/cookiecloud deb-pkg/usr/bin/
	cp ../apps/cookiecloud.desktop deb-pkg/usr/share/applications/
	echo "Package: cookiecloud-client\nVersion: 1.0.0\nArchitecture: amd64\nMaintainer: CookieNet\nDescription: CookieCloud native sync client" \
	    > deb-pkg/DEBIAN/control
	dpkg-deb --build deb-pkg $(OUT_DIR)/cookiecloud-client_1.0.0_amd64.deb

clean:
	rm -rf dist build .venv deb-pkg *.spec
