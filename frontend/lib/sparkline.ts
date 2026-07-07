// Tiny single-series sparkline as an SVG string (usable inside MapLibre popups).
// One series → no legend; the caption names it. 2px line, endpoint marker,
// min/max in muted ink.
export function sparklineSVG(
  points: Array<[string, number]>,
  color = "#3987e5",
  width = 208,
  height = 48
): string {
  if (!points || points.length < 2) {
    return `<div class="spark-caption">not enough data yet</div>`;
  }
  const values = points.map((p) => p[1]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = 4;
  const span = max - min || 1;
  const stepX = (width - pad * 2) / (points.length - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${(pad + i * stepX).toFixed(1)},${y(p[1]).toFixed(1)}`)
    .join(" ");
  const lastX = pad + (points.length - 1) * stepX;
  const lastY = y(values[values.length - 1]);
  const latest = values[values.length - 1];
  return `
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="last 24 h, latest ${latest}">
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#383835" stroke-width="1"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="3" fill="${color}" stroke="#1a1a19" stroke-width="2"/>
    </svg>
    <div class="spark-caption">24 h · min ${min.toFixed(2)} · max ${max.toFixed(2)} · latest <b style="color:#ffffff">${latest.toFixed(2)}</b></div>
  `;
}
