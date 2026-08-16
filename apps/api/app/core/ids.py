"""Opaque public identifiers.

SPEC §4.2 forbids exposing database primary keys in URLs or API responses for patients. Every
patient therefore carries a ``public_id`` alongside its internal UUID, and that is the only
identifier the outside world ever sees.

Two reasons this matters:

* A sequential or guessable identifier lets anyone who obtains one URL walk the whole patient
  list by incrementing a number. UUIDs are not guessable, but they are long, ugly in a URL, and
  they leak which database row a record is — including its creation ordering, for v1 UUIDs.
* Internal keys are an implementation detail. Once they appear in a URL that someone has
  bookmarked, they are a public API and can never be changed.
"""

from __future__ import annotations

import secrets

# Crockford's base32 alphabet: the digits and letters that remain unambiguous when a human reads
# them aloud or copies them off a screen. I, L, O and U are excluded — I/1, L/1 and O/0 are the
# classic misreads, and U is dropped so the encoding cannot accidentally spell certain words.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# 12 characters of a 32-symbol alphabet is 60 bits of entropy — about 1.15 x 10^18 possibilities.
# Far beyond anything an attacker could enumerate against a rate-limited API, and still short
# enough to read out over the phone.
PUBLIC_ID_LENGTH = 12


def generate_public_id(length: int = PUBLIC_ID_LENGTH) -> str:
    """Return a new random, opaque public identifier.

    Uses ``secrets`` rather than ``random``: the latter is seeded predictably and is not safe for
    anything an outsider might try to guess.
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
