#!/usr/bin/env bash
# Downloads Geofabrik's southern-India OSM extract (~550 MB — India has no
# state-level split on Geofabrik, only zone-level) and clips it down to a
# bounding box around this deployment's pilot district (Chennai + the coastal
# neighbourhoods already named throughout this project: Ennore, Kasimedu,
# Marina Beach, Besant Nagar, Injambakkam — see fisherman/pfz.py and
# ivr/locations.py). Valhalla only ever builds tiles from the clipped pilot
# extract, matching the phase-2 plan's "pilot districts, not all-India" call.
#
# Clipping needs osmium-tool, which isn't worth adding to any long-lived
# image just for a one-time data-prep step — this runs it in a throwaway
# ubuntu container instead. Re-run this script any time the pilot bbox needs
# to change; it's idempotent (skips the big download if already present).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
mkdir -p data/osm

SOUTHERN_ZONE_URL="https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"
RAW_PBF="data/osm/southern-zone.osm.pbf"
PILOT_PBF="data/osm/chennai-pilot.osm.pbf"

# Chennai metro + pilot coastline, with margin for a connected road network
# (not just a coastal strip — Valhalla needs inland roads to route through).
PILOT_BBOX="79.95,12.70,80.50,13.35"

if [ ! -f "$RAW_PBF" ]; then
  echo "Downloading southern-zone OSM extract (~550 MB, one-time)..."
  curl -L -o "$RAW_PBF.part" "$SOUTHERN_ZONE_URL"
  mv "$RAW_PBF.part" "$RAW_PBF"
else
  echo "Reusing existing $RAW_PBF"
fi

echo "Clipping to pilot bbox $PILOT_BBOX..."
# MSYS_NO_PATHCONV: on Windows + Git Bash, MSYS auto-mangles the /data
# container-side path in -v host:/data as if it were a POSIX path needing
# translation. Harmless on real Linux/macOS shells (the var just doesn't
# apply there).
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd)/data/osm:/data" ubuntu:22.04 bash -c "
  set -e
  apt-get update -qq
  apt-get install -qq -y osmium-tool >/dev/null
  osmium extract --bbox=$PILOT_BBOX --overwrite -o /data/chennai-pilot.osm.pbf /data/southern-zone.osm.pbf
"

echo "Done: $PILOT_PBF ($(du -h "$PILOT_PBF" | cut -f1))"
echo "Restart the valhalla container to build tiles from it: docker compose restart valhalla"
