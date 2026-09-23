"""Seminar-local JWT issuer and JWKS-style validator (ADR-0014, ADR-0021).

One RSA keypair is generated per process and identified by `_KEY_ID`. Issuing
signs with the private key; validating resolves the public key by the
token's `kid` header, then checks signature, `iss`, `aud` and `exp` — the
same validation shape production will use against Supabase's real JWKS
endpoint, minus the network fetch (issuer and validator share one process
here, so there is nothing to fetch over HTTP yet).
"""

from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from techcamp.shared.config import jwt_audience, jwt_issuer

_KEY_ID = "seminar-local-1"
_TOKEN_TTL_SECONDS = 3600

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()


class InvalidTokenError(Exception):
    """Raised when a bearer token fails validation (signature, iss, aud or exp)."""


def issue_token(subject: str, *, ttl_seconds: int = _TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    claims = {
        "iss": jwt_issuer(),
        "aud": jwt_audience(),
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, _private_key, algorithm="RS256", headers={"kid": _KEY_ID})


def decode_token(token: str) -> dict[str, str]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Malformed token") from exc
    if header.get("kid") != _KEY_ID:
        raise InvalidTokenError("Unknown signing key")
    try:
        return jwt.decode(
            token,
            _public_key,
            algorithms=["RS256"],
            audience=jwt_audience(),
            issuer=jwt_issuer(),
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(str(exc)) from exc
