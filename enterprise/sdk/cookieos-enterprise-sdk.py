#!/usr/bin/env python3
"""
CookieOS Enterprise SDK
Allows organisations and developers to build custom AI features,
security policies, and integrations on top of CookieOS.

Enterprise features (beyond Community):
  - Custom Ollama model endpoints and fine-tuning API
  - Multi-tenant user management (LDAP/SSO integration)
  - Centralised policy management (push config to fleet)
  - Extended audit logging with SIEM export
  - Priority CookieAI model routing (CookieHost GPU servers)
  - Custom AppArmor/SELinux policy generator (AI-assisted)
  - White-labelling support
  - Enterprise support SLA via CookieNet

SDK usage:
  from cookieos_enterprise_sdk import EnterpriseClient, AIBuilder, PolicyManager

  client  = EnterpriseClient(api_key="...", org="acme-corp")
  builder = AIBuilder(client)
  builder.create_assistant(
      name="Acme Support Bot",
      model="gemma4:4b",
      system_prompt="You are the Acme Corp IT support assistant...",
      rag_sources=["/data/acme-docs/"],
  )
"""

import os
import json
import time
import logging
import hashlib
import getpass
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime

import requests

log = logging.getLogger("cookieos.enterprise")

ENTERPRISE_API_BASE = os.environ.get(
    "COOKIEOS_ENTERPRISE_API",
    "http://localhost:11435",   # Local enterprise API server
)

SDK_VERSION = "1.0.0"


# ── Licence validation ────────────────────────────────────────────────────────

class LicenceError(Exception):
    pass


class EnterpriseLicence:
    """Validates CookieOS Enterprise licence (offline-capable via signed token)."""

    LICENCE_FILE = Path.home() / ".config/cookieos/enterprise/licence.json"

    def __init__(self, licence_key: str | None = None):
        if licence_key:
            self._load_from_key(licence_key)
        elif self.LICENCE_FILE.exists():
            self._data = json.loads(self.LICENCE_FILE.read_text())
        else:
            raise LicenceError(
                "No CookieOS Enterprise licence found.\n"
                "Contact CookieNet at cookiehost.uk for Enterprise licensing."
            )

    def _load_from_key(self, key: str):
        # In production: verify cryptographic signature from CookieNet
        # For now: decode and validate structure
        try:
            import base64
            decoded = json.loads(base64.b64decode(key.split(".")[1] + "=="))
            self._data = decoded
            self.LICENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.LICENCE_FILE.write_text(json.dumps(decoded, indent=2))
            self.LICENCE_FILE.chmod(0o600)
        except Exception as e:
            raise LicenceError(f"Invalid licence key: {e}")

    @property
    def org(self) -> str:
        return self._data.get("org", "unknown")

    @property
    def tier(self) -> str:
        return self._data.get("tier", "community")  # community | professional | enterprise

    @property
    def max_users(self) -> int:
        return self._data.get("max_users", 5)

    @property
    def features(self) -> list[str]:
        return self._data.get("features", [])

    def require_feature(self, feature: str):
        if feature not in self.features:
            raise LicenceError(
                f"Feature '{feature}' not included in your {self.tier} licence.\n"
                f"Upgrade at cookiehost.uk/enterprise"
            )

    def __repr__(self):
        return f"<EnterpriseLicence org={self.org!r} tier={self.tier!r}>"


# ── Enterprise client ─────────────────────────────────────────────────────────

class EnterpriseClient:
    """Base client for CookieOS Enterprise API."""

    def __init__(
        self,
        api_key:    str | None = None,
        org:        str = "",
        licence_key: str | None = None,
        server:     str = ENTERPRISE_API_BASE,
    ):
        self.server  = server.rstrip("/")
        self.api_key = api_key or os.environ.get("COOKIEOS_ENTERPRISE_KEY", "")
        self.licence = EnterpriseLicence(licence_key)
        self.org     = org or self.licence.org
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["Authorization"] = f"Bearer {self.api_key}"
        self._session.headers["X-CookieOS-SDK"] = SDK_VERSION
        self._session.headers["X-CookieOS-Org"] = self.org

    def _post(self, path: str, data: dict) -> dict:
        r = self._session.post(f"{self.server}{path}", json=data, timeout=60)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = self._session.get(f"{self.server}{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        return self._get("/health")


# ── AI Builder — custom assistant creation ────────────────────────────────────

class AIBuilder:
    """
    Build custom AI assistants using Ollama models with:
    - Custom system prompts
    - RAG from local/CookieCloud document sources
    - Custom safety policies (extend default CookieOS filters)
    - Output webhooks (integrate with n8n, Slack, etc.)
    """

    def __init__(self, client: EnterpriseClient):
        self.client = client
        client.licence.require_feature("ai_builder")

    def create_assistant(
        self,
        name:            str,
        model:           str = "gemma4:4b",
        system_prompt:   str = "",
        rag_sources:     list[str] | None = None,
        safety_policy:   dict | None = None,
        webhook_url:     str | None = None,
        temperature:     float = 0.7,
    ) -> "CustomAssistant":
        """
        Create and register a custom AI assistant.

        Example:
            assistant = builder.create_assistant(
                name="IT Helpdesk Bot",
                model="gemma4:4b",
                system_prompt="You are the IT support bot for Acme Corp. ...",
                rag_sources=["/data/it-docs/", "/data/kb/"],
                safety_policy={"block_topics": ["confidential projects"]},
            )
            reply = assistant.chat("How do I reset my password?")
        """
        config = {
            "name":          name,
            "model":         model,
            "system_prompt": system_prompt,
            "rag_sources":   rag_sources or [],
            "safety_policy": safety_policy or {},
            "webhook_url":   webhook_url,
            "temperature":   temperature,
            "org":           self.client.org,
            "created":       datetime.utcnow().isoformat(),
        }
        assistant = CustomAssistant(config, self.client)
        assistant.save()
        log.info("Assistant '%s' created for %s", name, self.client.org)
        return assistant

    def list_assistants(self) -> list[str]:
        assistant_dir = Path.home() / ".config/cookieos/enterprise/assistants"
        if not assistant_dir.exists():
            return []
        return [p.stem for p in assistant_dir.glob("*.json")]

    def load_assistant(self, name: str) -> "CustomAssistant":
        path = Path.home() / f".config/cookieos/enterprise/assistants/{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Assistant '{name}' not found.")
        config = json.loads(path.read_text())
        return CustomAssistant(config, self.client)


class CustomAssistant:
    """A configured AI assistant with custom prompt + RAG + safety policy."""

    def __init__(self, config: dict, client: EnterpriseClient):
        self.cfg    = config
        self.client = client
        self._history: list[dict] = []

        # Import CookieOS components
        import sys
        root = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(root / "ai" / "safeguards"))
        sys.path.insert(0, str(root / "ai" / "ollama"))
        from content_filter import check_prompt, Severity
        from cookie_ollama  import OllamaClient, build_rag_context

        self._filter        = check_prompt
        self._Severity      = Severity
        self._OllamaClient  = OllamaClient
        self._build_rag     = build_rag_context

        # Build system message
        self._system = config.get("system_prompt", "")
        if config.get("rag_sources"):
            paths       = [Path(s) for s in config["rag_sources"]]
            rag_context = build_rag_context(paths)
            self._system += f"\n\nKnowledge base:\n{rag_context[:8000]}"

        self._history = [{"role": "system", "content": self._system}]

    def save(self):
        out_dir = Path.home() / ".config/cookieos/enterprise/assistants"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.cfg['name']}.json"
        path.write_text(json.dumps(self.cfg, indent=2))
        path.chmod(0o600)

    def chat(self, user_message: str, user_id: str = "enterprise") -> str:
        # Merge default + custom safety policy
        custom_blocks = self.cfg.get("safety_policy", {}).get("block_topics", [])
        if custom_blocks:
            for topic in custom_blocks:
                if topic.lower() in user_message.lower():
                    return f"I can't discuss '{topic}' — it's restricted by your organisation's policy."

        # Standard CookieOS safety check
        result = self._filter(user_message, user_id=user_id)
        if not result.allowed:
            return f"Request blocked by CookieOS safety filter: {result.reason}"

        # Chat via Ollama
        ollama = self._OllamaClient()
        if not ollama.is_available():
            return "Ollama is not running. Start Ollama and try again."

        self._history.append({"role": "user", "content": user_message})
        full_response = ""
        try:
            for chunk in ollama.chat_stream(
                self._history,
                model=self.cfg.get("model", "gemma4:4b"),
                temperature=self.cfg.get("temperature", 0.7),
            ):
                full_response += chunk
        except Exception as e:
            return f"[AI error: {e}]"

        self._history.append({"role": "assistant", "content": full_response})

        # Webhook
        if self.cfg.get("webhook_url"):
            try:
                requests.post(self.cfg["webhook_url"], json={
                    "assistant": self.cfg["name"],
                    "user_msg":  user_message[:500],
                    "reply":     full_response[:500],
                }, timeout=5)
            except Exception:
                pass

        return full_response

    def clear_history(self):
        self._history = [{"role": "system", "content": self._system}]


# ── Policy Manager — fleet config push ───────────────────────────────────────

class PolicyManager:
    """
    Push security and configuration policies to a fleet of CookieOS devices.
    Requires Enterprise licence with 'fleet_management' feature.
    """

    def __init__(self, client: EnterpriseClient):
        self.client = client
        client.licence.require_feature("fleet_management")

    def push_policy(
        self,
        policy_name: str,
        config:      dict,
        targets:     list[str] | None = None,   # Tailscale IPs; None = all devices
    ) -> dict:
        """Push a named policy to fleet devices over Tailscale."""
        payload = {
            "policy":  policy_name,
            "config":  config,
            "targets": targets,
            "issued":  datetime.utcnow().isoformat(),
            "issuer":  self.client.org,
        }
        # In production: push via CookieOS fleet management daemon (port 11436)
        results = {}
        _targets = targets or self._discover_fleet()
        for device_ip in _targets:
            try:
                r = requests.post(
                    f"http://{device_ip}:11436/apply-policy",
                    json=payload,
                    timeout=15,
                )
                results[device_ip] = "ok" if r.status_code == 200 else "failed"
            except Exception as e:
                results[device_ip] = f"error: {e}"
        return results

    def _discover_fleet(self) -> list[str]:
        """Discover fleet devices via Tailscale."""
        import subprocess
        try:
            out = subprocess.check_output(
                ["tailscale", "status", "--json"],
                stderr=subprocess.DEVNULL,
            ).decode()
            data = json.loads(out)
            return [
                peer["TailscaleIPs"][0]
                for peer in data.get("Peer", {}).values()
                if peer.get("Tags") and "tag:cookieos" in peer["Tags"]
            ]
        except Exception:
            return []

    def get_compliance_report(self) -> dict:
        """Pull compliance status from all fleet devices."""
        devices = self._discover_fleet()
        report  = {"generated": datetime.utcnow().isoformat(), "devices": {}}
        for ip in devices:
            try:
                r = requests.get(f"http://{ip}:11436/compliance", timeout=10)
                report["devices"][ip] = r.json()
            except Exception as e:
                report["devices"][ip] = {"error": str(e)}
        return report


# ── Quick-start example ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CookieOS Enterprise SDK demo")
    parser.add_argument("--create-assistant", metavar="NAME",
                        help="Create a new custom assistant")
    parser.add_argument("--prompt",           help="System prompt for assistant")
    parser.add_argument("--model",            default="gemma4:4b")
    parser.add_argument("--chat",             metavar="ASSISTANT",
                        help="Chat with a named assistant")
    args = parser.parse_args()

    # Mock licence for demo
    import base64
    demo_licence = base64.b64encode(json.dumps({
        "org":       "demo",
        "tier":      "enterprise",
        "features":  ["ai_builder", "fleet_management", "custom_models"],
        "max_users": 50,
    }).encode()).decode()

    client = EnterpriseClient(
        org="demo",
        licence_key=f"header.{demo_licence}.sig",
    )
    builder = AIBuilder(client)

    if args.create_assistant:
        assistant = builder.create_assistant(
            name=args.create_assistant,
            model=args.model,
            system_prompt=args.prompt or "You are a helpful enterprise assistant.",
        )
        print(f"✓ Assistant '{args.create_assistant}' created.")

    elif args.chat:
        assistant = builder.load_assistant(args.chat)
        print(f"Chatting with: {args.chat}  (type 'exit' to quit)\n")
        while True:
            msg = input("You: ").strip()
            if msg.lower() == "exit":
                break
            reply = assistant.chat(msg)
            print(f"Bot: {reply}\n")

    else:
        print("CookieOS Enterprise SDK")
        print(f"  Licence: {client.licence}")
        print(f"  Assistants: {builder.list_assistants()}")
        print()
        print("Use --help for options.")
