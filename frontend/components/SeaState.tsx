"use client";

import { useEffect, useState } from "react";
import { getJSON } from "@/lib/api";

type StationReading = {
  station_id: string;
  station_name: string;
  distance_km: number;
  is_local: boolean;
  latest: Record<string, { value: number; time: string }>;
  anomalies: { variable: string; zscore: number }[];
};

type PfzZone = {
  lat: number;
  lon: number;
  depth_m: number;
  distance_km: number;
  bearing: string;
  valid_until: string;
};

export default function SeaState() {
  const [station, setStation] = useState<StationReading | null | undefined>(undefined);
  const [pfz, setPfz] = useState<{ sector: string; zones: PfzZone[] } | undefined>(undefined);

  useEffect(() => {
    const loadState = (lat?: number, lon?: number) => {
      const qs = lat != null && lon != null ? `?lat=${lat}&lon=${lon}` : "";
      getJSON(`/sea/state${qs}`)
        .then((res) => setStation(res.station))
        .catch(() => setStation(null));
    };
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => loadState(pos.coords.latitude, pos.coords.longitude),
        () => loadState(),
        { timeout: 5000 }
      );
    } else {
      loadState();
    }
    getJSON("/sea/pfz")
      .then(setPfz)
      .catch(() => setPfz({ sector: "", zones: [] }));
  }, []);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div className="card">
        <h3>Sea state</h3>
        {station === undefined && <p style={{ color: "var(--ink-2)" }}>Loading…</p>}
        {station === null && (
          <p style={{ color: "var(--ink-2)" }}>No instrument stations are configured yet.</p>
        )}
        {station && (
          <>
            <div className="conf-total">
              <span className="value">{station.station_name}</span>
            </div>
            <p style={{ color: "var(--muted)", marginTop: 0 }}>
              {station.distance_km.toLocaleString()} km away
            </p>
            {!station.is_local && (
              <div className="notice">
                This is the nearest configured station, but it's far from this pilot
                area — no closer instrument is set up yet.
              </div>
            )}
            {Object.entries(station.latest).map(([variable, point]) => (
              <div key={variable} className="bar-row" style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--ink-2)" }}>{variable}</span>
                <span>{point.value}</span>
              </div>
            ))}
            {Object.keys(station.latest).length === 0 && (
              <p style={{ color: "var(--muted)", fontSize: 12 }}>No readings in the last 24h.</p>
            )}
            {station.anomalies.map((a) => (
              <div key={a.variable} className="notice err">
                ⚠ {a.variable} anomaly (z={a.zscore})
              </div>
            ))}
          </>
        )}
      </div>

      <div className="card">
        <h3>Potential fishing zones{pfz?.sector ? ` — ${pfz.sector}` : ""}</h3>
        {pfz === undefined && <p style={{ color: "var(--ink-2)" }}>Loading…</p>}
        {pfz && pfz.zones.length === 0 && (
          <p style={{ color: "var(--ink-2)" }}>No potential fishing zone advisory is active right now.</p>
        )}
        {pfz?.zones.map((z, i) => (
          <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid var(--grid)" }}>
            <div>{z.bearing}</div>
            <div style={{ color: "var(--muted)", fontSize: 12 }}>
              depth ~{z.depth_m} m · valid until {new Date(z.valid_until).toLocaleString()}
            </div>
          </div>
        ))}
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 12 }}>
          Deterministic placeholder zones for this pilot deployment — INCOIS's real PFZ
          advisories aren't published as a machine-readable feed (see the phase-2 plan).
        </p>
      </div>
    </div>
  );
}
