"""Cooperative membership roster for fisherman-mode registration.

A real deployment would get this list from the fisheries cooperatives
themselves (they already keep member phone-number rolls for their own
welfare-scheme administration) and re-import it periodically. This
environment has no cooperative relationship to pull a real roster from, so
it ships a small, hand-seeded stand-in instead — the same honest-stub role
`ivr/locations.py`'s pilot location list and `satellite/providers.py`'s
StubProvider play elsewhere in this project. Matching is by phone number
(the identity a cooperative roster would actually contain), not by Telegram
ID, since a member's phone number is what the cooperative can attest to.
"""

MEMBERS = [
    {"phone": "+919840012345", "name": "M. Selvam", "cooperative": "Kasimedu Fishermen Welfare Cooperative"},
    {"phone": "+919840012346", "name": "R. Anbarasu", "cooperative": "Kasimedu Fishermen Welfare Cooperative"},
    {"phone": "+919840098765", "name": "K. Murugan", "cooperative": "Chennai Fishermen Cooperative Federation"},
    {"phone": "+919840098766", "name": "S. Velmurugan", "cooperative": "Chennai Fishermen Cooperative Federation"},
    {"phone": "+919840055512", "name": "P. Rajendran", "cooperative": "Ennore Fishermen Welfare Society"},
]


def _normalize(phone: str) -> str:
    """Keep only digits, then compare the last 10 (the local subscriber
    number) so it doesn't matter whether a country code or a leading zero
    was included — Telegram's shared-contact phone numbers are inconsistent
    about this depending on the client."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


_BY_NORMALIZED_PHONE = {_normalize(m["phone"]): m for m in MEMBERS}


def member_for_phone(phone: str) -> dict | None:
    return _BY_NORMALIZED_PHONE.get(_normalize(phone))
