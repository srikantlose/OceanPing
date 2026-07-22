"""Pure matching logic (phase 3, milestone 7): mutual-aid proximity+category
matching, and missing/found-person fuzzy-name matching. No DB/IO here —
service.py fetches rows and hands them in as plain dataclasses, the same
engine.py-is-pure / service.py-does-IO split every module in this project
uses."""
import difflib
from dataclasses import dataclass

from app.modules.geo.distance import haversine_km


@dataclass
class AidParty:
    id: str
    category: str
    lat: float
    lon: float


@dataclass
class AidMatch:
    request_id: str
    offer_id: str
    category: str
    distance_km: float


def match_aid(requests: list[AidParty], offers: list[AidParty], max_km: float) -> list[AidMatch]:
    """Every (request, offer) pair sharing a category within max_km, nearest
    first — a candidate list for a human to action, deliberately not an
    automatic assignment: one offer can and should surface against several
    nearby requests, and deciding which gets it first is a human call this
    app has no basis to make."""
    matches = []
    for req in requests:
        for off in offers:
            if req.category != off.category:
                continue
            dist = haversine_km(req.lat, req.lon, off.lat, off.lon)
            if dist <= max_km:
                matches.append(
                    AidMatch(request_id=req.id, offer_id=off.id, category=req.category, distance_km=round(dist, 2))
                )
    matches.sort(key=lambda m: m.distance_km)
    return matches


def fuzzy_name_score(a: str, b: str) -> float:
    """difflib's SequenceMatcher ratio — stdlib, deterministic, no model
    needed to catch likely misspellings/transliteration variants of a name.
    Case/whitespace-insensitive."""
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


@dataclass
class MissingCandidate:
    id: str
    name: str
    lat: float | None
    lon: float | None


@dataclass
class MissingMatch:
    candidate_id: str
    name_score: float
    distance_km: float | None


def rank_missing_matches(
    subject_name: str,
    subject_lat: float | None,
    subject_lon: float | None,
    candidates: list[MissingCandidate],
    *,
    name_threshold: float,
    max_km: float,
) -> list[MissingMatch]:
    """Candidates above name_threshold, best name match first. Geo-gated only
    when *both* sides carry a location — a phone-in report with no location
    shouldn't be penalized for it. Always a suggestion list: resolving a
    match is an analyst decision (see recovery/service.py::
    resolve_missing_person), never automatic — misidentifying a person is a
    far worse failure mode than surfacing one extra candidate."""
    out = []
    for cand in candidates:
        score = fuzzy_name_score(subject_name, cand.name)
        if score < name_threshold:
            continue
        distance_km = None
        if subject_lat is not None and subject_lon is not None and cand.lat is not None and cand.lon is not None:
            distance_km = round(haversine_km(subject_lat, subject_lon, cand.lat, cand.lon), 2)
            if distance_km > max_km:
                continue
        out.append(MissingMatch(candidate_id=cand.id, name_score=round(score, 3), distance_km=distance_km))
    out.sort(key=lambda m: m.name_score, reverse=True)
    return out
