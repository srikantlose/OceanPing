"""Rumor-narrative clustering, contradiction detection, and correction-
message templates — pure functions, no I/O.

Clusters citizen reports by text-embedding similarity, not spatial adjacency
(unlike `nlp/dedup.py`'s incident merge): the same rumor ("another wave is
coming") can spread across multiple locations/hazard-adjacent incidents that
would never merge into one Incident, and that cross-location spread is
exactly the shape a rumor takes that plain spatial clustering can't see. A
cluster only becomes a flagged narrative once it also contradicts something
real — see `is_contradiction`.
"""
from collections import Counter
from dataclasses import dataclass, field

from app.modules.nlp.dedup import cosine

MIN_NARRATIVE_REPORTS = 3
SIM_THRESHOLD = 0.55


@dataclass
class Cluster:
    report_ids: list = field(default_factory=list)
    h3_cells: set = field(default_factory=set)
    texts: list = field(default_factory=list)
    embeddings: list = field(default_factory=list)
    hazard_types: list = field(default_factory=list)
    lats: list = field(default_factory=list)
    lons: list = field(default_factory=list)
    statuses: list = field(default_factory=list)
    centroid_embedding: list | None = None

    def add(self, report: dict) -> None:
        n = len(self.report_ids)
        self.report_ids.append(report["id"])
        self.h3_cells.add(report["h3_cell"])
        self.texts.append(report["text"])
        self.embeddings.append(report["embedding"])
        self.hazard_types.append(report["hazard_type"])
        self.lats.append(report["lat"])
        self.lons.append(report["lon"])
        self.statuses.append(report["status"])
        if self.centroid_embedding is None:
            self.centroid_embedding = list(report["embedding"])
        else:
            self.centroid_embedding = [
                (c * n + e) / (n + 1) for c, e in zip(self.centroid_embedding, report["embedding"])
            ]

    @property
    def report_count(self) -> int:
        return len(self.report_ids)

    def dominant_hazard(self) -> str:
        return Counter(self.hazard_types).most_common(1)[0][0]

    def centroid(self) -> tuple[float, float]:
        return sum(self.lats) / len(self.lats), sum(self.lons) / len(self.lons)

    def rejected_count(self) -> int:
        return sum(1 for s in self.statuses if s == "rejected")

    def representative_text(self) -> str:
        """The single member report whose embedding sits closest to the
        cluster's centroid — the most "typical" phrasing of the claim."""
        best_i, best_sim = 0, -2.0
        for i, emb in enumerate(self.embeddings):
            sim = cosine(emb, self.centroid_embedding)
            if sim > best_sim:
                best_sim, best_i = sim, i
        return self.texts[best_i]


def cluster_reports(reports: list[dict], sim_threshold: float = SIM_THRESHOLD) -> list[Cluster]:
    """Greedy single-pass clustering, oldest report first: each report joins
    whichever existing cluster its embedding is most similar to (if that
    clears `sim_threshold`), else starts a new one. `reports` are plain dicts
    — {id, h3_cell, embedding, text, hazard_type, lat, lon, status,
    created_at} — so this stays a pure function independent of the ORM.
    Clusters smaller than MIN_NARRATIVE_REPORTS are dropped: a single
    secondhand text isn't a "narrative" spreading anywhere yet."""
    clusters: list[Cluster] = []
    for r in sorted(reports, key=lambda r: r["created_at"]):
        best_cluster, best_sim = None, sim_threshold
        for c in clusters:
            sim = cosine(r["embedding"], c.centroid_embedding)
            if sim >= best_sim:
                best_cluster, best_sim = c, sim
        if best_cluster is None:
            best_cluster = Cluster()
            clusters.append(best_cluster)
        best_cluster.add(r)
    return [c for c in clusters if c.report_count >= MIN_NARRATIVE_REPORTS]


def is_contradiction(instrument_flat: bool, hazard_has_instrument_signal: bool, rejected_count: int) -> bool:
    """A narrative is worth an analyst's attention only when it contradicts
    something real: either an analyst has already rejected a member report
    (the strongest available signal — a human already looked and disagreed),
    or the claimed hazard has an instrument signal defined at all (see
    scoring/engine.py's HAZARD_VARIABLES) and nothing active corroborates it
    nearby. Hazards with no instrument signal (oil_spill, algal_bloom) can
    only be flagged via the rejected-report path — "instruments show
    nothing" is meaningless for a hazard no instrument ever measures."""
    if rejected_count > 0:
        return True
    return hazard_has_instrument_signal and instrument_flat


# Rumor-correction templates — deterministic, always-available fallback when
# no LLM adapter is configured (see modules/narratives/service.py), and the
# fact-set an LLM-polished English variant is constrained to. Tamil/Telugu
# stay template-only always (never LLM-polished) — same caution
# ingest/report_conversation.py already applies to those languages: a first
# pass, not reviewed by a native speaker, so nothing here should get less
# predictable than a fixed template.
_REASON_REJECTED = {
    "en": "an analyst has already reviewed similar reports and found no hazard",
    "ta": "இதே போன்ற தகவல்களை பகுப்பாய்வாளர் ஏற்கனவே சரிபார்த்து ஆபத்து இல்லை என கண்டறிந்துள்ளார்",
    "te": "ఇలాంటి నివేదికలను విశ్లేషకుడు ఇప్పటికే సమీక్షించి ప్రమాదం లేదని నిర్ధారించారు",
}
_REASON_INSTRUMENT_FLAT = {
    "en": "nearby monitoring instruments show nothing unusual",
    "ta": "அருகிலுள்ள கண்காணிப்பு கருவிகளில் அசாதாரணமான எதுவும் இல்லை",
    "te": "సమీపంలోని పర్యవేక్షణ పరికరాల్లో అసాధారణమైనది ఏమీ లేదు",
}
_CORRECTION_STANDARD = {
    "en": "OceanPing correction: recent reports of {label} near {location} are not confirmed — {reason}. "
          "Please don't share this further; check the map or official channels for verified alerts.",
    "ta": "OceanPing திருத்தம்: {location} அருகில் {label} பற்றிய சமீபத்திய தகவல்கள் உறுதிப்படுத்தப்படவில்லை — "
          "{reason}. இதை மேலும் பகிர வேண்டாம்; சரிபார்க்கப்பட்ட எச்சரிக்கைகளுக்கு வரைபடத்தை அல்லது "
          "அதிகாரப்பூர்வ தகவல்களை பார்க்கவும்.",
    "te": "OceanPing సవరణ: {location} సమీపంలో {label} గురించి ఇటీవలి నివేదికలు ధృవీకరించబడలేదు — {reason}. "
          "దీన్ని ఇంకా షేర్ చేయవద్దు; నిర్ధారించిన హెచ్చరికల కోసం మ్యాప్ లేదా అధికారిక ఛానెల్‌లను తనిఖీ చేయండి.",
}
_CORRECTION_SHORT = {
    "en": "OceanPing: {label} report near {location} NOT confirmed. Please don't share. Check official alerts.",
    "ta": "OceanPing: {location} அருகில் {label} உறுதிப்படுத்தப்படவில்லை. பகிர வேண்டாம். "
          "அதிகாரப்பூர்வ எச்சரிக்கைகளை பார்க்கவும்.",
    "te": "OceanPing: {location} వద్ద {label} నిర్ధారించలేదు. షేర్ చేయవద్దు. అధికారిక హెచ్చరికలు చూడండి.",
}
assert set(_CORRECTION_STANDARD) == set(_CORRECTION_SHORT) == set(_REASON_REJECTED) == set(_REASON_INSTRUMENT_FLAT)


def compose_correction(
    lang: str, hazard_label: str, location: str, instrument_flat: bool, rejected_count: int
) -> dict:
    """One language's {"standard", "short"} correction-message pair. Never
    invents anything beyond the facts passed in — same discipline as
    sitrep/engine.py's snapshot-only composition."""
    reason = _REASON_REJECTED[lang] if rejected_count > 0 else _REASON_INSTRUMENT_FLAT[lang]
    return {
        "standard": _CORRECTION_STANDARD[lang].format(label=hazard_label, location=location, reason=reason),
        "short": _CORRECTION_SHORT[lang].format(label=hazard_label, location=location),
    }


def compose_correction_all_langs(
    hazard_labels: dict[str, str], location: str, instrument_flat: bool, rejected_count: int
) -> dict:
    """{lang: {"standard", "short"}} for every language in `hazard_labels` —
    the full draft-message shape (see alerts/engine.py::draft_message, which
    this mirrors so both message families share one delivery-side resolver,
    `alerts/engine.py::message_text`)."""
    return {
        lang: compose_correction(lang, label, location, instrument_flat, rejected_count)
        for lang, label in hazard_labels.items()
    }
