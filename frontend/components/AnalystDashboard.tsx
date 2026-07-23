"use client";

import { useState } from "react";
import useSWR from "swr";
import { getJSON, postFormAuth, postJSON } from "@/lib/api";
import {
  ALERT_TIER_COLORS,
  ALERT_TIER_LABELS,
  HAZARD_COLORS,
  HAZARD_LABELS,
  SEVERITY_COLORS,
  SEVERITY_LABELS,
  STATUS_COLORS,
  STATUS_LABELS,
} from "@/lib/palette";

const COMPONENT_LABELS: Record<string, string> = {
  trust: "Reporter trust",
  coherence: "Coherence",
  instrument: "Instruments",
  media: "Media",
  satellite: "Satellite",
  account_device: "Account/device",
  official: "Official advisory",
};

function StatusChip({ status }: { status: string }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: STATUS_COLORS[status] || "#898781" }} />
      {STATUS_LABELS[status] || status}
    </span>
  );
}

function HazardChip({ hazard }: { hazard: string }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: HAZARD_COLORS[hazard] || "#898781" }} />
      {HAZARD_LABELS[hazard] || hazard}
    </span>
  );
}

function TierChip({ tier }: { tier: string }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: ALERT_TIER_COLORS[tier] || "#898781" }} />
      {ALERT_TIER_LABELS[tier] || tier}
    </span>
  );
}

function SeverityChip({ severity }: { severity: string }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: SEVERITY_COLORS[severity] || "#898781" }} />
      {SEVERITY_LABELS[severity] || severity}
    </span>
  );
}

function ConfidenceBars({ report }: { report: any }) {
  const comp = report.confidence_components || {};
  return (
    <div className="conf-bars">
      <div className="conf-total">
        <span className="value">{Math.round(report.confidence * 100)}%</span>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>overall confidence</span>
      </div>
      {Object.entries(COMPONENT_LABELS).map(([key, label]) => {
        const v = typeof comp[key] === "number" ? comp[key] : 0;
        return (
          <div className="bar-row" key={key}>
            <span className="bar-label">{label}</span>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${Math.round(v * 100)}%` }} />
            </div>
            <span className="bar-value">{v.toFixed(2)}</span>
          </div>
        );
      })}
      {comp.detail?.hearsay && (
        <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 6 }}>
          🗣 reads as a secondhand account (hearsay) — coherence contribution halved
        </div>
      )}
      {comp.detail?.satellite_observations?.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 6 }}>
          🛰 {comp.detail.satellite_observations.length} satellite observation(s):{" "}
          {comp.detail.satellite_observations
            .map((o: any) => `${o.provider}/${o.recipe} score=${o.score}`)
            .join("; ")}
        </div>
      )}
      {comp.detail?.corroborating_anomalies?.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 6 }}>
          ⚠ {comp.detail.corroborating_anomalies.length} corroborating instrument
          anomaly(ies): {comp.detail.corroborating_anomalies
            .map((a: any) => `${a.station_id} ${a.variable} z=${a.zscore}`)
            .join("; ")}
        </div>
      )}
      {comp.detail?.official_advisory && (
        <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 6 }}>
          📋 official advisory active over this location: {comp.detail.official_advisory.event}
          {" "}({comp.detail.official_advisory.sender}, certainty {comp.detail.official_advisory.certainty})
        </div>
      )}
      {comp.detail && (
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
          {comp.detail.n_independent_reports} independent nearby report(s) in ±30 min
        </div>
      )}
    </div>
  );
}

function Login({ onToken }: { onToken: (t: string) => void }) {
  const [username, setUsername] = useState("analyst");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const { token } = await postJSON("/auth/login", { username, password });
      localStorage.setItem("oceanping-analyst-token", token);
      onToken(token);
    } catch {
      setError("Invalid credentials");
    }
  }

  return (
    <form className="card" style={{ maxWidth: 360, margin: "60px auto" }} onSubmit={submit}>
      <h3>Analyst sign-in</h3>
      <label className="field">
        <span>Username</span>
        <input value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: "100%" }} />
      </label>
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ width: "100%" }}
        />
      </label>
      <button className="primary" style={{ width: "100%" }}>Sign in</button>
      {error && <div className="notice err">{error}</div>}
    </form>
  );
}

function Sitreps({ token }: { token: string }) {
  const fetcher = (path: string) => getJSON(path, token);
  const { data: sitreps, mutate } = useSWR("/analyst/sitreps?limit=20", fetcher, {
    refreshInterval: 30_000,
  });
  const [expandedId, setExpandedId] = useState<string | null>(null);

  async function generateNow() {
    await postJSON("/analyst/sitreps/generate", {}, token);
    mutate();
  }

  async function file(id: string) {
    await postJSON(`/analyst/sitreps/${id}/file`, {}, token);
    mutate();
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>SITREPs</h3>
        <button onClick={generateNow}>Generate now</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
        {(sitreps || []).map((s: any) => (
          <div key={s.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setExpandedId(expandedId === s.id ? null : s.id)}
            >
              <span>
                <strong>{new Date(s.period_start).toLocaleString()}</strong>
                {" → "}
                {new Date(s.period_end).toLocaleTimeString()}{" "}
                <span className="chip">{s.status}</span>
              </span>
              {s.status === "draft" && (
                <button
                  className="good"
                  onClick={(e) => {
                    e.stopPropagation();
                    file(s.id);
                  }}
                >
                  File
                </button>
              )}
            </div>
            <p style={{ fontSize: 13, marginTop: 6 }}>{s.content.summary}</p>
            {expandedId === s.id && (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: 12,
                  background: "var(--surface-2)",
                  padding: 8,
                  borderRadius: 6,
                  marginTop: 6,
                }}
              >
                {JSON.stringify(s.content.sections, null, 2)}
              </pre>
            )}
          </div>
        ))}
        {(sitreps || []).length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            No SITREPs yet — wait for the hourly job or click "Generate now".
          </p>
        )}
      </div>
    </div>
  );
}

function Forecasts({ token }: { token: string }) {
  const fetcher = (path: string) => getJSON(path, token);
  const { data: forecasts, mutate } = useSWR("/analyst/forecasts?limit=20", fetcher, {
    refreshInterval: 30_000,
  });
  const { data: accuracy } = useSWR("/forecasts/accuracy", fetcher, { refreshInterval: 60_000 });

  async function generateNow() {
    await postJSON("/analyst/forecasts/generate", {}, token);
    mutate();
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Forecasts</h3>
        <button onClick={generateNow}>Generate now</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
        {(forecasts || []).map((f: any) => (
          <div key={f.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>
                <span className="chip">{f.kind}</span>{" "}
                <strong>{f.subject_type === "station" ? f.content.variable : f.hazard_type}</strong>
                {" · "}
                {new Date(f.generated_at).toLocaleString()}
              </span>
              {f.validated_at ? (
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {f.kind === "sensor"
                    ? `MAE ${f.validation?.mean_abs_error ?? "—"}`
                    : `hit rate ${f.validation?.hit_rate ?? "—"}`}
                </span>
              ) : (
                <span style={{ fontSize: 12, color: "var(--muted)" }}>not yet validated</span>
              )}
            </div>
            {f.kind === "propagation" && (
              <p style={{ fontSize: 13, marginTop: 6 }}>
                {f.content.location} · front speed {f.content.front?.speed_kmh} km/h, bearing {f.content.front?.bearing_deg}°
                {" — "}
                {Object.values(f.content.projected || {}).reduce((n: number, c: any) => n + c.length, 0)} projected cell(s)
              </p>
            )}
          </div>
        ))}
        {(forecasts || []).length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            No forecasts yet — wait for the scheduled job or click "Generate now".
          </p>
        )}
      </div>
      {accuracy && (accuracy.sensor.length > 0 || accuracy.propagation.length > 0) && (
        <div style={{ marginTop: 14 }}>
          <h4 style={{ margin: "0 0 6px" }}>How right were we?</h4>
          {accuracy.sensor.map((s: any, i: number) => (
            <div key={i} style={{ fontSize: 12, color: "var(--muted)" }}>
              {s.location} · {s.variable}: mean error {s.mean_abs_error} over {s.n_forecasts} forecast(s)
            </div>
          ))}
          {accuracy.propagation.map((p: any, i: number) => (
            <div key={i} style={{ fontSize: 12, color: "var(--muted)" }}>
              {p.location} · {p.hazard_type}: hit rate {p.mean_hit_rate} over {p.n_forecasts} forecast(s)
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Narratives({ token }: { token: string }) {
  const fetcher = (path: string) => getJSON(path, token);
  const { data: narratives, mutate } = useSWR("/analyst/narratives?limit=50", fetcher, {
    refreshInterval: 30_000,
  });

  async function detectNow() {
    await postJSON("/analyst/narratives/detect", {}, token);
    mutate();
  }

  async function approve(id: string) {
    await postJSON(`/analyst/narratives/${id}/approve`, {}, token);
    mutate();
  }

  async function dismiss(id: string) {
    await postJSON(`/analyst/narratives/${id}/dismiss`, {}, token);
    mutate();
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Rumor tracker</h3>
        <button onClick={detectNow}>Check now</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
        {(narratives || []).map((n: any) => (
          <div key={n.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>
                <HazardChip hazard={n.hazard_type} /> {n.report_count} report(s){" "}
                <span className="chip">{n.status}</span>{" "}
                <span style={{ fontSize: 11, color: "var(--muted)" }}>({n.draft_method})</span>
              </span>
              {n.status === "draft" && (
                <span style={{ display: "flex", gap: 6 }}>
                  <button className="good" onClick={() => approve(n.id)}>Approve &amp; send</button>
                  <button onClick={() => dismiss(n.id)}>Dismiss</button>
                </span>
              )}
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>"{n.representative_text}"</p>
            <p style={{ fontSize: 12, marginTop: 4 }}>
              {n.instrument_flat && "No corroborating instrument signal nearby. "}
              {n.rejected_report_count > 0 &&
                `${n.rejected_report_count} member report(s) already rejected by an analyst. `}
            </p>
            <p style={{ fontSize: 13, marginTop: 6 }}>{n.message?.en?.standard}</p>
            {n.reviewed_by && (
              <p style={{ fontSize: 11, color: "var(--muted)" }}>
                reviewed by {n.reviewed_by}
                {n.reviewed_at ? ` at ${new Date(n.reviewed_at).toLocaleString()}` : ""}
              </p>
            )}
          </div>
        ))}
        {(narratives || []).length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            No rumor narratives flagged — wait for the scheduled check or click "Check now".
          </p>
        )}
      </div>
    </div>
  );
}

function Recovery({ token }: { token: string }) {
  const fetcher = (path: string) => getJSON(path, token);
  const { data: damage, mutate: refreshDamage } = useSWR("/analyst/recovery/damage?hours=72", fetcher, {
    refreshInterval: 30_000,
  });
  const { data: reliefRequests, mutate: refreshRelief } = useSWR(
    "/analyst/recovery/relief-requests", fetcher, { refreshInterval: 30_000 }
  );
  const { data: aidOffers, mutate: refreshOffers } = useSWR(
    "/analyst/recovery/aid-offers", fetcher, { refreshInterval: 30_000 }
  );
  const { data: matches, mutate: refreshMatches } = useSWR(
    "/analyst/recovery/aid-matches", fetcher, { refreshInterval: 30_000 }
  );
  const { data: missing, mutate: refreshMissing } = useSWR(
    "/analyst/recovery/missing", fetcher, { refreshInterval: 30_000 }
  );
  const [candidatesFor, setCandidatesFor] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<any[]>([]);

  const refreshAid = () => {
    refreshRelief();
    refreshOffers();
    refreshMatches();
  };

  async function reviewDamage(id: string) {
    await postJSON(`/analyst/recovery/damage/${id}/review`, {}, token);
    refreshDamage();
  }

  async function fulfillRequest(id: string) {
    const fulfilledBy = window.prompt("Fulfilled by (org/volunteer name, optional):") || "";
    await postFormAuth(`/analyst/recovery/relief-requests/${id}/fulfill`,
      fulfilledBy ? { fulfilled_by: fulfilledBy } : {}, token);
    refreshAid();
  }

  async function closeOffer(id: string) {
    await postJSON(`/analyst/recovery/aid-offers/${id}/close`, {}, token);
    refreshAid();
  }

  async function loadCandidates(id: string) {
    if (candidatesFor === id) {
      setCandidatesFor(null);
      return;
    }
    const rows = await getJSON(`/analyst/recovery/missing/${id}/matches`, token);
    setCandidatesFor(id);
    setCandidates(rows);
  }

  async function resolveMissing(id: string, matchedId?: string) {
    await postFormAuth(`/analyst/recovery/missing/${id}/resolve`,
      matchedId ? { matched_person_id: matchedId } : {}, token);
    setCandidatesFor(null);
    refreshMissing();
  }

  const requestById: Record<string, any> = {};
  for (const r of reliefRequests || []) requestById[r.id] = r;
  const offerById: Record<string, any> = {};
  for (const o of aidOffers || []) offerById[o.id] = o;

  return (
    <div className="card">
      <h3 style={{ margin: 0 }}>Recovery</h3>

      <div style={{ marginTop: 10 }}>
        <h4 style={{ margin: "0 0 6px" }}>Damage assessments (last 72h)</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(damage || []).map((d: any) => (
            <div key={d.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8,
                                     display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>
                <SeverityChip severity={d.severity} /> {d.damage_class.replace(/_/g, " ")}{" "}
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  ({d.cv_mode}, {Math.round(d.cv_confidence * 100)}% confidence)
                </span>
              </span>
              {d.status === "submitted" ? (
                <button onClick={() => reviewDamage(d.id)}>Mark reviewed</button>
              ) : (
                <span className="chip">reviewed</span>
              )}
            </div>
          ))}
          {(damage || []).length === 0 && <p style={{ color: "var(--muted)" }}>No damage assessments yet.</p>}
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <h4 style={{ margin: "0 0 6px" }}>Mutual-aid board</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(matches || []).map((m: any, i: number) => {
            const req = requestById[m.request_id];
            const offer = offerById[m.offer_id];
            if (!req || !offer) return null;
            return (
              <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span>
                    <span className="chip">{m.category}</span> request for {req.people_count ?? "?"} people
                    {" "}↔ offer capacity {offer.capacity ?? "?"} · {m.distance_km} km apart
                  </span>
                  <span style={{ display: "flex", gap: 6 }}>
                    <button className="good" onClick={() => fulfillRequest(req.id)}>Fulfill request</button>
                    <button onClick={() => closeOffer(offer.id)}>Close offer</button>
                  </span>
                </div>
                {req.description && <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>"{req.description}"</p>}
              </div>
            );
          })}
          {(matches || []).length === 0 && (
            <p style={{ color: "var(--muted)" }}>
              No matches — {(reliefRequests || []).length} open request(s), {(aidOffers || []).length} open offer(s).
            </p>
          )}
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <h4 style={{ margin: "0 0 6px" }}>Missing / found persons</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(missing || []).map((p: any) => (
            <div key={p.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>
                  <span className="chip">{p.report_type}</span> {p.name}
                  {p.age != null && ` (${p.age})`}
                </span>
                <span style={{ display: "flex", gap: 6 }}>
                  <button onClick={() => loadCandidates(p.id)}>
                    {candidatesFor === p.id ? "Hide matches" : "Find matches"}
                  </button>
                  <button onClick={() => resolveMissing(p.id)}>Resolve (no match)</button>
                </span>
              </div>
              {p.description && <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>{p.description}</p>}
              {candidatesFor === p.id && (
                <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                  {candidates.map((c) => (
                    <div key={c.candidate_id} style={{ display: "flex", justifyContent: "space-between",
                                                        alignItems: "center", fontSize: 12 }}>
                      <span>
                        {c.candidate_name} · name score {c.name_score}
                        {c.distance_km != null ? ` · ${c.distance_km} km away` : ""}
                      </span>
                      <button className="good" onClick={() => resolveMissing(p.id, c.candidate_id)}>
                        Resolve as match
                      </button>
                    </div>
                  ))}
                  {candidates.length === 0 && <span style={{ fontSize: 12, color: "var(--muted)" }}>No candidates.</span>}
                </div>
              )}
            </div>
          ))}
          {(missing || []).length === 0 && <p style={{ color: "var(--muted)" }}>No open missing/found reports.</p>}
        </div>
      </div>
    </div>
  );
}

export default function AnalystDashboard() {
  const [token, setToken] = useState<string | null>(() =>
    typeof window === "undefined" ? null : localStorage.getItem("oceanping-analyst-token")
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [correctedHazard, setCorrectedHazard] = useState("");
  const [audit, setAudit] = useState<string | null>(null);

  const fetcher = (path: string) => getJSON(path, token!);
  const { data: reports, mutate: refreshReports } = useSWR(
    token ? "/analyst/reports?limit=100" : null,
    fetcher,
    { refreshInterval: 10_000, onError: () => setToken(null) }
  );
  const { data: incidents, mutate: refreshIncidents } = useSWR(
    token ? "/analyst/incidents" : null,
    fetcher,
    { refreshInterval: 15_000 }
  );
  const { data: alerts, mutate: refreshAlerts } = useSWR(
    token ? "/analyst/alerts" : null,
    fetcher,
    { refreshInterval: 10_000 }
  );

  if (!token) return <Login onToken={setToken} />;

  const selected = (reports || []).find((r: any) => r.id === selectedId);
  const activeAlertByIncident: Record<string, any> = {};
  for (const a of alerts || []) {
    if (a.status === "active") activeAlertByIncident[a.incident_id] = a;
  }

  async function decide(action: "verify" | "reject") {
    if (!selected) return;
    await postJSON(
      `/analyst/reports/${selected.id}/${action}`,
      {
        note: note || null,
        corrected_hazard_type: action === "reject" ? correctedHazard || null : null,
      },
      token
    );
    setNote("");
    setCorrectedHazard("");
    refreshReports();
    refreshIncidents();
  }

  async function issueWarning(incidentId: string) {
    const note = window.prompt(
      "Optional note for this warning (recorded in the audit log, sent to subscribers):"
    );
    if (note === null) return; // cancelled
    await postJSON(`/analyst/incidents/${incidentId}/warning`, { note: note || null }, token!);
    refreshAlerts();
  }

  async function expireAlert(alertId: string) {
    await postJSON(`/analyst/alerts/${alertId}/expire`, {}, token!);
    refreshAlerts();
  }

  async function checkAudit() {
    const res = await getJSON("/analyst/audit/verify", token!);
    setAudit(
      res.intact
        ? `Audit chain intact — ${res.entries_checked} entries verified`
        : `⚠ AUDIT CHAIN BROKEN after ${res.entries_checked} entries`
    );
  }

  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "1fr 400px", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>Report queue</h3>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {audit && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{audit}</span>}
              <button onClick={checkAudit}>Verify audit chain</button>
            </div>
          </div>
          <table className="data" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Hazard</th>
                <th>Status</th>
                <th>Conf.</th>
                <th>Source</th>
                <th>Lang</th>
              </tr>
            </thead>
            <tbody>
              {(reports || []).map((r: any) => (
                <tr
                  key={r.id}
                  className={`selectable ${r.id === selectedId ? "selected" : ""}`}
                  onClick={() => setSelectedId(r.id)}
                >
                  <td>{new Date(r.created_at).toLocaleTimeString()}</td>
                  <td><HazardChip hazard={r.hazard_type} /></td>
                  <td><StatusChip status={r.status} /></td>
                  <td>{Math.round(r.confidence * 100)}%</td>
                  <td>{r.source}</td>
                  <td>{r.lang}</td>
                </tr>
              ))}
              {(reports || []).length === 0 && (
                <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No reports yet — run scripts/drill.py or submit one.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Incidents (deduplicated)</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Hazard</th>
                <th>Status</th>
                <th>Reports</th>
                <th>Peak conf.</th>
                <th>Last seen</th>
                <th>Alert</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(incidents || []).map((inc: any) => {
                const active = activeAlertByIncident[inc.id];
                const canIssueWarning =
                  (inc.status === "corroborated" || inc.status === "verified") &&
                  active?.tier !== "warning";
                return (
                  <tr key={inc.id}>
                    <td><HazardChip hazard={inc.hazard_type} /></td>
                    <td><StatusChip status={inc.status} /></td>
                    <td>{inc.report_count}</td>
                    <td>{Math.round(inc.max_confidence * 100)}%</td>
                    <td>{new Date(inc.last_seen).toLocaleTimeString()}</td>
                    <td>{active ? <TierChip tier={active.tier} /> : <span style={{ color: "var(--muted)" }}>—</span>}</td>
                    <td>
                      {canIssueWarning && (
                        <button className="danger" onClick={() => issueWarning(inc.id)}>
                          Issue warning
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {(incidents || []).length === 0 && (
                <tr><td colSpan={7} style={{ color: "var(--muted)" }}>No incidents yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Active alerts</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Tier</th>
                <th>Hazard</th>
                <th>Message</th>
                <th>Issued by</th>
                <th>Expires</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(alerts || []).filter((a: any) => a.status === "active").map((a: any) => (
                <tr key={a.id}>
                  <td><TierChip tier={a.tier} /></td>
                  <td><HazardChip hazard={a.hazard_type} /></td>
                  <td style={{ maxWidth: 320 }}>
                    {typeof a.message?.en === "string" ? a.message.en : a.message?.en?.standard}
                  </td>
                  <td>{a.issued_by || "automatic"}</td>
                  <td>{a.expires_at ? new Date(a.expires_at).toLocaleString() : "—"}</td>
                  <td>
                    <button onClick={() => expireAlert(a.id)}>Expire</button>
                  </td>
                </tr>
              ))}
              {(alerts || []).filter((a: any) => a.status === "active").length === 0 && (
                <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No active alerts.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <Forecasts token={token} />
        <Narratives token={token} />
        <Sitreps token={token} />
        <Recovery token={token} />
      </div>

      <div className="card" style={{ alignSelf: "start", position: "sticky", top: 65 }}>
        <h3>Report detail</h3>
        {!selected && <p style={{ color: "var(--muted)" }}>Select a report from the queue.</p>}
        {selected && (
          <>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              <HazardChip hazard={selected.hazard_type} />
              <StatusChip status={selected.status} />
              <span className="chip">urgency: {selected.urgency}</span>
            </div>
            {selected.text && (
              <p style={{ background: "var(--surface-2)", padding: 10, borderRadius: 8 }}>
                “{selected.text}”
              </p>
            )}
            <p style={{ fontSize: 12, color: "var(--muted)" }}>
              {selected.lat.toFixed(5)}, {selected.lon.toFixed(5)} · cell {selected.h3_cell}
              <br />
              via {selected.source} · reporter trust {selected.reporter.trust_score}
              {" "}({selected.reporter.verified_count}✓ / {selected.reporter.debunked_count}✗)
            </p>
            <ConfidenceBars report={selected} />
            {selected.status !== "verified" && selected.status !== "rejected" && (
              <div style={{ marginTop: 14 }}>
                <label className="field">
                  <span>Decision note (goes to the audit log)</span>
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    style={{ width: "100%" }}
                    placeholder="e.g. matches tide gauge anomaly + 5 independent reports"
                  />
                </label>
                <label className="field">
                  <span>Wrong hazard type? Which? (only used if you reject)</span>
                  <select
                    value={correctedHazard}
                    onChange={(e) => setCorrectedHazard(e.target.value)}
                    style={{ width: "100%" }}
                  >
                    <option value="">— not a misclassification —</option>
                    {Object.entries(HAZARD_LABELS)
                      .filter(([key]) => key !== selected.hazard_type)
                      .map(([key, label]) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                  </select>
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="good" style={{ flex: 1 }} onClick={() => decide("verify")}>
                    ✓ Verify
                  </button>
                  <button className="danger" style={{ flex: 1 }} onClick={() => decide("reject")}>
                    ✗ Reject
                  </button>
                </div>
                <p style={{ fontSize: 12, color: "var(--muted)" }}>
                  Verifying publishes this report to the public map and raises the
                  reporter's trust; rejecting lowers it. Both are recorded in the
                  hash-chained audit log. If the report was rejected because the
                  hazard type was wrong (not because it wasn't credible), picking
                  the correct type above turns it into a labeled training example
                  instead of just a negative signal.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
