# OceanPing — Phase Plans

Implementation plans for the full OceanPing blueprint, split by phase. **Start here.**

These are **gap plans**: each file states what already exists in the codebase (with file
paths), what the phase adds, and how to build it on the existing module seams. They are
written so a fresh working session (human or agent) can pick up any phase without
re-deriving context.

## Phase table

| Phase | File | Blueprint scope | Status |
|---|---|---|---|
| 0 | [phase-0-mvp-kernel.md](phase-0-mvp-kernel.md) | MVP kernel: reports → NLP → confidence scoring → hotspot map, ERDDAP corroboration | ✅ **Built** (as-built record + hardening list) |
| 1 | [phase-1-intelligence-alerting.md](phase-1-intelligence-alerting.md) | Weeks 7–14: tiered alerting, delivery channels, NLP upgrades, active learning | 🟡 Milestones 1–4 built (alerts engine + composer + Telegram; delivery worker + web push + SMS stub; Whisper voice notes; training export + retrain script + linear-probe classifier swap) |
| 2 | [phase-2-fusion-reach.md](phase-2-fusion-reach.md) | Weeks 15–24: satellite fusion, WhatsApp/IVR, fisherman mode, RAG chatbot, evacuation routing | 🔲 Planned |
| 3 | [phase-3-depth.md](phase-3-depth.md) | Months 7–10: inundation + digital twin, SITREPs, rumor tracker, post-disaster mode, mobile app, IoT — and the microservice split | 🔲 Planned |
| 4 | [phase-4-scale.md](phase-4-scale.md) | Months 11+: federated learning, AR, open data, parametric insurance, CAP + multi-state | 🔲 Planned |

## Dependencies

```
phase-0 (done)
  └─► phase-1  (alerting needs scoring/incidents; active learning needs verifications)
        └─► phase-2  (channels reuse delivery worker; satellite extends scoring components)
              └─► phase-3  (SITREPs/rumor tracker need alerts + forecasts; app needs routing)
                    └─► phase-4  (scale-out of everything below it)
```

Items can be cherry-picked out of order when they don't touch shared seams — each plan
marks its **independent items** explicitly.

## Conventions

- Every phase file follows the same skeleton: *Goals → Already built → Gap work breakdown
  → Data model changes → New infra → External dependencies & risks → Milestones →
  Verification*.
- Plans reference live code paths (e.g. `backend/app/modules/scoring/engine.py`). If a
  referenced seam has moved, trust the code, then fix the plan.
- **When a phase (or item) lands, update its status here and edit the phase file** — these
  docs are the project's memory, not write-once artifacts.
- Architectural rule inherited from the MVP: module boundaries inside the monolith mirror
  the future service split. New capabilities go in `backend/app/modules/<name>/` with
  router/service/engine separation; nothing reaches into another module's tables directly.
- Product rule that never bends: **no citizen-only escalation** — report volume alone never
  raises alert tier or verification status; instruments or a human must agree, and every
  decision lands in the hash-chained audit log.
