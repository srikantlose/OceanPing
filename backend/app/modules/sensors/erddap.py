"""Generic ERDDAP tabledap CSV fetcher (works against INCOIS, NOAA, any ERDDAP)."""
import csv
import io
import logging
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)


def build_url(cfg: dict, since: datetime) -> str:
    var_names = ",".join(v["name"] for v in cfg["variables"])
    time_var = cfg.get("time_var", "time")
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{cfg['server']}/tabledap/{cfg['dataset']}.csvp"
        f"?{time_var},{var_names}"
        f"&{cfg['station_query']}"
        f"&{time_var}>={since_iso}"
    )


def fetch_readings(cfg: dict, since: datetime) -> list[dict]:
    """Return [{time, variable(label), value}] rows; empty list on any failure."""
    url = build_url(cfg, since)
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("ERDDAP fetch failed for %s: %s", cfg["id"], exc)
        return []

    label_by_name = {v["name"]: v["label"] for v in cfg["variables"]}
    time_var = cfg.get("time_var", "time")
    rows: list[dict] = []
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, None)
    if not header:
        return []
    # csvp headers look like "time (UTC)" / "wvht (m)" — strip the unit suffix.
    names = [h.split(" (")[0].strip() for h in header]
    for raw in reader:
        record = dict(zip(names, raw))
        ts_raw = record.get(time_var, "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        for name, label in label_by_name.items():
            val_raw = (record.get(name) or "").strip()
            if not val_raw or val_raw.lower() == "nan":
                continue
            try:
                value = float(val_raw)
            except ValueError:
                continue
            rows.append({"time": ts, "variable": label, "value": value})
    return rows
