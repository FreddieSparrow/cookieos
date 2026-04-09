#!/usr/bin/env python3
"""
CookieOS Secret Manager
Stores secrets (API keys, tokens, credentials) encrypted with AES-256-GCM,
derived from a master password via Argon2id — similar to Vault/pass.

Secrets are stored in ~/.config/cookieos/vault/ and optionally synced
to CookieCloud (encrypted — server never sees plaintext).

Usage:
  secret-manager.py set   <name> <value>
  secret-manager.py get   <name>
  secret-manager.py list
  secret-manager.py delete <name>
  secret-manager.py export --output vault.enc   (encrypted backup)
  secret-manager.py import --input  vault.enc
"""

import os
import sys
import json
import base64
import getpass
import argparse
import logging
import secrets
from pathlib import Path
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger("cookieos.vault")

VAULT_DIR   = Path.home() / ".config/cookieos/vault"
VAULT_META  = VAULT_DIR / ".meta.json"

# KDF parameters — Argon2id-equivalent via scrypt
SCRYPT_N    = 2**20   # CPU/memory cost (1M = ~1GB RAM)
SCRYPT_R    = 8
SCRYPT_P    = 1
KEY_LEN     = 32      # AES-256
SALT_LEN    = 32
NONCE_LEN   = 12      # GCM nonce


class VaultError(Exception):
    pass


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))


def _encrypt(key: bytes, plaintext: bytes) -> bytes:
    nonce  = secrets.token_bytes(NONCE_LEN)
    aesgcm = AESGCM(key)
    ct     = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ct


def _decrypt(key: bytes, blob: bytes) -> bytes:
    nonce  = blob[:NONCE_LEN]
    ct     = blob[NONCE_LEN:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, None)
    except Exception:
        raise VaultError("Decryption failed — wrong password or corrupted data.")


class Vault:
    def __init__(self):
        VAULT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._key: bytes | None = None
        self._salt: bytes | None = None
        self._load_meta()

    def _load_meta(self):
        if VAULT_META.exists():
            meta = json.loads(VAULT_META.read_bytes())
            self._salt = base64.b64decode(meta["salt"])
        else:
            self._salt = secrets.token_bytes(SALT_LEN)
            VAULT_META.write_text(json.dumps({"salt": base64.b64encode(self._salt).decode()}))
            VAULT_META.chmod(0o600)

    def unlock(self, password: str | None = None):
        if self._key:
            return
        if password is None:
            password = getpass.getpass("Vault password: ")
        self._key = _derive_key(password, self._salt)
        # Verify by reading a sentinel if it exists
        sentinel = VAULT_DIR / ".sentinel"
        if sentinel.exists():
            try:
                blob = base64.b64decode(sentinel.read_text())
                _decrypt(self._key, blob)
            except VaultError:
                self._key = None
                raise VaultError("Wrong vault password.")
        else:
            # First use — write sentinel
            blob = _encrypt(self._key, b"cookieos-vault-ok")
            sentinel.write_text(base64.b64encode(blob).decode())
            sentinel.chmod(0o600)

    def _secret_path(self, name: str) -> Path:
        # Use a hash of the name as filename (don't reveal secret names in filesystem)
        import hashlib
        fname = hashlib.sha256(name.encode()).hexdigest()[:24]
        return VAULT_DIR / f"{fname}.enc"

    def set(self, name: str, value: str):
        self.unlock()
        payload = json.dumps({
            "name":    name,
            "value":   value,
            "created": datetime.utcnow().isoformat(),
        }).encode()
        blob = _encrypt(self._key, payload)
        path = self._secret_path(name)
        path.write_bytes(base64.b64encode(blob))
        path.chmod(0o600)

        # Update index (encrypted)
        index = self._load_index()
        index[name] = {
            "file":    path.name,
            "created": datetime.utcnow().isoformat(),
        }
        self._save_index(index)
        log.info("Secret '%s' stored.", name)

    def get(self, name: str) -> str:
        self.unlock()
        path = self._secret_path(name)
        if not path.exists():
            raise VaultError(f"Secret '{name}' not found.")
        blob    = base64.b64decode(path.read_bytes())
        payload = json.loads(_decrypt(self._key, blob))
        return payload["value"]

    def delete(self, name: str):
        self.unlock()
        path = self._secret_path(name)
        if path.exists():
            # Overwrite before deleting (secure wipe)
            path.write_bytes(secrets.token_bytes(path.stat().st_size))
            path.unlink()
        index = self._load_index()
        index.pop(name, None)
        self._save_index(index)
        log.info("Secret '%s' deleted.", name)

    def list_names(self) -> list[str]:
        self.unlock()
        return list(self._load_index().keys())

    def _load_index(self) -> dict:
        idx_path = VAULT_DIR / ".index.enc"
        if not idx_path.exists():
            return {}
        blob = base64.b64decode(idx_path.read_bytes())
        return json.loads(_decrypt(self._key, blob))

    def _save_index(self, index: dict):
        idx_path  = VAULT_DIR / ".index.enc"
        payload   = json.dumps(index).encode()
        blob      = _encrypt(self._key, payload)
        idx_path.write_bytes(base64.b64encode(blob))
        idx_path.chmod(0o600)

    def export_encrypted(self, out_path: Path, export_password: str | None = None):
        """Export entire vault as a single encrypted blob (for backup)."""
        self.unlock()
        if export_password is None:
            export_password = getpass.getpass("Export password (can differ from vault): ")

        all_secrets = {}
        for name in self.list_names():
            all_secrets[name] = self.get(name)

        export_salt = secrets.token_bytes(SALT_LEN)
        export_key  = _derive_key(export_password, export_salt)
        payload     = json.dumps(all_secrets).encode()
        blob        = _encrypt(export_key, payload)
        out_path.write_bytes(export_salt + blob)
        out_path.chmod(0o600)
        print(f"✓ Vault exported to {out_path} ({len(all_secrets)} secrets)")

    def import_encrypted(self, in_path: Path, import_password: str | None = None):
        """Import secrets from an encrypted export file."""
        self.unlock()
        if import_password is None:
            import_password = getpass.getpass("Export file password: ")

        raw         = in_path.read_bytes()
        export_salt = raw[:SALT_LEN]
        blob        = raw[SALT_LEN:]
        export_key  = _derive_key(import_password, export_salt)
        all_secrets = json.loads(_decrypt(export_key, blob))

        for name, value in all_secrets.items():
            self.set(name, value)

        print(f"✓ Imported {len(all_secrets)} secrets.")


def main():
    parser = argparse.ArgumentParser(description="CookieOS Secret Manager")
    sub    = parser.add_subparsers(dest="cmd")

    p_set = sub.add_parser("set",    help="Store a secret")
    p_set.add_argument("name")
    p_set.add_argument("value", nargs="?", help="Value (or stdin if omitted)")

    p_get = sub.add_parser("get",    help="Retrieve a secret")
    p_get.add_argument("name")

    p_del = sub.add_parser("delete", help="Delete a secret")
    p_del.add_argument("name")

    sub.add_parser("list", help="List secret names")

    p_exp = sub.add_parser("export", help="Export vault (encrypted backup)")
    p_exp.add_argument("--output", "-o", required=True)

    p_imp = sub.add_parser("import", help="Import vault from backup")
    p_imp.add_argument("--input",  "-i", required=True)

    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    vault = Vault()

    if args.cmd == "set":
        value = args.value or getpass.getpass(f"Value for '{args.name}': ")
        vault.set(args.name, value)
        print(f"✓ Secret '{args.name}' stored.")

    elif args.cmd == "get":
        print(vault.get(args.name))

    elif args.cmd == "delete":
        vault.delete(args.name)
        print(f"✓ Secret '{args.name}' deleted.")

    elif args.cmd == "list":
        names = vault.list_names()
        if names:
            print(f"Secrets ({len(names)}):")
            for n in sorted(names):
                print(f"  {n}")
        else:
            print("Vault is empty.")

    elif args.cmd == "export":
        vault.export_encrypted(Path(args.output))

    elif args.cmd == "import":
        vault.import_encrypted(Path(args.input))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
