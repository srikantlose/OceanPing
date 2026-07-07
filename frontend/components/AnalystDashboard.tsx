"use client";

import { useState } from "react";
import useSWR from "swr";
import { getJSON, postJSON } from "@/lib/api";
import {
  HAZARD_COLORS,
  HAZARD_LABELS,
  STATUS_COLORS,
  STATUS_LABELS,
} from "@/lib/palette";

const COMPONENT_LABELS: Record<string, string> = {
  trust: "Reporter trust",
  coherence: "Coherence",
  instrument: "Instruments",
  media: "Media",
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
      {comp.detail?.corroborating_anomalies?.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 6 }}>
          ⚠ {comp.detail.corroborating_anomalies.length} corroborating instrument
          anomaly(ies): {comp.detail.corroborating_anomalies
            .map((a: any) => `${a.station_id} ${a.variable} z=${a.zscore}`)
            .join("; ")}
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

export default function AnalystDashboard() {
  const [token, setToken] = useState<string | null>(() =>
    typeof window === "undefined" ? null : localStorage.getItem("oceanping-analyst-token")
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState("");
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

  if (!token) return <Login onToken={setToken} />;

  const selected = (reports || []).find((r: any) => r.id === selectedId);

  async function decide(action: "verify" | "reject") {
    if (!selected) return;
    await postJSON(`/analyst/reports/${selected.id}/${action}`, { note: note || null }, token);
    setNote("");
    refreshReports();
    refreshIncidents();
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
              </tr>
            </thead>
            <tbody>
              {(incidents || []).map((inc: any) => (
                <tr key={inc.id}>
                  <td><HazardChip hazard={inc.hazard_type} /></td>
                  <td><StatusChip status={inc.status} /></td>
                  <td>{inc.report_count}</td>
                  <td>{Math.round(inc.max_confidence * 100)}%</td>
                  <td>{new Date(inc.last_seen).toLocaleTimeString()}</td>
                </tr>
              ))}
              {(incidents || []).length === 0 && (
                <tr><td colSpan={5} style={{ color: "var(--muted)" }}>No incidents yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
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
                  hash-chained audit log.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
