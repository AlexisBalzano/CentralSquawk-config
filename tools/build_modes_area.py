"""Build the Mode S conspicuity area polygon from the Navigraph DFD database.

The area is the union of the FIR/UIR extents of the states participating in the
EUROCONTROL Mode S programme. There is no published polygon for it, so it is
reconstructed here from tbl_uf_fir_uir, which carries the boundary of every FIR
and UIR in the world as an ordered list of points.

The participating states are taken from CCAMS -- the plugin that performs this
job in production on VATSIM -- which encodes the area as an ICAO prefix regex in
its config.txt. Decoding that regex gives the 22 prefixes in PARTICIPATING.

Output is a GeoJSON FeatureCollection, one Feature per FIR/UIR record, so it can
be dropped straight into geojson.io and inspected. Containment against it means
"inside ANY feature", which is the union without needing a geometry library.

Usage:  py tools/build_modes_area.py [--db db.s3db] [--out modes_area.geojson]
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

# ICAO region prefixes of the Mode S participating states, decoded from the
# CCAMS config.txt airport regex. Deliberately excludes EG, EI, EN, EF, LG, LP
# and LT: those states are not in the CCAMS area.
PARTICIPATING = [
    "EB",  # Belgium
    "ED",  # Germany
    "EH",  # Netherlands
    "EK",  # Denmark
    "EL",  # Luxembourg (no FIR of its own -- inside Brussels FIR)
    "EP",  # Poland
    "ES",  # Sweden
    "EY",  # Lithuania
    "EV",  # Latvia
    "LB",  # Bulgaria
    "LD",  # Croatia
    "LE",  # Spain (peninsular -- Canaries GCCC are not participating)
    "LF",  # France
    "LH",  # Hungary
    "LI",  # Italy
    "LJ",  # Slovenia
    "LK",  # Czechia
    "LO",  # Austria
    "LR",  # Romania
    "LS",  # Switzerland
    "LU",  # Moldova
    "LZ",  # Slovakia
]

# F = FIR, U = UIR, B = both. C records are airspace DELEGATED from one unit to
# another; they are sub-areas rather than national extents, and including them
# would enlarge the area with chunks of non-participating states' airspace that
# happen to be worked by a participating unit. Left out deliberately: a smaller
# area is the conservative direction, since it yields discrete codes rather than
# conspicuity codes when in doubt.
WANTED_INDICATORS = ("F", "U", "B")

ARC_STEP_DEG = 2.0  # arc interpolation granularity


def bearing_from(lat0: float, lon0: float, lat: float, lon: float) -> float:
    """Initial great-circle bearing in degrees from one point to another."""
    p0, p1 = math.radians(lat0), math.radians(lat)
    dl = math.radians(lon - lon0)
    y = math.sin(dl) * math.cos(p1)
    x = math.cos(p0) * math.sin(p1) - math.sin(p0) * math.cos(p1) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def point_at(lat0: float, lon0: float, bearing: float, distance_nm: float) -> tuple[float, float]:
    """Point a given bearing and distance from an origin, on a spherical earth."""
    ang = distance_nm / 3440.065  # nautical miles per earth radius
    p0, b = math.radians(lat0), math.radians(bearing)
    lat = math.asin(math.sin(p0) * math.cos(ang) + math.cos(p0) * math.sin(ang) * math.cos(b))
    lon = math.radians(lon0) + math.atan2(
        math.sin(b) * math.sin(ang) * math.cos(p0),
        math.cos(ang) - math.sin(p0) * math.sin(lat),
    )
    return math.degrees(lat), (math.degrees(lon) + 540.0) % 360.0 - 180.0


def expand_arc(prev: tuple[float, float], cur: tuple[float, float],
               origin: tuple[float, float], radius_nm: float, clockwise: bool) -> list[tuple[float, float]]:
    """Points along an arc segment, excluding its start and including its end.

    ARINC 424 gives the arc's END point on the row that carries the R/L code,
    with the arc running from the previous boundary point around arc_origin.
    """
    if origin[0] is None or origin[1] is None or not radius_nm:
        return [cur]
    start = bearing_from(origin[0], origin[1], prev[0], prev[1])
    end = bearing_from(origin[0], origin[1], cur[0], cur[1])
    sweep = (end - start) % 360.0 if clockwise else -((start - end) % 360.0)
    steps = max(1, int(abs(sweep) / ARC_STEP_DEG))
    out = []
    for i in range(1, steps + 1):
        out.append(point_at(origin[0], origin[1], start + sweep * i / steps, radius_nm))
    out[-1] = cur  # land exactly on the published end point
    return out


def build_rings(db: sqlite3.Connection) -> list[tuple[str, str, str, list[tuple[float, float]]]]:
    """(identifier, indicator, name, ring) for every participating FIR/UIR."""
    where_ident = " or ".join(["fir_uir_identifier like ?"] * len(PARTICIPATING))
    where_ind = ",".join("?" * len(WANTED_INDICATORS))
    rows = db.execute(
        "select fir_uir_identifier, fir_uir_indicator, fir_uir_name, seqno, boundary_via,"
        " fir_uir_latitude, fir_uir_longitude, arc_origin_latitude, arc_origin_longitude,"
        " arc_distance"
        f" from tbl_uf_fir_uir where ({where_ident}) and fir_uir_indicator in ({where_ind})"
        " order by fir_uir_identifier, fir_uir_indicator, seqno",
        [p + "%" for p in PARTICIPATING] + list(WANTED_INDICATORS),
    ).fetchall()

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    names: dict[tuple[str, str], str] = {}
    for ident, ind, name, *rest in rows:
        grouped[(ident, ind)].append(rest)
        if name and (ident, ind) not in names:
            names[(ident, ind)] = name

    out = []
    for key in sorted(grouped):
        ring: list[tuple[float, float]] = []
        for seqno, via, lat, lon, olat, olon, dist in grouped[key]:
            if lat is None or lon is None:
                continue
            via = (via or "G ").strip()
            code = via[0] if via else "G"
            if code in ("R", "L") and ring:
                ring.extend(expand_arc(ring[-1], (lat, lon), (olat, olon), dist, code == "R"))
            else:
                ring.append((lat, lon))
        if len(ring) < 4:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        out.append((key[0], key[1], names.get(key, ""), ring))
    return out


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=here / "db.s3db")
    ap.add_argument("--out", type=Path, default=here / "modes_area.geojson")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cycle = (db.execute("select cycle from tbl_hdr_header").fetchone() or ("????",))[0]
    rings = build_rings(db)
    if not rings:
        print("no FIR/UIR records matched -- check PARTICIPATING against the database")
        return 1

    features = [{
        "type": "Feature",
        "properties": {"id": ident, "indicator": ind, "name": name, "cycle": cycle},
        # GeoJSON is [longitude, latitude]; the DFD stores latitude first.
        "geometry": {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in ring]]},
    } for ident, ind, name, ring in rings]

    args.out.write_text(json.dumps(
        {"type": "FeatureCollection",
         "properties": {"source": "Navigraph DFD tbl_uf_fir_uir", "cycle": cycle,
                        "regions": PARTICIPATING},
         "features": features}, indent=1), encoding="utf-8")

    total = sum(len(r[3]) for r in rings)
    lats = [lat for _, _, _, ring in rings for lat, _ in ring]
    lons = [lon for _, _, _, ring in rings for _, lon in ring]
    print(f"AIRAC {cycle}: {len(rings)} FIR/UIR rings, {total:,} boundary points")
    print(f"bbox  lat {min(lats):.2f}..{max(lats):.2f}  lon {min(lons):.2f}..{max(lons):.2f}")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
