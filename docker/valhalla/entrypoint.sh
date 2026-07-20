#!/usr/bin/env bash
# Builds routing tiles from the pilot-district OSM extract on first start
# (persisted in the valhalla_tiles volume, so restarts don't rebuild), then
# serves Valhalla's HTTP API. Admin/timezone databases are skipped: they only
# feed turn-by-turn narrative text and DST-aware ETAs, not the route
# geometry or exclude_polygons this app actually uses, and building them
# needs extra downloads this pilot scope doesn't need to depend on.
set -euo pipefail

TILE_DIR=/data/valhalla
CONFIG=/data/valhalla/valhalla.json
PBF="${OSM_PBF:-/data/osm/chennai-pilot.osm.pbf}"

# Regenerated on every start (cheap — it's just a text file) so a config
# change never needs the tile volume wiped to take effect.
valhalla_build_config \
  --mjolnir-tile-dir "$TILE_DIR" \
  --mjolnir-data-processing-use-admin-db False \
  --service-limits-max-exclude-polygons-length 200000 \
  > "$CONFIG"

if [ ! -d "$TILE_DIR/0" ]; then
  if [ ! -f "$PBF" ]; then
    echo "Valhalla: no OSM extract at $PBF yet." >&2
    echo "Run scripts/routing/fetch_osm_extract.sh on the host, then restart this container." >&2
    echo "Staying up (not crash-looping) so /route's caller gets a clean connection-refused instead." >&2
    exec sleep infinity
  fi
  echo "Valhalla: building tiles from $PBF (one-time; persisted in the valhalla_tiles volume)..."
  valhalla_build_tiles -c "$CONFIG" "$PBF"
fi

exec valhalla_service "$CONFIG" 1
