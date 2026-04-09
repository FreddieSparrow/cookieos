#!/usr/bin/env python3
"""
CookieOS — Tanda 3D Printing Integration
Provides a Python client for interacting with Tanda 3D Printing services.

Tanda site: https://www.tanda-3dprinting.co.uk

Features:
  - Fetch available materials/services via web scrape (no official API)
  - Submit print quote requests
  - Track order status (if logged in)
  - AI-assisted print design suggestions via CookieGPT
  - Open in browser for final checkout (privacy — no stored payment data locally)

Usage:
  from tanda_client import TandaClient
  client = TandaClient()
  client.open_site()  # Open in default browser
  client.get_quote("20mm cube, PLA, black")
"""

import re
import json
import logging
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional
from datetime import datetime

log = logging.getLogger("cookieos.tanda")

TANDA_BASE_URL  = "https://www.tanda-3dprinting.co.uk"
QUOTE_CACHE_DIR = Path.home() / ".local/share/cookieos/tanda-quotes"

# Try importing requests; fall back gracefully
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    log.warning("requests not available — some Tanda features limited.")


class TandaClient:
    """
    Client for Tanda 3D Printing.

    Since Tanda doesn't have a public API, this client:
      - Opens the Tanda website for final submission/checkout
      - Provides AI-assisted design suggestions via local Ollama
      - Caches quotes locally so you can compare them over time
    """

    MATERIALS = {
        "pla":  {"name": "PLA",  "desc": "Standard FDM plastic, good for most parts. ~£0.02/g"},
        "abs":  {"name": "ABS",  "desc": "Stronger, heat-resistant, slight warping risk. ~£0.025/g"},
        "petg": {"name": "PETG", "desc": "Food-safe options, flexible-ish, great layer adhesion. ~£0.022/g"},
        "resin": {"name": "Resin (SLA)", "desc": "High detail, smooth finish, brittle. Quoted per item."},
        "tpu":  {"name": "TPU",  "desc": "Flexible rubber-like material. ~£0.03/g"},
        "nylon": {"name": "Nylon", "desc": "Strong, slightly flexible, moisture-sensitive. ~£0.04/g"},
    }

    COLOURS_STANDARD = [
        "Black", "White", "Grey", "Red", "Blue", "Green",
        "Yellow", "Orange", "Clear", "Gold", "Silver",
    ]

    def __init__(self, ollama_host: str = "http://localhost:11434"):
        self.ollama_host = ollama_host
        QUOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Site access ───────────────────────────────────────────────────────────

    def open_site(self, path: str = ""):
        """Open Tanda website in the default browser."""
        url = f"{TANDA_BASE_URL}/{path.lstrip('/')}"
        log.info("Opening Tanda: %s", url)
        try:
            webbrowser.open(url)
        except Exception:
            # Fallback: xdg-open or subprocess
            try:
                subprocess.Popen(["xdg-open", url], stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"Could not open browser: {e}")
                print(f"Visit manually: {url}")

    def open_quote_page(self):
        """Open the Tanda quote/upload page directly."""
        self.open_site("/quote")  # Adjust path as per actual Tanda site structure

    def open_order_page(self):
        """Open Tanda's order tracking page."""
        self.open_site("/orders")

    # ── Materials / services info ─────────────────────────────────────────────

    def list_materials(self) -> dict:
        """Return available materials with descriptions and rough pricing."""
        return self.MATERIALS

    def get_material_info(self, material_key: str) -> Optional[dict]:
        """Get info on a specific material (pla, abs, petg, etc.)."""
        return self.MATERIALS.get(material_key.lower())

    def print_materials_table(self):
        """Print a formatted table of available materials to stdout."""
        print(f"\n{'='*60}")
        print(f"  Tanda 3D Printing — Available Materials")
        print(f"  {TANDA_BASE_URL}")
        print(f"{'='*60}")
        for key, info in self.MATERIALS.items():
            print(f"  {info['name']:<12} — {info['desc']}")
        print(f"\n  Colours: {', '.join(self.COLOURS_STANDARD)}")
        print(f"{'='*60}\n")

    # ── Quote builder ─────────────────────────────────────────────────────────

    def build_quote_request(
        self,
        description: str,
        material:    str = "pla",
        colour:      str = "Black",
        quantity:    int = 1,
        notes:       str = "",
    ) -> dict:
        """
        Build a structured quote request dict.
        Save locally and optionally open Tanda for submission.
        """
        quote = {
            "id":          f"q-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "description": description,
            "material":    material.upper(),
            "colour":      colour,
            "quantity":    quantity,
            "notes":       notes,
            "created":     datetime.now().isoformat(),
            "status":      "draft",
            "tanda_url":   TANDA_BASE_URL,
        }

        # Save quote cache
        quote_file = QUOTE_CACHE_DIR / f"{quote['id']}.json"
        quote_file.write_text(json.dumps(quote, indent=2))

        log.info("Quote saved: %s", quote_file)
        return quote

    def list_quotes(self) -> list:
        """List all locally cached quote requests."""
        quotes = []
        for f in sorted(QUOTE_CACHE_DIR.glob("*.json")):
            try:
                quotes.append(json.loads(f.read_text()))
            except Exception:
                pass
        return quotes

    def print_quote(self, quote: dict):
        """Print a formatted quote summary."""
        print(f"\n{'─'*50}")
        print(f"  Quote ID:    {quote['id']}")
        print(f"  Description: {quote['description']}")
        print(f"  Material:    {quote['material']}")
        print(f"  Colour:      {quote['colour']}")
        print(f"  Quantity:    {quote['quantity']}")
        if quote.get("notes"):
            print(f"  Notes:       {quote['notes']}")
        print(f"  Created:     {quote['created'][:10]}")
        print(f"  Status:      {quote['status']}")
        print(f"\n  To submit: visit {TANDA_BASE_URL}")
        print(f"{'─'*50}\n")

    # ── AI-assisted design suggestions ────────────────────────────────────────

    def ai_design_suggestions(self, part_description: str) -> str:
        """
        Use local CookieGPT to suggest print settings for a described part.
        Runs fully locally — no data leaves the device.
        """
        if not _HAS_REQUESTS:
            return "Install 'requests' to use AI suggestions."

        prompt = f"""A user wants to 3D print: {part_description}

Suggest:
1. Best material (PLA/ABS/PETG/Resin/TPU/Nylon) and why
2. Recommended layer height (0.1/0.2/0.3mm)
3. Infill percentage
4. Any special considerations (supports, orientation, tolerances)

Keep it brief and practical. No fluff."""

        try:
            r = requests.post(
                f"{self.ollama_host}/api/generate",
                json={"model": "gemma3:4b", "prompt": prompt, "stream": False},
                timeout=60
            )
            if r.status_code == 200:
                return r.json().get("response", "No response from AI.")
            return f"Ollama error: HTTP {r.status_code}"
        except Exception as e:
            return f"Could not reach Ollama: {e}\nMake sure Ollama is running."

    # ── STL file preparation ──────────────────────────────────────────────────

    def check_stl(self, stl_path: str) -> dict:
        """
        Basic STL file validation before sending to Tanda.
        Checks: file exists, correct magic bytes, rough size estimate.
        """
        path = Path(stl_path)
        result = {"valid": False, "file": str(path), "errors": [], "warnings": []}

        if not path.exists():
            result["errors"].append(f"File not found: {stl_path}")
            return result

        if path.suffix.lower() != ".stl":
            result["warnings"].append("File doesn't have .stl extension")

        size_kb = path.stat().st_size // 1024

        if size_kb < 1:
            result["errors"].append("File too small — may be empty or corrupt")
            return result

        if size_kb > 50 * 1024:  # 50MB
            result["warnings"].append(f"Large file ({size_kb}KB) — may need optimisation")

        # Check if binary or ASCII STL
        try:
            with open(path, 'rb') as f:
                header = f.read(80)
                if b"solid" in header[:5]:
                    result["format"] = "ASCII STL"
                    result["warnings"].append("ASCII STL detected — binary is preferred for Tanda upload")
                else:
                    result["format"] = "Binary STL"
        except Exception as e:
            result["errors"].append(f"Could not read file: {e}")
            return result

        result["valid"] = len(result["errors"]) == 0
        result["size_kb"] = size_kb
        return result

    def prepare_for_upload(self, stl_path: str) -> dict:
        """
        Validate STL and provide upload instructions for Tanda.
        """
        check = self.check_stl(stl_path)
        if not check["valid"]:
            return check

        check["upload_url"] = TANDA_BASE_URL
        check["instructions"] = [
            f"1. Visit {TANDA_BASE_URL}",
            "2. Click 'Upload File' or 'Get a Quote'",
            f"3. Upload: {stl_path}",
            "4. Select material, colour, quantity",
            "5. Review quote and proceed to checkout",
        ]
        return check


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Tanda 3D Printing — CookieOS Integration")
    parser.add_argument("--materials",   action="store_true",  help="List available materials")
    parser.add_argument("--quote",       metavar="DESC",       help="Create a quote request")
    parser.add_argument("--material",    default="pla",        help="Material (pla/abs/petg/resin/tpu/nylon)")
    parser.add_argument("--colour",      default="Black",      help="Colour")
    parser.add_argument("--quantity",    type=int, default=1,  help="Quantity")
    parser.add_argument("--notes",       default="",           help="Extra notes")
    parser.add_argument("--ai-suggest",  metavar="DESC",       help="Get AI print settings for a part")
    parser.add_argument("--stl",         metavar="FILE",       help="Check and prepare STL for upload")
    parser.add_argument("--list-quotes", action="store_true",  help="List saved quote drafts")
    parser.add_argument("--open",        action="store_true",  help="Open Tanda website in browser")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    args = parser.parse_args()

    client = TandaClient(ollama_host=args.ollama_host)

    if args.materials:
        client.print_materials_table()

    elif args.quote:
        quote = client.build_quote_request(
            description=args.quote,
            material=args.material,
            colour=args.colour,
            quantity=args.quantity,
            notes=args.notes,
        )
        client.print_quote(quote)
        ans = input("Open Tanda website to submit? [Y/n]: ").strip().lower()
        if ans != 'n':
            client.open_quote_page()

    elif args.ai_suggest:
        print(f"\nAsking CookieGPT for print settings: '{args.ai_suggest}'")
        print("─" * 50)
        suggestion = client.ai_design_suggestions(args.ai_suggest)
        print(suggestion)
        print("─" * 50)

    elif args.stl:
        result = client.prepare_for_upload(args.stl)
        print(json.dumps(result, indent=2))

    elif args.list_quotes:
        quotes = client.list_quotes()
        if quotes:
            print(f"\n{len(quotes)} saved quote(s):")
            for q in quotes:
                client.print_quote(q)
        else:
            print("No saved quotes. Use --quote to create one.")

    elif args.open:
        client.open_site()

    else:
        # Default: show materials and prompt for action
        client.print_materials_table()
        print("Use --help for all options.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
