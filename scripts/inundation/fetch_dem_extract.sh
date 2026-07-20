#!/usr/bin/env bash
# Downloads the Copernicus DEM GLO-30 tiles (public AWS Open Data bucket,
# HTTPS, no account/credentials needed) covering this deployment's coastal
# pilot bbox, then merges + clips them to that bbox with GDAL. Mirrors
# scripts/routing/fetch_osm_extract.sh: real data, pilot-scoped, and the
# processing tool (GDAL) runs in a throwaway container rather than being
# added to any long-lived image.
#
# Idempotent: skips tiles/output that already exist. Re-run if the pilot bbox
# changes.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
mkdir -p data/dem

# Chennai coastal pilot strip — covers every named pilot location elsewhere in
# this project (Marina, Besant Nagar, Kasimedu, Injambakkam, Ennore; see
# fisherman/pfz.py, ivr/locations.py, routing/shelters_seed.json) with margin,
# narrower than the routing OSM bbox since inundation only matters near the
# coast. Falls entirely within the Copernicus DEM's E080 tile column.
MINLON=80.10
MINLAT=12.85
MAXLON=80.40
MAXLAT=13.30

BASE_URL="https://copernicus-dem-30m.s3.amazonaws.com"
TILES=("Copernicus_DSM_COG_10_N12_00_E080_00_DEM" "Copernicus_DSM_COG_10_N13_00_E080_00_DEM")
OUT="data/dem/chennai-coastal-dem.tif"

for name in "${TILES[@]}"; do
  dest="data/dem/$name.tif"
  if [ -f "$dest" ]; then
    echo "Reusing existing $dest"
    continue
  fi
  echo "Downloading $name.tif..."
  curl -fL -o "$dest.part" "$BASE_URL/$name/$name.tif"
  mv "$dest.part" "$dest"
done

echo "Merging + clipping to pilot bbox $MINLON,$MINLAT,$MAXLON,$MAXLAT..."
# MSYS_NO_PATHCONV: see fetch_osm_extract.sh's comment — Git Bash on Windows
# otherwise mangles the container-side /data path.
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd)/data/dem:/data" ghcr.io/osgeo/gdal:ubuntu-small-latest bash -c "
  set -e
  gdalwarp -te $MINLON $MINLAT $MAXLON $MAXLAT -overwrite \
    /data/Copernicus_DSM_COG_10_N12_00_E080_00_DEM.tif \
    /data/Copernicus_DSM_COG_10_N13_00_E080_00_DEM.tif \
    /data/chennai-coastal-dem.tif
"

echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Next: scripts/inundation/build_elevation_cells.sh to compute the per-H3-cell elevation table."
