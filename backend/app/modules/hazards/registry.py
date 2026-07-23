"""Per-hazard-type behavior as data, not code (phase 4, milestone 2).

Before this module, "what does hazard X mean" was answered by nine separate
hand-maintained Python dicts scattered across scoring, satellite, alerts,
the chat conversation core, CAP ingestion, and the RAG corpus — each keyed by
the same hazard strings, each requiring its own edit to add a hazard, and
several already quietly out of sync with each other (the frontend's short
"Erosion" legend label vs. the alert engine's "Coastal erosion", for one).

Now there is exactly one place: one YAML file per hazard under
`definitions/`. Everything else — `models.py::HAZARD_TYPES`,
`scoring/engine.py::HAZARD_VARIABLES`, `satellite/providers.py::HAZARD_RECIPES`,
`alerts/engine.py`'s label tables, `ingest/report_conversation.py`'s menu/
speech tables, `alerts/cap_ingest.py::EVENT_HAZARD_KEYWORDS`, and
`chat/corpus.py`'s per-hazard FAQ entries — is derived from this registry at
import time. Adding "king tide" is adding `definitions/king_tide.yaml`;
nothing else in the tree changes.

A hazard file only strictly needs `key`, `order`, and at least an English
`menu_label` to be usable end to end. Missing Tamil/Telugu falls back to
English (the same "English is the safe fallback for an unrecognized or
unconfigured language" posture `report_conversation.py` already documented
before this refactor — just extended from "the translation may be a rough
first pass" to "the translation may not exist yet"). Missing
`satellite_recipe` / `cap_event_keywords` / `faq` simply means that hazard
contributes nothing to those tables — the same "absence, not a guess" stance
`oil_spill`'s always-empty `instrument_variables` already modeled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
SUPPORTED_LANGS = ("en", "ta", "te")

_FALLBACK_COLOR = "#898781"


@dataclass
class HazardDef:
    key: str
    order: int
    menu_label: dict[str, str]
    speech_label: dict[str, str]
    alert_label_en: str
    instrument_variables: set[str] = field(default_factory=set)
    satellite_recipe: str | None = None
    cap_event_keywords: list[str] = field(default_factory=list)
    faq: list[dict] = field(default_factory=list)

    def label(self, lang: str) -> str:
        return self.menu_label.get(lang) or self.menu_label["en"]

    def speech(self, lang: str) -> str:
        return self.speech_label.get(lang) or self.speech_label["en"]

    def alert(self, lang: str) -> str:
        """English gets its own curated alert-body phrasing; other languages
        reuse the speech label, exactly as alerts/engine.py did before this
        refactor (it never maintained a separate ta/te alert label)."""
        if lang == "en":
            return self.alert_label_en
        return self.speech_label.get(lang) or self.alert_label_en


def _fallback_label(key: str) -> str:
    return key.replace("_", " ").title()


def _parse_hazard(raw: dict, source: Path) -> HazardDef:
    key = raw["key"]
    menu_label = {str(k): v for k, v in (raw.get("menu_label") or {}).items()}
    speech_label = {str(k): v for k, v in (raw.get("speech_label") or {}).items()}
    fallback = menu_label.get("en") or _fallback_label(key)
    menu_label.setdefault("en", fallback)
    speech_label.setdefault("en", speech_label.get("en") or fallback)
    for lang in SUPPORTED_LANGS:
        if lang == "en":
            continue
        if lang not in menu_label or lang not in speech_label:
            log.warning(
                "hazard %r has no %s translation in %s — falling back to English",
                key, lang, source.name,
            )
    return HazardDef(
        key=key,
        order=int(raw.get("order", 0)),
        menu_label=menu_label,
        speech_label=speech_label,
        alert_label_en=raw.get("alert_label_en") or menu_label["en"],
        instrument_variables=set(raw.get("instrument_variables") or []),
        satellite_recipe=raw.get("satellite_recipe"),
        cap_event_keywords=list(raw.get("cap_event_keywords") or []),
        faq=list(raw.get("faq") or []),
    )


def load_registry(directory: Path) -> dict[str, HazardDef]:
    """Pure loader — reads every `*.yaml` file in `directory` and returns
    hazards keyed by hazard_type, ordered by each file's `order` field. Takes
    an explicit directory (rather than always reading `DEFINITIONS_DIR`) so
    tests can point it at a fixture directory without touching the real,
    shipped registry — see tests/test_hazard_registry.py."""
    hazards: dict[str, HazardDef] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "key" not in raw:
            raise ValueError(f"{path}: hazard definition is missing required field 'key'")
        hz = _parse_hazard(raw, path)
        if hz.key in hazards:
            raise ValueError(f"duplicate hazard key {hz.key!r} (from {path.name})")
        hazards[hz.key] = hz
    return dict(sorted(hazards.items(), key=lambda kv: kv[1].order))


HAZARDS: dict[str, HazardDef] = load_registry(DEFINITIONS_DIR)
HAZARD_TYPES: list[str] = list(HAZARDS.keys())


def menu_label(hazard_type: str, lang: str = "en") -> str:
    hz = HAZARDS.get(hazard_type)
    return hz.label(lang) if hz is not None else _fallback_label(hazard_type)


def speech_label(hazard_type: str, lang: str = "en") -> str:
    hz = HAZARDS.get(hazard_type)
    return hz.speech(lang) if hz is not None else _fallback_label(hazard_type)


def alert_label(hazard_type: str, lang: str = "en") -> str:
    hz = HAZARDS.get(hazard_type)
    return hz.alert(lang) if hz is not None else _fallback_label(hazard_type)


def menu_labels_by_lang(lang: str = "en") -> dict[str, str]:
    return {key: hz.label(lang) for key, hz in HAZARDS.items()}


def speech_labels_by_lang(lang: str = "en") -> dict[str, str]:
    return {key: hz.speech(lang) for key, hz in HAZARDS.items()}


def alert_labels_by_lang(lang: str = "en") -> dict[str, str]:
    return {key: hz.alert(lang) for key, hz in HAZARDS.items()}


def instrument_variables_table() -> dict[str, set[str]]:
    return {key: set(hz.instrument_variables) for key, hz in HAZARDS.items()}


def satellite_recipes_table() -> dict[str, str]:
    return {key: hz.satellite_recipe for key, hz in HAZARDS.items() if hz.satellite_recipe}


def cap_event_keywords_table() -> list[tuple[str, str]]:
    """`[(keyword, hazard_type), ...]` in registry order, first-match-wins —
    same shape and matching semantics `alerts/cap_ingest.py::map_event_to_hazard`
    always used, just sourced from the registry instead of a hardcoded list."""
    pairs: list[tuple[str, str]] = []
    for hz in HAZARDS.values():
        for keyword in hz.cap_event_keywords:
            pairs.append((keyword, hz.key))
    return pairs


def faq_entries() -> list[dict]:
    entries: list[dict] = []
    for hz in HAZARDS.values():
        entries.extend(hz.faq)
    return entries
