"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { API_BASE } from "@/lib/api";
import {
  ALERT_TIER_COLORS,
  ALERT_TIER_LABELS,
  HAZARD_COLORS,
  HAZARD_LABELS,
  INK,
  STATUS_COLORS,
} from "@/lib/palette";
import { sparklineSVG } from "@/lib/sparkline";

const REFRESH_MS = 15_000;
const CHENNAI: [number, number] = [80.2824, 13.05];

const BASE_STYLE: any = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
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

const EMPTY_FC = { type: "FeatureCollection", features: [] };

function hazardMatch(): any {
  const pairs = Object.entries(HAZARD_COLORS).flat();
  return ["match", ["get", "hazard_type"], ...pairs.slice(0, -1), HAZARD_COLORS.other];
}

function tierMatch(): any {
  const pairs = Object.entries(ALERT_TIER_COLORS).flat();
  return ["match", ["get", "tier"], ...pairs.slice(0, -1), ALERT_TIER_COLORS.advisory];
}

async function fetchFC(path: string) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!res.ok) return EMPTY_FC;
    return await res.json();
  } catch {
    return EMPTY_FC;
  }
}

export default function MapView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_STYLE,
      center: CHENNAI,
      zoom: 11.5,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      for (const id of ["hotspots", "alerts", "incident-cells", "incidents", "reports", "stations"]) {
        map.addSource(id, { type: "geojson", data: EMPTY_FC as any });
      }

      map.addLayer({
        id: "hotspots-fill",
        type: "fill",
        source: "hotspots",
        paint: { "fill-color": INK.critical, "fill-opacity": 0.14 },
      });
      map.addLayer({
        id: "hotspots-line",
        type: "line",
        source: "hotspots",
        paint: { "line-color": INK.critical, "line-width": 2, "line-dasharray": [2, 1.5] },
      });

      map.addLayer({
        id: "alerts-fill",
        type: "fill",
        source: "alerts",
        paint: { "fill-color": tierMatch(), "fill-opacity": 0.16 },
      });
      map.addLayer({
        id: "alerts-line",
        type: "line",
        source: "alerts",
        paint: {
          "line-color": tierMatch(),
          "line-width": ["match", ["get", "tier"], "warning", 3, 2],
        },
      });

      map.addLayer({
        id: "incident-cells-fill",
        type: "fill",
        source: "incident-cells",
        paint: { "fill-color": hazardMatch(), "fill-opacity": 0.18 },
      });

      map.addLayer({
        id: "incidents-circles",
        type: "circle",
        source: "incidents",
        paint: {
          "circle-color": hazardMatch(),
          "circle-radius": [
            "interpolate", ["linear"], ["get", "report_count"],
            1, 8, 10, 16, 30, 24,
          ],
          "circle-opacity": 0.85,
          "circle-stroke-width": 2,
          "circle-stroke-color": INK.surface,
        },
      });
      map.addLayer({
        id: "incidents-count",
        type: "symbol",
        source: "incidents",
        layout: {
          "text-field": ["to-string", ["get", "report_count"]],
          "text-size": 11,
          "text-font": ["Noto Sans Regular"],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#ffffff" },
      });

      map.addLayer({
        id: "reports-circles",
        type: "circle",
        source: "reports",
        paint: {
          "circle-color": hazardMatch(),
          "circle-radius": 5,
          "circle-stroke-width": 2,
          "circle-stroke-color": INK.surface,
        },
      });

      map.addLayer({
        id: "stations-circles",
        type: "circle",
        source: "stations",
        paint: {
          "circle-color": [
            "case", ["get", "has_anomaly"], INK.critical, "#c3c2b7",
          ],
          "circle-radius": 7,
          "circle-stroke-width": 2,
          "circle-stroke-color": INK.surface,
        },
      });
      map.addLayer({
        id: "stations-icon",
        type: "symbol",
        source: "stations",
        layout: {
          "text-field": "▲",
          "text-size": 8,
          "text-font": ["Noto Sans Regular"],
          "text-allow-overlap": true,
          "text-offset": [0, 0.05],
        },
        paint: { "text-color": INK.surface },
      });

      const refresh = async () => {
        const [hotspots, alerts, incidents, reports, stations] = await Promise.all([
          fetchFC("/map/hotspots"),
          fetchFC("/map/alerts"),
          fetchFC("/map/incidents"),
          fetchFC("/map/reports"),
          fetchFC("/map/stations"),
        ]);
        const cells = {
          type: "FeatureCollection",
          features: (incidents.features || []).flatMap(
            (f: any) => (f.properties?.cells || []).map((c: any) => ({
              ...c,
              properties: { ...c.properties, hazard_type: f.properties.hazard_type },
            }))
          ),
        };
        for (const f of stations.features || []) {
          f.properties.has_anomaly = (f.properties.anomalies || []).length > 0;
        }
        (map.getSource("hotspots") as any)?.setData(hotspots);
        (map.getSource("alerts") as any)?.setData(alerts);
        (map.getSource("incident-cells") as any)?.setData(cells);
        (map.getSource("incidents") as any)?.setData(incidents);
        (map.getSource("reports") as any)?.setData(reports);
        (map.getSource("stations") as any)?.setData(stations);
      };
      refresh();
      const timer = setInterval(refresh, REFRESH_MS);
      map.once("remove", () => clearInterval(timer));

      const popup = (lngLat: any, html: string) =>
        new maplibregl.Popup({ closeButton: true, maxWidth: "260px" })
          .setLngLat(lngLat)
          .setHTML(html)
          .addTo(map);

      map.on("click", "stations-circles", (e) => {
        const p: any = e.features?.[0]?.properties;
        if (!p) return;
        const series = typeof p.series === "string" ? JSON.parse(p.series) : p.series;
        const anomalies =
          typeof p.anomalies === "string" ? JSON.parse(p.anomalies) : p.anomalies;
        const firstVar = Object.keys(series || {})[0];
        const anomalyHtml = (anomalies || [])
          .map(
            (a: any) =>
              `<div style="color:${INK.critical};font-size:12px">⚠ ${a.variable} anomaly, z=${a.zscore}</div>`
          )
          .join("");
        popup(
          e.lngLat,
          `<div class="popup-title">${p.name}</div>
           <div class="popup-sub">${p.provider}</div>
           ${anomalyHtml}
           ${firstVar ? `<div class="popup-sub" style="margin-top:6px">${firstVar}</div>${sparklineSVG(series[firstVar])}` : `<div class="spark-caption">no readings yet</div>`}`
        );
      });

      map.on("click", "alerts-fill", (e) => {
        const p: any = e.features?.[0]?.properties;
        if (!p) return;
        const tierColor = ALERT_TIER_COLORS[p.tier] || ALERT_TIER_COLORS.advisory;
        popup(
          e.lngLat,
          `<div class="popup-title" style="color:${tierColor}">${ALERT_TIER_LABELS[p.tier] || p.tier}</div>
           <div class="popup-sub">${HAZARD_LABELS[p.hazard_type] || p.hazard_type} · issued by ${p.issued_by}</div>
           <div style="font-size:12px">${p.message}</div>
           ${p.expires_at ? `<div class="spark-caption">expires ${new Date(p.expires_at).toLocaleString()}</div>` : ""}`
        );
      });

      map.on("click", "incidents-circles", (e) => {
        const p: any = e.features?.[0]?.properties;
        if (!p) return;
        popup(
          e.lngLat,
          `<div class="popup-title">${HAZARD_LABELS[p.hazard_type] || p.hazard_type}</div>
           <div class="popup-sub">Verified incident · ${p.report_count} merged report(s)</div>
           <div style="font-size:12px">Peak confidence ${(p.max_confidence * 100).toFixed(0)}%</div>
           <div class="spark-caption">since ${new Date(p.first_seen).toLocaleString()}</div>`
        );
      });

      map.on("click", "reports-circles", (e) => {
        const p: any = e.features?.[0]?.properties;
        if (!p) return;
        popup(
          e.lngLat,
          `<div class="popup-title">${HAZARD_LABELS[p.hazard_type] || p.hazard_type}</div>
           <div class="popup-sub">Verified citizen report · location shown at cell level</div>
           <div style="font-size:12px">Confidence ${(p.confidence * 100).toFixed(0)}% · urgency ${p.urgency}</div>
           <div class="spark-caption">${new Date(p.created_at).toLocaleString()}</div>`
        );
      });

      for (const layer of ["stations-circles", "incidents-circles", "reports-circles", "alerts-fill"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  return (
    <>
      <div ref={containerRef} className="map-container" />
      <div className="map-legend">
        <h4>Hazards</h4>
        {Object.entries(HAZARD_LABELS).map(([key, label]) => (
          <div className="legend-row" key={key}>
            <span className="legend-swatch" style={{ background: HAZARD_COLORS[key] }} />
            {label}
          </div>
        ))}
        <h4 style={{ marginTop: 8 }}>Alerts</h4>
        {Object.entries(ALERT_TIER_LABELS).map(([key, label]) => (
          <div className="legend-row" key={key}>
            <span className="legend-swatch ring" style={{ borderColor: ALERT_TIER_COLORS[key] }} />
            {label}
          </div>
        ))}
        <h4 style={{ marginTop: 8 }}>Layers</h4>
        <div className="legend-row">
          <span className="legend-swatch ring" style={{ borderColor: INK.critical }} />
          Active hotspot
        </div>
        <div className="legend-row">
          <span className="legend-swatch" style={{ background: "#c3c2b7" }} />
          Sensor station
        </div>
        <div className="legend-row">
          <span className="legend-swatch" style={{ background: INK.critical }} />
          Station anomaly
        </div>
        <div className="legend-row">
          <span className="legend-swatch" style={{ background: STATUS_COLORS.verified }} />
          Verified only on this map
        </div>
      </div>
    </>
  );
}
