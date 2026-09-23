"""UUIDv7 generation (docs/03-modelo-datos.md: time-ordered ids, client-generatable offline).

Python 3.12 stdlib has no ``uuid.uuid7`` (added in 3.14), and no dependency is
already installed for it, so this implements RFC 9562 section 5.7 directly:
48-bit big-endian Unix ms timestamp, a 4-bit version, 12 bits of random
``rand_a``, a 2-bit variant, and 62 bits of random ``rand_b``.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7: sortable by creation time, unique per call."""
    unix_ms = int(time.time() * 1000)
    rand = os.urandom(10)

    time_bytes = unix_ms.to_bytes(6, "big")
    byte6 = 0x70 | (rand[0] & 0x0F)  # version 0111 + top 4 bits of rand_a
    byte7 = rand[1]  # remaining 8 bits of rand_a
    byte8 = 0x80 | (rand[2] & 0x3F)  # variant 10 + top 6 bits of rand_b
    rest = rand[3:10]  # remaining 56 bits of rand_b

    raw = time_bytes + bytes([byte6, byte7, byte8]) + rest
    return uuid.UUID(bytes=raw)
