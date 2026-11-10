"""Embed an OpenRouter API key into _bundled.py (obfuscated).

Usage:
    python scripts/embed_key.py sk-or-v1-...
"""
import base64
import re
import sys
from pathlib import Path

_SALT = b"\x5a\x3c\x7f\x21\x8b\x44\x9d\x12"
_TARGET = Path(__file__).parent.parent / "src" / "triconvey_agent" / "backend" / "_bundled.py"


def encode(key: str) -> str:
    raw = key.encode()
    return base64.b64encode(bytes(b ^ _SALT[i % len(_SALT)] for i, b in enumerate(raw))).decode()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/embed_key.py <openrouter-api-key>")
        sys.exit(1)
    key = sys.argv[1].strip()
    blob = encode(key)
    text = _TARGET.read_text(encoding="utf-8")
    text = re.sub(r'^_K = ".*"$', f'_K = "{blob}"', text, flags=re.MULTILINE)
    _TARGET.write_text(text, encoding="utf-8")
    print(f"Key embedded into {_TARGET}")


if __name__ == "__main__":
    main()
