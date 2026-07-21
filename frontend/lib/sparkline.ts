// Single- or dual-series sparkline as an SVG string (usable inside MapLibre
// popups). The optional second series (a station's harmonic-trend sensor
// forecast, phase 3 milestone 3) is drawn dashed, continuing on from the
// last observed point on the same scale.
export function sparklineSVG(
  points: Array<[string, number]>,
  forecastPoints: Array<[string, number]> = [],
  color = "#3987e5",
  forecastColor = "#e5a339",
  width = 208,
  height = 48
): string {
  if (!points || points.length < 2) {
    return `<div class="spark-caption">not enough data yet</div>`;
  }
  const values = points.map((p) => p[1]);
  const allValues = forecastPoints.length ? [...values, ...forecastPoints.map((p) => p[1])] : values;
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const pad = 4;
  const span = max - min || 1;
  const totalPoints = points.length + forecastPoints.length;
  const stepX = (width - pad * 2) / Math.max(1, totalPoints - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);
  const x = (i: number) => pad + i * stepX;

  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const lastX = x(points.length - 1);
  const lastY = y(values[values.length - 1]);
  const latest = values[values.length - 1];

  let forecastPath = "";
  if (forecastPoints.length > 0) {
    const fd = [
      `M${lastX.toFixed(1)},${lastY.toFixed(1)}`,
      ...forecastPoints.map((p, i) => `L${x(points.length + i).toFixed(1)},${y(p[1]).toFixed(1)}`),
    ].join(" ");
    forecastPath = `<path d="${fd}" fill="none" stroke="${forecastColor}" stroke-width="2" stroke-dasharray="3,2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }

  return `
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="last 24 h, latest ${latest}">
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#383835" stroke-width="1"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${forecastPath}
      <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="3" fill="${color}" stroke="#1a1a19" stroke-width="2"/>
    </svg>
    <div class="spark-caption">24 h · min ${min.toFixed(2)} · max ${max.toFixed(2)} · latest <b style="color:#ffffff">${latest.toFixed(2)}</b>${
      forecastPoints.length ? ` · <span style="color:${forecastColor}">- - 3h forecast</span>` : ""
    }</div>
  `;
}
