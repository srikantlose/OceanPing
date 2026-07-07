"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { clientId, postForm } from "@/lib/api";
import { HAZARD_LABELS } from "@/lib/palette";

const CHENNAI: [number, number] = [80.2824, 13.05];

const PICKER_STYLE: any = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: ["a", "b", "c", "d"].map(
        (s) => `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png`
      ),
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
};

export default function ReportForm() {
  const mapDiv = useRef<HTMLDivElement>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const [coords, setCoords] = useState<[number, number] | null>(null);
  const [hazard, setHazard] = useState("coastal_flooding");
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!mapDiv.current) return;
    const map = new maplibregl.Map({
      container: mapDiv.current,
      style: PICKER_STYLE,
      center: CHENNAI,
      zoom: 11,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("click", (e) => {
      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      if (!markerRef.current) {
        markerRef.current = new maplibregl.Marker({ color: "#3987e5" })
          .setLngLat(lngLat)
          .addTo(map);
      } else {
        markerRef.current.setLngLat(lngLat);
      }
      setCoords(lngLat);
    });
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition((pos) => {
        map.setCenter([pos.coords.longitude, pos.coords.latitude]);
      });
    }
    return () => map.remove();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!coords) {
      setResult({ ok: false, message: "Tap the map to mark where the hazard is." });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const form = new FormData();
      form.set("lat", String(coords[1]));
      form.set("lon", String(coords[0]));
      form.set("client_id", clientId());
      form.set("hazard_type", hazard);
      if (text.trim()) form.set("text", text.trim());
      if (photo) form.set("photo", photo);
      const rep = await postForm("/reports", form);
      setResult({
        ok: true,
        message: `Report received (ref ${rep.id.slice(0, 8)}). It is now being cross-checked against ocean sensors and nearby reports.`,
      });
      setText("");
      setPhoto(null);
    } catch (err: any) {
      setResult({ ok: false, message: err.message || "Submission failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 16 }}>
      <div className="card" style={{ padding: 0, overflow: "hidden", minHeight: 480 }}>
        <div ref={mapDiv} style={{ width: "100%", height: "100%", minHeight: 480 }} />
      </div>
      <form className="card" onSubmit={submit}>
        <h3>Report a coastal hazard</h3>
        <p style={{ color: "var(--ink-2)", marginTop: 0 }}>
          Tap the map where you see the hazard, then describe it — any language works.
        </p>
        <label className="field">
          <span>Location</span>
          <input
            readOnly
            value={coords ? `${coords[1].toFixed(5)}, ${coords[0].toFixed(5)}` : "Tap the map…"}
          />
        </label>
        <label className="field">
          <span>Hazard type</span>
          <select value={hazard} onChange={(e) => setHazard(e.target.value)} style={{ width: "100%" }}>
            {Object.entries(HAZARD_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>What do you see? (optional)</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ width: "100%" }}
            placeholder="e.g. Sea water entering the streets near the market…"
          />
        </label>
        <label className="field">
          <span>Photo (optional — checked for authenticity)</span>
          <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] || null)} />
        </label>
        <button className="primary" disabled={busy} style={{ width: "100%" }}>
          {busy ? "Submitting…" : "Submit report"}
        </button>
        {result && (
          <div className={`notice ${result.ok ? "ok" : "err"}`}>{result.message}</div>
        )}
        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          Your exact location is only visible to response analysts; the public map shows
          reports at neighbourhood (H3 cell) level.
        </p>
      </form>
    </div>
  );
}
