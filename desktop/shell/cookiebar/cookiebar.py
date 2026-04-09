#!/usr/bin/env python3
"""
CookieBar — CookieOS Status Bar / Taskbar
A lightweight Wayland/X11 panel inspired by Cinnamon + GNOME Shell
with privacy indicators, CookieCloud status, and Tor toggle.

Requires: python3-gi (GTK4), python3-dbus, libgtk-4-dev
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, GLib, GObject
import subprocess
import json
import os
import time
import threading
import dbus
import dbus.mainloop.glib
from pathlib import Path
from datetime import datetime

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

APP_ID = "uk.cookiehost.cookiebar"
STYLE = """
window.cookiebar {
    background-color: #1a1a2e;
    border-bottom: 1px solid #16213e;
    color: #e0e0e0;
    font-family: "Inter", "Noto Sans", sans-serif;
    font-size: 13px;
}
button.cookiebar-btn {
    background: transparent;
    border: none;
    color: #e0e0e0;
    padding: 4px 8px;
    border-radius: 6px;
    transition: background 0.15s;
}
button.cookiebar-btn:hover {
    background-color: rgba(255,255,255,0.08);
}
label.cookie-logo {
    font-weight: bold;
    color: #f5a623;
    font-size: 15px;
    padding: 0 10px;
}
.privacy-indicator { color: #4caf50; }
.privacy-indicator.off { color: #f44336; }
.tor-active { color: #9c27b0; }
.cloud-syncing { color: #2196f3; }
.cloud-ok { color: #4caf50; }
.clock { font-variant-numeric: tabular-nums; margin: 0 12px; }
"""


class PrivacyIndicator(Gtk.Box):
    """Shows Tor status + VPN + privacy mode toggles."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._tor_btn   = self._make_btn("🧅 Tor", self._toggle_tor)
        self._vpn_label = Gtk.Label(label="")
        self._priv_btn  = self._make_btn("👁 Private", self._toggle_private_mode)

        self.append(self._tor_btn)
        self.append(self._vpn_label)
        self.append(self._priv_btn)

        GLib.timeout_add_seconds(5, self._refresh_status)
        self._refresh_status()

    def _make_btn(self, label: str, callback) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.add_css_class("cookiebar-btn")
        btn.connect("clicked", lambda _: callback())
        return btn

    def _toggle_tor(self):
        try:
            status = subprocess.run(
                ["systemctl", "is-active", "tor"],
                capture_output=True, text=True
            ).stdout.strip()
            if status == "active":
                subprocess.run(["pkexec", "privacy/network/tor-router.sh", "disable"])
                subprocess.run(["pkexec", "systemctl", "stop", "tor"])
            else:
                subprocess.run(["pkexec", "systemctl", "start", "tor"])
                subprocess.run(["pkexec", "privacy/network/tor-router.sh", "enable"])
        except Exception as e:
            print(f"[cookiebar] Tor toggle error: {e}")
        self._refresh_status()

    def _toggle_private_mode(self):
        # Launch Firefox in private mode through Tor
        subprocess.Popen([
            "firejail", "--private",
            "firefox", "--private-window", "--no-remote",
        ])

    def _refresh_status(self) -> bool:
        tor_active = subprocess.run(
            ["systemctl", "is-active", "tor"],
            capture_output=True, text=True
        ).stdout.strip() == "active"

        self._tor_btn.set_label("🧅 Tor: ON" if tor_active else "🧅 Tor: OFF")
        css = "privacy-indicator tor-active" if tor_active else "privacy-indicator off"
        self._tor_btn.set_css_classes(["cookiebar-btn"] + css.split())
        return True  # Keep timeout alive


class CookieCloudWidget(Gtk.Button):
    """Shows CookieCloud sync status and opens the client."""

    def __init__(self):
        super().__init__()
        self.add_css_class("cookiebar-btn")
        self._label = Gtk.Label(label="☁ CookieCloud")
        self.set_child(self._label)
        self.connect("clicked", lambda _: self._open_client())

        GLib.timeout_add_seconds(30, self._refresh)
        self._refresh()

    def _refresh(self) -> bool:
        state_file = Path.home() / ".cache/cookiecloud/sync-state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                n = len(state.get("files", {}))
                self._label.set_label(f"☁ {n} files synced")
                self.set_css_classes(["cookiebar-btn", "cloud-ok"])
            except Exception:
                self._label.set_label("☁ CookieCloud")
        else:
            self._label.set_label("☁ Not connected")
            self.set_css_classes(["cookiebar-btn", "privacy-indicator", "off"])
        return True

    def _open_client(self):
        subprocess.Popen(["cookiecloud", "status"])


class SystemTray(Gtk.Box):
    """Right-side system tray: network, battery, clock."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._net_label  = Gtk.Label(label="")
        self._bat_label  = Gtk.Label(label="")
        self._clock      = Gtk.Label(label="")
        self._clock.add_css_class("clock")

        self.append(self._net_label)
        self.append(self._bat_label)
        self.append(self._clock)

        GLib.timeout_add_seconds(1, self._tick)
        self._tick()

    def _tick(self) -> bool:
        self._clock.set_label(datetime.now().strftime("%a %d %b  %H:%M:%S"))
        self._update_battery()
        return True

    def _update_battery(self):
        bat_path = Path("/sys/class/power_supply/BAT0")
        if bat_path.exists():
            try:
                capacity = (bat_path / "capacity").read_text().strip()
                status   = (bat_path / "status").read_text().strip()
                icon = "🔋" if status == "Discharging" else "⚡"
                self._bat_label.set_label(f"{icon} {capacity}%")
            except Exception:
                pass


class CookieBar(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("CookieBar")
        self.set_decorated(False)
        self.set_default_size(-1, 36)

        # Apply CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(STYLE.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.add_css_class("cookiebar")

        # Layout: [Logo | AppMenu | Privacy] ←→ [CookieCloud | Tray]
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_hexpand(True)

        # Left
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        logo = Gtk.Label(label="🍪 CookieOS")
        logo.add_css_class("cookie-logo")
        left.append(logo)
        left.append(PrivacyIndicator())

        # Right
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        right.set_halign(Gtk.Align.END)
        right.append(CookieCloudWidget())
        right.append(SystemTray())

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        hbox.append(left)
        hbox.append(spacer)
        hbox.append(right)

        self.set_child(hbox)


class CookieBarApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = CookieBar(app)
        win.present()


if __name__ == "__main__":
    app = CookieBarApp()
    app.run()
