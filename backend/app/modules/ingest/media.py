"""Media intake + forensics: pHash recycled-media check and EXIF consistency."""
import logging
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MediaAsset

log = logging.getLogger(__name__)

GPS_IFD = 0x8825
EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 36867
PHASH_REUSE_MAX_HAMMING = 6


def save_upload(data: bytes, filename: str | None) -> Path:
    media_dir = Path(get_settings().media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename or "photo.jpg").suffix or ".jpg"
    path = media_dir / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return path


def compute_phash(path: Path) -> str | None:
    try:
        import imagehash

        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        log.warning("pHash failed for %s", path, exc_info=True)
        return None


def _dms_to_deg(dms, ref) -> float:
    deg = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    return -deg if ref in ("S", "W") else deg


def extract_exif(path: Path) -> dict:
    """Return {gps_lat, gps_lon, taken_at_iso} where available."""
    out: dict = {}
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return out
            gps = exif.get_ifd(GPS_IFD)
            if gps and 2 in gps and 4 in gps:
                out["gps_lat"] = _dms_to_deg(gps[2], gps.get(1, "N"))
                out["gps_lon"] = _dms_to_deg(gps[4], gps.get(3, "E"))
            exif_ifd = exif.get_ifd(EXIF_IFD)
            raw_dt = exif_ifd.get(TAG_DATETIME_ORIGINAL)
            if raw_dt:
                taken = datetime.strptime(str(raw_dt), "%Y:%m:%d %H:%M:%S")
                out["taken_at_iso"] = taken.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        log.warning("EXIF extraction failed for %s", path, exc_info=True)
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def is_phash_reused(db: Session, phash: str | None) -> bool:
    """Recycled-media check against every previously seen asset (Hamming distance)."""
    if phash is None:
        return False
    try:
        import imagehash

        new_hash = imagehash.hex_to_hash(phash)
    except Exception:
        return False
    seen = db.scalars(select(MediaAsset.phash).where(MediaAsset.phash.isnot(None))).all()
    for old in seen:
        try:
            if (new_hash - imagehash.hex_to_hash(old)) <= PHASH_REUSE_MAX_HAMMING:
                return True
        except Exception:
            continue
    return False


def run_forensics(
    db: Session,
    path: Path,
    claimed_lat: float,
    claimed_lon: float,
    claimed_time: datetime,
) -> dict:
    """Return {phash, reused, exif, gps_km, time_offset_hours}."""
    phash = compute_phash(path)
    exif = extract_exif(path)
    gps_km = None
    if "gps_lat" in exif:
        gps_km = round(
            haversine_km(claimed_lat, claimed_lon, exif["gps_lat"], exif["gps_lon"]), 2
        )
    time_offset_hours = None
    if "taken_at_iso" in exif:
        taken = datetime.fromisoformat(exif["taken_at_iso"])
        time_offset_hours = round(abs((claimed_time - taken).total_seconds()) / 3600, 2)
    return {
        "phash": phash,
        "reused": is_phash_reused(db, phash),
        "exif": exif,
        "gps_km": gps_km,
        "time_offset_hours": time_offset_hours,
    }
