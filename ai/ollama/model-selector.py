#!/usr/bin/env python3
"""
CookieOS AI Model Selector
Auto-detects device RAM/VRAM and user power preference,
then selects the best Gemma 4 variant (or Gemma 2B for phones).

Gemma 4 tiers (desktop/server):
  gemma4:2b   (e2b)   7.2 GB   128K ctx  — Low-end / Fast
  gemma4:4b   (e4b)   9.6 GB   128K ctx  — Mid-range / Balanced  ← default
  gemma4:26b         18.0 GB   256K ctx  — High-end / Quality
  gemma4:31b         20.0 GB   256K ctx  — Flagship / Max Quality

Phone tier (Android):
  gemma3:2b           1.7 GB   8K ctx    — Runs on any Android phone ≥ 4GB RAM
  Served over Tailscale to other CookieOS/CookieAI devices.
"""

import os
import sys
import platform
import subprocess
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

# ── Power level ───────────────────────────────────────────────────────────────

class PowerLevel(Enum):
    FAST     = "fast"       # Minimum RAM, fastest responses
    BALANCED = "balanced"   # Default — best quality for the device
    QUALITY  = "quality"    # Biggest model that fits
    MAX      = "max"        # Force biggest model (user accepts OOM risk)


# ── Model catalogue ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AIModel:
    name:         str
    ollama_tag:   str
    size_gb:      float   # Approximate VRAM/RAM requirement
    context_k:    int     # Max context window (K tokens)
    multimodal:   bool    # Supports image input
    tier:         str     # "phone" | "nano" | "small" | "mid" | "flagship"
    description:  str
    platform:     str     # "phone" | "desktop" | "any"


ALL_MODELS: list[AIModel] = [
    # ── Phone ─────────────────────────────────────────────────────────────────
    AIModel(
        name="Gemma 3 2B (Phone)",
        ollama_tag="gemma3:2b",
        size_gb=1.7,
        context_k=8,
        multimodal=False,
        tier="phone",
        platform="phone",
        description=(
            "Runs directly on Android phones. Served over Tailscale to "
            "CookieOS desktops and CookieAI apps."
        ),
    ),
    # ── Desktop/Server ────────────────────────────────────────────────────────
    AIModel(
        name="Gemma 4 2B",
        ollama_tag="gemma4:e2b",
        size_gb=7.2,
        context_k=128,
        multimodal=True,
        tier="nano",
        platform="desktop",
        description="Ultra-fast, lowest RAM. Good for quick answers on low-end hardware.",
    ),
    AIModel(
        name="Gemma 4 4B",
        ollama_tag="gemma4:e4b",
        size_gb=9.6,
        context_k=128,
        multimodal=True,
        tier="small",
        platform="desktop",
        description="Balanced speed + quality. Default for most laptops and desktops.",
    ),
    AIModel(
        name="Gemma 4 26B",
        ollama_tag="gemma4:26b",
        size_gb=18.0,
        context_k=256,
        multimodal=True,
        tier="mid",
        platform="desktop",
        description="High quality. Requires 20GB+ RAM or dedicated GPU.",
    ),
    AIModel(
        name="Gemma 4 31B",
        ollama_tag="gemma4:31b",
        size_gb=20.0,
        context_k=256,
        multimodal=True,
        tier="flagship",
        platform="desktop",
        description="Maximum quality. Requires 24GB+ RAM/VRAM or server-grade hardware.",
    ),
]

DESKTOP_MODELS = [m for m in ALL_MODELS if m.platform in ("desktop", "any")]
PHONE_MODELS   = [m for m in ALL_MODELS if m.platform in ("phone",   "any")]


# ── Device detection ──────────────────────────────────────────────────────────

@dataclass
class DeviceProfile:
    total_ram_gb:   float
    available_gb:   float
    vram_gb:        float
    gpu_name:       str
    cpu_cores:      int
    sys_platform:   str
    is_mobile:      bool
    usable_gb:      float
    tailscale_ip:   str     # Empty string if Tailscale not active

    def summary(self) -> str:
        ts = f" | Tailscale {self.tailscale_ip}" if self.tailscale_ip else ""
        return (
            f"{self.sys_platform} | CPU {self.cpu_cores}c | "
            f"RAM {self.total_ram_gb:.1f}GB (avail {self.available_gb:.1f}GB) | "
            f"VRAM {self.vram_gb:.1f}GB [{self.gpu_name or 'no GPU'}]{ts}"
        )


def detect_tailscale_ip() -> str:
    """Return this device's Tailscale IP if active, else empty string."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def detect_device() -> DeviceProfile:
    sys_p     = platform.system()
    cpu_cores = os.cpu_count() or 2
    total_ram_gb = available_gb = 0.0

    try:
        import psutil
        vm = psutil.virtual_memory()
        total_ram_gb = vm.total    / (1024 ** 3)
        available_gb = vm.available / (1024 ** 3)
    except ImportError:
        if sys_p == "Linux" or "ANDROID_ROOT" in os.environ:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            total_ram_gb = int(line.split()[1]) / (1024**2)
                        elif line.startswith("MemAvailable"):
                            available_gb = int(line.split()[1]) / (1024**2)
            except Exception:
                pass
        elif sys_p == "Darwin":
            try:
                out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode()
                total_ram_gb = int(out.strip()) / (1024**3)
                available_gb = total_ram_gb * 0.5
            except Exception:
                pass

    vram_gb  = 0.0
    gpu_name = ""

    # NVIDIA
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
        ).decode().strip().split("\n")[0]
        parts    = out.split(", ")
        gpu_name = parts[0].strip()
        vram_gb  = float(parts[1]) / 1024
    except Exception:
        pass

    # AMD
    if not vram_gb:
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                stderr=subprocess.DEVNULL,
            ).decode()
            for line in out.splitlines():
                if "VRAM Total Memory" in line:
                    vram_gb  = float(line.split(",")[-1]) / (1024**3)
                    gpu_name = "AMD GPU"
                    break
        except Exception:
            pass

    # Apple Silicon — unified memory
    if not vram_gb and sys_p == "Darwin":
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"],
                stderr=subprocess.DEVNULL,
            ).decode()
            if "Apple M" in out:
                gpu_name = "Apple Silicon"
                vram_gb  = total_ram_gb  # Unified memory
        except Exception:
            pass

    cpu_usable = max(0.0, available_gb - 4.0)
    usable_gb  = max(vram_gb, cpu_usable) if vram_gb > 0 else cpu_usable

    is_mobile = (
        "ANDROID_ROOT" in os.environ or
        sys_p == "Android" or
        (sys_p == "Linux" and Path("/sys/class/power_supply/battery").exists())
    )

    return DeviceProfile(
        total_ram_gb=total_ram_gb,
        available_gb=available_gb,
        vram_gb=vram_gb,
        gpu_name=gpu_name,
        cpu_cores=cpu_cores,
        sys_platform=sys_p,
        is_mobile=is_mobile,
        usable_gb=usable_gb,
        tailscale_ip=detect_tailscale_ip(),
    )


# ── Model selection logic ─────────────────────────────────────────────────────

def select_model(
    power_level: PowerLevel = PowerLevel.BALANCED,
    device: Optional[DeviceProfile] = None,
    force_phone: bool = False,
) -> AIModel:
    """
    Pick the best model given device hardware + power preference.
    If force_phone=True or device is mobile: return the phone model.
    """
    if device is None:
        device = detect_device()

    # Phone devices always use the phone model
    if force_phone or device.is_mobile:
        return PHONE_MODELS[0]

    pool = sorted(DESKTOP_MODELS, key=lambda m: m.size_gb)
    fitting = [m for m in pool if m.size_gb <= device.usable_gb]

    if not fitting:
        return pool[0]  # Use smallest even if tight

    if power_level == PowerLevel.FAST:
        return fitting[0]

    if power_level == PowerLevel.QUALITY:
        return fitting[-1]

    if power_level == PowerLevel.MAX:
        return pool[-1]

    # BALANCED — tiered by usable memory
    if device.usable_gb >= 22:
        target = "flagship"
    elif device.usable_gb >= 15:
        target = "mid"
    elif device.usable_gb >= 8:
        target = "small"
    else:
        target = "nano"

    tier_order = ["nano", "small", "mid", "large", "flagship"]
    for tier in reversed(tier_order[:tier_order.index(target) + 1]):
        for m in reversed(fitting):
            if m.tier == tier:
                return m

    return fitting[-1]


def recommend(
    power_level: PowerLevel = PowerLevel.BALANCED,
    force_phone: bool = False,
) -> dict:
    device = detect_device()
    model  = select_model(power_level, device, force_phone=force_phone)
    all_fitting = [m for m in DESKTOP_MODELS if m.size_gb <= device.usable_gb]

    result = {
        "device":      device.summary(),
        "usable_gb":   round(device.usable_gb, 1),
        "power_level": power_level.value,
        "recommended": {
            "name":        model.name,
            "ollama_tag":  model.ollama_tag,
            "size_gb":     model.size_gb,
            "context_k":   model.context_k,
            "multimodal":  model.multimodal,
            "description": model.description,
        },
        "all_fitting_models": [
            {"name": m.name, "tag": m.ollama_tag, "size_gb": m.size_gb}
            for m in all_fitting
        ],
    }

    if device.tailscale_ip:
        result["tailscale"] = {
            "ip":    device.tailscale_ip,
            "note":  "Phone AI server reachable at http://<phone-ts-ip>:11434",
        }

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CookieOS model selector")
    parser.add_argument("--power", "-p",
                        choices=["fast", "balanced", "quality", "max"],
                        default="balanced")
    parser.add_argument("--phone",  action="store_true",
                        help="Show phone model recommendation")
    parser.add_argument("--json",   action="store_true")
    args = parser.parse_args()

    level  = PowerLevel(args.power)
    report = recommend(level, force_phone=args.phone)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n🍪 CookieOS Model Selector")
        print(f"   Device:      {report['device']}")
        print(f"   Usable:      {report['usable_gb']} GB")
        print(f"   Power level: {report['power_level']}")
        print(f"\n   ✓ Recommended: {report['recommended']['name']}")
        print(f"     Tag:       {report['recommended']['ollama_tag']}")
        print(f"     Size:      {report['recommended']['size_gb']} GB")
        print(f"     Context:   {report['recommended']['context_k']}K tokens")
        print(f"     Multimodal: {report['recommended']['multimodal']}")
        print(f"     {report['recommended']['description']}")

        if report.get("tailscale"):
            print(f"\n   Tailscale active ({report['tailscale']['ip']})")
            print(f"   {report['tailscale']['note']}")

        print()
        if len(report["all_fitting_models"]) > 1:
            print("   All fitting models:")
            for m in report["all_fitting_models"]:
                star = "→" if m["tag"] == report["recommended"]["ollama_tag"] else " "
                print(f"    {star} {m['name']:22s}  {m['tag']:16s}  {m['size_gb']} GB")
        print()
