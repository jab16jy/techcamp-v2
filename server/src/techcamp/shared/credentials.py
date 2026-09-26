"""Node MQTT credential generation and hashing (docs/06-diseno-detallado.md
§2): the password is shown once and only its hash is stored.

In `shared/`, not `telemetry/adapters/`: it's pure stdlib with no I/O (like
`shared/ids.py`'s `uuid7`), and `telemetry/application/manage_nodes.py`
needs to call it without importing an adapter (ADR-0002's layering rule).

ponytail: production would also register the credential with Mosquitto
Dynamic Security (`createClient`) at claim/rotate time; the seminar broker is
anonymous (ADR-0021), so this module only generates and stores the hash.

No dependency already installed hashes a password with a per-call salt, and
stdlib's `hashlib.scrypt` does it directly (ponytail: stdlib first).
"""

from __future__ import annotations

import hashlib
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_DKLEN = 32


def generate_password() -> str:
    """A random, URL-safe one-time MQTT password (docs/04-api.md: shown once)."""
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt_hex$hash_hex`: self-describing so cost parameters
    can change later without breaking hashes already stored."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Not called by any T3 endpoint (Mosquitto, not this API, checks MQTT
    credentials in production); kept next to `hash_password` so a test can
    prove a generated password matches its own stored hash."""
    algorithm, n, r, p, salt_hex, hash_hex = stored_hash.split("$")
    if algorithm != "scrypt":
        return False
    derived = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=_DKLEN
    )
    return secrets.compare_digest(derived.hex(), hash_hex)
