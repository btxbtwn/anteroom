#!/usr/bin/env python3
"""Create the key that signs web Save draft commands for the Chrome extension.

Writes the public key into ``vendoo-extension/cloud-command-key.js`` (commit
that) and prints the private key as one ``ANTEROOM_COMMAND_SIGNING_KEY=...``
line for Fly, so it never lands in a file:

    python scripts/command-signing-key.py | fly secrets import --stage -a peaceful-meadowstone-6944

A new key replaces the old one: commands signed with the old key stop
verifying once the updated extension is installed. See docs/cloud-extension.md.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

KEY_FILE = Path(__file__).resolve().parents[2] / "vendoo-extension" / "cloud-command-key.js"
HEADER = KEY_FILE.read_text().split("var CLOUD_COMMAND_KEYS", 1)[0]


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    public = {"kty": "EC", "crv": "P-256", "x": _b64(numbers.x), "y": _b64(numbers.y)}
    KEY_FILE.write_text(f"{HEADER}var CLOUD_COMMAND_KEYS = [{json.dumps(public)}];\n")
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    print(f"ANTEROOM_COMMAND_SIGNING_KEY={base64.b64encode(der).decode()}")


if __name__ == "__main__":
    main()
