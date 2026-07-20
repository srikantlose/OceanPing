#!/usr/bin/env bash
# Computes the per-H3-cell (res 9) elevation table that
# modules/inundation/seed.py loads into the DB, from the DEM extract
# scripts/inundation/fetch_dem_extract.sh produces. Two throwaway containers,
# neither baked into any long-lived image:
#   1. python:3.12-slim + pip-installed h3 — enumerate H3 cells covering the
#      pilot bbox and their centroids (pure Python, no GDAL needed).
#   2. the GDAL image already used for clipping — sample the DEM at each
#      centroid via its Python bindings (osgeo.gdal ships in that image, so
#      this needs no pip install and no rasterio dependency in the backend).
# Output: backend/app/modules/inundation/elevation_cells_chennai.json,
# committed to the repo like routing/shelters_seed.json — real ingested data,
# not illustrative.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

DEM_TIF="data/dem/chennai-coastal-dem.tif"
if [ ! -f "$DEM_TIF" ]; then
  echo "Missing $DEM_TIF — run scripts/inundation/fetch_dem_extract.sh first." >&2
  exit 1
fi

MINLON=80.10
MINLAT=12.85
MAXLON=80.40
MAXLAT=13.30
H3_RES=9

OUT_DIR="backend/app/modules/inundation"

echo "Enumerating res-$H3_RES H3 cells over the pilot bbox..."
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd)/data/dem:/data" python:3.12-slim bash -c "
  set -e
  pip install -q 'h3>=4.0'
  python3 -c \"
import json
import h3

poly = h3.LatLngPoly([($MINLAT, $MINLON), ($MINLAT, $MAXLON), ($MAXLAT, $MAXLON), ($MAXLAT, $MINLON)])
cells = h3.polygon_to_cells(poly, $H3_RES)
points = [{'h3_cell': c, 'lat': lat, 'lon': lon} for c in cells for lat, lon in [h3.cell_to_latlng(c)]]
json.dump(points, open('/data/centroids.json', 'w'))
print(f'{len(points)} cells enumerated')
\"
"

echo "Sampling DEM elevation at each centroid..."
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd)/data/dem:/data" ghcr.io/osgeo/gdal:ubuntu-small-latest python3 -c "
import json
from osgeo import gdal

ds = gdal.Open('/data/chennai-coastal-dem.tif')
band = ds.GetRasterBand(1)
gt = ds.GetGeoTransform()
inv = gdal.InvGeoTransform(gt)
nodata = band.GetNoDataValue()

points = json.load(open('/data/centroids.json'))
out = []
for p in points:
    px, py = gdal.ApplyGeoTransform(inv, p['lon'], p['lat'])
    px, py = int(px), int(py)
    if px < 0 or py < 0 or px >= ds.RasterXSize or py >= ds.RasterYSize:
        continue
    val = band.ReadAsArray(px, py, 1, 1)[0][0]
    if nodata is not None and float(val) == nodata:
        continue
    out.append({'h3_cell': p['h3_cell'], 'elevation_m': round(float(val), 2)})

json.dump(out, open('/data/elevation_cells_chennai.json', 'w'))
print(f'{len(out)} cells with elevation written')
"

cp "data/dem/elevation_cells_chennai.json" "$OUT_DIR/elevation_cells_chennai.json"
echo "Done: $OUT_DIR/elevation_cells_chennai.json"
