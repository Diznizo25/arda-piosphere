"""
Generate species-differentiated piosphere ring polygons for water sources and
upsert them into `piosphere_zones`.

Does the buffering in PostGIS itself (ST_Buffer on ::geography casts real
meters, not degrees) rather than in Python/Shapely — avoids pulling every
water point geometry over the wire and reprojecting client-side.

Radii come from config/species_rings.yaml, never hardcoded.

Usage:
  # one ward (validation gate)
  python scripts/generate_piosphere_zones.py --ward "Oldonyiro"

  # full county (scale-up)
  python scripts/generate_piosphere_zones.py --county "Isiolo"

Requires DATABASE_URL in .env (needs direct PostGIS SQL access, not just the
Supabase REST client).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_species_rings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("generate_piosphere_zones")

UPSERT_SQL = """
insert into piosphere_zones (water_source_id, species, radius_km, geom, last_computed)
select
    ws.id,
    %(species)s,
    %(radius_km)s,
    st_buffer(ws.geom::geography, %(radius_m)s)::geometry(Polygon, 4326),
    now()
from water_sources ws
where (%(ward)s::text is null or ws.ward = %(ward)s)
  and (%(county)s::text is null or ws.county = %(county)s)
on conflict (water_source_id, species)
do update set
    radius_km     = excluded.radius_km,
    geom          = excluded.geom,
    last_computed = excluded.last_computed
"""


def run(ward: str | None, county: str | None) -> None:
    rings = get_species_rings()

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            # sanity check: make sure the scope actually matches rows before we churn
            cur.execute(
                """select count(*) as n from water_sources
                   where (%(ward)s::text is null or ward = %(ward)s)
                     and (%(county)s::text is null or county = %(county)s)""",
                {"ward": ward, "county": county},
            )
            n = cur.fetchone()["n"]
            log.info(f"Scope ward={ward!r} county={county!r}: {n} water_sources in range")
            if n == 0:
                log.warning("No water_sources match this scope — nothing to do. "
                             "Did you run import_water_sources.py for this ward/county first?")
                return

            for species in rings.radii_km:
                radius_km = rings.radius_for(species)
                log.info(f"Buffering species={species} radius_km={radius_km} ...")
                cur.execute(
                    UPSERT_SQL,
                    {
                        "species": species,
                        "radius_km": radius_km,
                        "radius_m": radius_km * 1000,
                        "ward": ward,
                        "county": county,
                    },
                )
                log.info(f"  -> {cur.rowcount} piosphere_zones rows upserted for {species}")
        conn.commit()
    log.info("Done.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ward", help="Restrict to a single ward (validation gate)")
    scope.add_argument("--county", help="Restrict to a full county (scale-up)")
    args = parser.parse_args()
    run(ward=args.ward, county=args.county)


if __name__ == "__main__":
    main()
