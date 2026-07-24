# OceanPing Open Data — Dataset Datasheet

Follows the "Datasheets for Datasets" convention (Gebru et al., 2018), adapted to a
dataset whose release process is a live pipeline (`backend/app/modules/opendata/`)
rather than a one-time export. Update this file if the aggregation parameters, access
process, or retention policy described below ever change in code — this document
should always describe what `POST /analyst/opendata/releases` actually produces.

## Motivation

**For what purpose was the dataset created?** OceanPing fuses citizen reports,
instrument (tide-gauge/IoT) anomalies, satellite corroboration, and official-agency
advisories into a single confidence-scored coastal-hazard event stream (see
`docs/plans/phase-0-mvp-kernel.md` and `phase-2-fusion-reach.md`). That fused,
human-verified event history has no public equivalent for India's coastline and is
academically useful on its own — for hazard-frequency research, model benchmarking,
and coastal-risk studies — independent of OceanPing's own operational use.

**Who created it?** The OceanPing pilot team, from reports submitted by citizens and
corroborated by INCOIS-style instrument data, satellite scenes, and official advisories,
then verified by a human analyst (see `phase-0-mvp-kernel.md`'s escalation gate: no
report is ever verified from citizen volume alone).

## Composition

**What do the instances represent?** Each released row is one (H3 cell, hazard type,
calendar day) group: a coarsened location, a hazard type, a date, and a
count of verified reports. There is no free text, no photo, no reporter identity, and
no exact coordinate anywhere in a release — see "Anonymization" below.

**How many instances are there?** Varies per release; see each release's own
`row_count`/`suppressed_group_count` (`GET /opendata/datasets`).

**Does the dataset contain all possible instances, or a sample?** All groups meeting
the k-anonymity floor within the requested period — not a random sample. A group with
fewer than `open_data_k_anonymity_min` (default 5) raw reports is dropped, not sampled
down, so absence of a row does *not* mean zero reports occurred there; it means too few
occurred to publish safely.

**Is there a label or target?** No — this is a descriptive count dataset, not a
labeled ML training set (OceanPing's own hazard classifier training data is a separate,
internal artifact — see `phase-1-intelligence-alerting.md`'s active-learning loop —
and is not part of this release).

## Anonymization (the load-bearing section)

Two independent privacy mechanisms apply, in this order:

1. **Spatial + temporal aggregation.** Reports are grouped by H3 cell at
   `open_data_h3_resolution` (default 6, roughly 36 km² hexagons — coarser than the
   ~0.7 km² resolution-8 cell OceanPing uses internally for scoring) and by calendar
   day, never by individual report or reporter.
2. **k-anonymity floor.** A group's *true* report count must reach
   `open_data_k_anonymity_min` (default 5) before it is even considered for release.
   This is enforced before noise is added — Laplace noise cannot retroactively hide
   that a raw count of 1–4 was ever computed, so under-floor groups are suppressed
   outright, not noised down.
3. **Differential-privacy noise.** Every surviving group's count then gets independent
   Laplace-mechanism noise (`scale = 1/dp_epsilon`, default `dp_epsilon=1.0`), rounded
   to the nearest non-negative integer. Two releases built from the same underlying
   data will not produce byte-identical counts.

No release ever contains: reporter identity, verbatim report text, media, exact
coordinates, or anything at finer granularity than one calendar day and one
`open_data_h3_resolution` cell.

**Retention of the underlying source data** (not the release itself): a `Report` row's
exact `lat`/`lon`/`geom` are permanently overwritten with its H3 cell's centroid once
the report is older than `open_data_retention_months` (default 12) — see
`modules/opendata/service.py::anonymize_expired_reports`, a real scheduled job (see
`core/scheduler.py`), not just a written-down policy. This never deletes the report —
hazard type, confidence, and incident linkage are untouched — it only reduces stored
location precision, one-way, after the fact.

## Collection process

Reports originate from Telegram, WhatsApp, IVR, the mobile app, and the web form (see
`phase-2-fusion-reach.md`), pass through NLP classification and multi-signal confidence
scoring (`scoring/engine.py`), and only reach `status="verified"` — the only status this
pipeline ever aggregates from — after an analyst decision or an instrument/satellite/
official-advisory corroboration crossing the escalation gate (`corroborated_threshold`).
No dataset release ever includes an `unverified`, `corroborated`-only, or `rejected`
report.

## Uses

**What other tasks could the dataset be used for?** Hazard-frequency and seasonality
analysis, coastal-risk modeling, cross-validation against independent tide-gauge or
satellite datasets, disaster-preparedness research.

**Are there tasks for which the dataset should not be used?** Individual-level
inference of any kind — the k-anonymity floor and DP noise are specifically designed
to make this hard, but a downstream user combining many small releases across
overlapping periods could, in principle, average out some of the DP noise. Researchers
requiring individual-report-level access for a specific, reviewed research purpose
should contact the pilot team directly rather than working around this API.

## Distribution

**How is the dataset distributed?** `GET /opendata/datasets` lists every release
(public, no key required — a researcher needs to see what exists before requesting
access). `GET /opendata/datasets/{id}` returns the actual rows, gated by a per-consumer
API key (`POST /analyst/opendata/api-keys`, analyst-issued) and rate-limited
(`open_data_rate_limit_per_hour`, default 200/hour per key).

**Will the dataset have a DOI?** `DatasetRelease.doi` is reserved for this but stays
null until an operator registers a specific release with an external DOI provider
(e.g. DataCite or Zenodo) and fills it in by hand — that registration step happens
outside this app, the same "pilot placeholder swappable the day a real integration
lands" posture as `cap_sender` in the CAP milestone.

**Licensing.** Not yet decided at the pilot stage — record the chosen license here
once one is picked, alongside the first real external release.

## Maintenance

Releases are point-in-time snapshots, immutable once created (`DatasetRelease` rows are
never edited after insert — only the underlying source `Report` data changes going
forward, via ordinary pipeline operation and the retention job above). A researcher
citing a release's `checksum` can always verify their copy matches exactly what was
published. New releases are triggered by an analyst via
`POST /analyst/opendata/releases` for an arbitrary period — there is currently no
automatic release cadence; this is a deliberate choice, since publishing implies a
review step (a human should have looked at `suppressed_group_count`/`row_count` before
anything goes out), not a fully automatic schedule.
