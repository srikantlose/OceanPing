"""Live end-to-end check for the LoRaWAN/IoT pilot (phase 3, milestone 6).

Publishes real MQTT telemetry to the EMQX broker and asserts it flows all the
way through the shared sensor path: the node self-registers as a Station with
provider "iot", its readings land in sensor_readings, and a surge value drives
the *existing* anomaly detector — no IoT-specific scoring anywhere.

Not part of the unit suite (it needs the stack up and an MQTT client):
    pip install paho-mqtt
    python scripts/iot/iot_live_check.py [--api http://localhost:8000] [--mqtt-host localhost]

Mirrors mobile/tests/live.integration.ts and scripts/drill.py: host-run,
against the compose-mapped ports.
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures += 1


def api(base: str, path: str, *, method="GET", token=None, json_body=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def publish(host: str, port: int, topic: str, payload: dict) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(host, port)
    client.loop_start()
    info = client.publish(topic, json.dumps(payload), qos=1)
    info.wait_for_publish(timeout=10)
    client.loop_stop()
    client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args()

    # Windows consoles often default to cp1252; keep the output portable
    # (same guard as scripts/drill.py).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"→ Live IoT check: MQTT {args.mqtt_host}:{args.mqtt_port} → API {args.api}")
    token = api(args.api, "/auth/login", method="POST",
                json_body={"username": "analyst", "password": "oceanping-dev"})["token"]

    node_id = f"live-{int(time.time())}"
    station_id = f"iot-{node_id}"
    topic = f"oceanping/iot/{node_id}/telemetry"
    now = datetime.now(timezone.utc)

    # 7 days of hourly calm baseline (deterministic small ripple so the
    # baseline has non-zero spread), then a surge — the shape the anomaly
    # detector needs to fire, published over the real broker.
    baseline = [
        {
            "variable": "water_level",
            "value": round(1.0 + 0.05 * ((h % 5) - 2), 3),
            "time": (now - timedelta(hours=h)).isoformat(),
        }
        for h in range(24 * 7, 1, -1)
    ]
    publish(args.mqtt_host, args.mqtt_port, topic, {
        "name": "Live pilot buoy — Ennore", "lat": 13.214, "lon": 80.322, "readings": baseline,
    })
    publish(args.mqtt_host, args.mqtt_port, topic, {
        "readings": [{"variable": "water_level", "value": 3.4, "time": now.isoformat()}],
    })

    # The bridge consumes asynchronously — give it a few seconds and poll.
    node = None
    for _ in range(15):
        stations = api(args.api, "/map/stations")["features"]
        node = next((s for s in stations if s["properties"]["id"] == station_id), None)
        if node and node["properties"]["latest"].get("water_level") is not None:
            break
        time.sleep(1.0)

    check("node self-registered as a Station via MQTT", node is not None,
          "" if node else f"{station_id} never appeared in /map/stations")
    if node is None:
        print("\n✗ IoT bridge didn't consume the message — is the iot-bridge service up?")
        sys.exit(1)

    props = node["properties"]
    check("node carries the iot provider", props["provider"] == "iot", props["provider"])
    check("node placed at its reported location",
          abs(node["geometry"]["coordinates"][1] - 13.214) < 0.01, str(node["geometry"]["coordinates"]))
    check("latest water_level reflects the surge value",
          abs(props["latest"]["water_level"] - 3.4) < 0.001, str(props["latest"].get("water_level")))
    # /map/stations windows its series to the last 24h, so hourly data shows
    # ~24 points here even though the full 7-day baseline landed — the anomaly
    # check below (which needs >=30 baseline points across 7 days) is the real
    # proof the whole series persisted.
    check("recent readings render in the 24h station window",
          len(props["series"].get("water_level", [])) >= 20,
          f'{len(props["series"].get("water_level", []))} points in last 24h')

    # Run the same anomaly detection the scheduler runs — the IoT surge must
    # drive it exactly like an ERDDAP anomaly would, which only fires if the
    # full multi-day baseline is present in sensor_readings.
    api(args.api, "/drill/tick", method="POST", token=token)
    stations = api(args.api, "/map/stations")["features"]
    node = next(s for s in stations if s["properties"]["id"] == station_id)
    anomalies = node["properties"]["anomalies"]
    check("the IoT surge drove the existing anomaly detector (proving the 7-day baseline landed)",
          any(a["variable"] == "water_level" for a in anomalies),
          f"anomalies={anomalies}")

    print(failures == 0 and "\n✓ Live IoT check passed." or f"\n✗ {failures} check(s) failed.")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
