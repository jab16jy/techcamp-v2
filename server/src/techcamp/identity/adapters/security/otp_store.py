"""In-memory OTP challenge store for the seminar auth emulator (ADR-0021).

The real code lives only in-process for a few minutes; there is no product
requirement to persist it, so a DB table would be speculative for a dev-only
emulator that production replaces outright with Supabase Auth (ADR-0014).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

_TTL = timedelta(minutes=5)


class OtpStore:
    def __init__(self) -> None:
        self._codes: dict[str, tuple[str, datetime]] = {}

    def issue(self, phone: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._codes[phone] = (code, datetime.now(UTC) + _TTL)
        return code

    def verify(self, phone: str, code: str) -> bool:
        entry = self._codes.get(phone)
        if entry is None:
            return False
        stored_code, expires_at = entry
        del self._codes[phone]
        if datetime.now(UTC) > expires_at:
            return False
        return secrets.compare_digest(stored_code, code)


otp_store = OtpStore()
