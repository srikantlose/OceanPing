"""Static hazard-safety FAQ corpus, seeded into rag_documents at startup
(mirrors sensors/service.py::sync_stations()'s upsert-by-id pattern). This is
educational/reference content only - general safety information, never
real-time evacuation directives. That distinction matters: chat/service.py
hard-bypasses the LLM entirely for evacuation-directive questions, and this
corpus is written so it would never need to make that call anyway.

Real INCOIS advisories / PFZ bulletins / a shelter list were in the original
plan as corpus sources too, but scraping a live advisory feed or building the
shelters table (phase-2 milestone 6) is out of scope here - this ships the
retrieval + generation + safety-gate seam with a real, useful, honest corpus
rather than faking external sources this session has no way to verify.
"""

HAZARD_FAQ: list[dict] = [
    {
        "id": "faq-hazard-types",
        "title": "What hazard types does OceanPing track?",
        "content": (
            "OceanPing tracks eight coastal hazard types: coastal flooding, storm surge, "
            "high waves, tsunami warning signs, rip currents, oil spills, algal blooms, "
            "and coastal erosion. Each report is classified into one of these types from "
            "the reporter's description, and citizens can also pick a type directly when "
            "submitting a report."
        ),
    },
    {
        "id": "faq-alert-tiers",
        "title": "What do advisory, watch, and warning mean?",
        "content": (
            "OceanPing uses three alert tiers. Advisory is the lowest tier, issued "
            "automatically when a hazard is reported and corroborated. Watch is issued "
            "automatically when an instrument or satellite observation agrees with "
            "multiple independent citizen reports. Warning is the highest tier and is "
            "only ever issued by a human analyst who has reviewed the evidence - it is "
            "never issued automatically, no matter how many reports come in."
        ),
    },
    {
        "id": "faq-report-status",
        "title": "What do the report statuses (unverified, corroborated, verified) mean?",
        "content": (
            "A report starts as 'unverified'. It becomes 'corroborated' automatically "
            "only when an instrument reading (like a tide gauge) or a satellite "
            "observation agrees with the report - report volume from citizens alone can "
            "never cause this escalation. It becomes 'verified' only when a human analyst "
            "reviews it and confirms it. 'Rejected' means an analyst reviewed it and did "
            "not confirm it."
        ),
    },
    {
        "id": "faq-tsunami-signs",
        "title": "What are natural warning signs of a tsunami?",
        "content": (
            "Natural warning signs include: strong or long earthquake shaking near the "
            "coast, the sea suddenly and unusually receding far from the shoreline and "
            "exposing the sea floor, a loud roaring sound from the ocean, and unusual "
            "rapid rises or falls in coastal water level. Coastal safety guidance is to "
            "move to higher ground immediately if you observe these signs directly, "
            "rather than waiting for an official alert."
        ),
    },
    {
        "id": "faq-rip-current-safety",
        "title": "What should I know about rip currents?",
        "content": (
            "A rip current is a narrow, fast-moving channel of water flowing away from "
            "the shore. General coastal-safety guidance for someone caught in one is to "
            "stay calm, avoid swimming directly against the current, and instead swim "
            "parallel to the shore until free of the current before swimming back at an "
            "angle, signaling for help if unable to escape."
        ),
    },
    {
        "id": "faq-oil-spill-report",
        "title": "What should I do if I see an oil spill?",
        "content": (
            "Report the location, extent, and any visible source of the spill through "
            "OceanPing or to local coastal authorities. Avoid direct contact with spilled "
            "oil and avoid consuming seafood from the affected area until authorities "
            "confirm it is safe. OceanPing corroborates oil spill reports using satellite "
            "imagery, since tide gauges and other instruments can't detect oil on the "
            "water."
        ),
    },
    {
        "id": "faq-algal-bloom",
        "title": "What is a harmful algal bloom and why does water color change?",
        "content": (
            "A harmful algal bloom is a rapid increase in algae that can discolor water "
            "(often green, red, or brown) and is sometimes associated with fish kills or "
            "unusual smells; some blooms produce toxins. If you notice discolored water, "
            "dead fish, or unusual odors along the coast, avoid swimming in or eating "
            "seafood from the affected area and report it."
        ),
    },
    {
        "id": "faq-how-reporting-works",
        "title": "How does reporting a hazard on OceanPing work?",
        "content": (
            "You can submit a report through the web form or the Telegram bot, describing "
            "what you're seeing along with your location and optionally a photo or voice "
            "note. The system automatically classifies the hazard type and language, "
            "checks for independent nearby reports and instrument or satellite data, and "
            "calculates a confidence score. A human analyst reviews reports before they "
            "can be marked verified."
        ),
    },
    {
        "id": "faq-trust-score",
        "title": "What is a reporter's trust score?",
        "content": (
            "Each reporter has a trust score that increases slightly each time an analyst "
            "verifies one of their reports, and decreases when an analyst rejects one. "
            "This lets the system weigh reports from an accurate track record more "
            "heavily, without ever letting trust alone unlock verified or warning status "
            "- a human analyst decision is always required for those."
        ),
    },
    {
        "id": "faq-coastal-flooding-general",
        "title": "General information about coastal flooding",
        "content": (
            "Coastal flooding happens when sea water moves inland beyond its normal "
            "extent, often driven by storm surge, high tides, or heavy wave action. "
            "Low-lying coastal roads and ground-floor buildings near the shoreline are "
            "typically affected first. Official advisories and local-authority guidance "
            "should be your primary source for real-time safety decisions."
        ),
    },
    {
        "id": "faq-erosion",
        "title": "What is coastal erosion?",
        "content": (
            "Coastal erosion is the gradual (or sometimes sudden, during storms) loss of "
            "land or sand along a shoreline as waves and currents wear it away. "
            "Structures built close to an eroding shoreline can become unstable over "
            "time. Reporting visible erosion helps track changing coastlines."
        ),
    },
    {
        "id": "faq-helpline",
        "title": "Who do I contact in an emergency?",
        "content": (
            "OceanPing is a reporting and information platform, not an emergency "
            "response service. In an emergency in India, dial 112 (the national "
            "emergency number) or contact the NDMA control room at 1078. Always follow "
            "guidance from local authorities and official emergency services first."
        ),
    },
]


def seed_corpus(db) -> int:
    """Idempotent upsert-by-id, embedding each document's content with the
    same multilingual sentence-transformer classify() uses. If the embedding
    model isn't available, documents are still stored (title/content), just
    without a vector - retrieve() skips rows with no embedding, so chat
    degrades to the helpline fallback rather than erroring."""
    from app.models import RagDocument
    from app.modules.nlp import classifier as nlp_classifier

    embedder = nlp_classifier._load_model()
    updated = 0
    for entry in HAZARD_FAQ:
        doc = db.get(RagDocument, entry["id"])
        if doc is None:
            doc = RagDocument(id=entry["id"])
            db.add(doc)
        doc.title = entry["title"]
        doc.content = entry["content"]
        doc.lang = entry.get("lang", "en")
        doc.source = "hazard_faq"
        if embedder is not None:
            doc.embedding = embedder.encode([entry["content"]], normalize_embeddings=True)[0].tolist()
        updated += 1
    db.commit()
    return updated
