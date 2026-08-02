# Ward / county boundaries needed here

`scripts/import_water_sources.py` and `scripts/generate_piosphere_zones.py` (for
ward-scoped runs) need a GeoJSON Polygon/MultiPolygon (EPSG:4326) per admin unit.
None are bundled yet. Options, cheapest first:

1. **IEBC/HDX Kenya administrative boundaries** — the Humanitarian Data Exchange
   hosts Kenya admin boundary shapefiles (county + ward/constituency level) from
   IEBC. Search "Kenya Administrative Boundaries" on data.humdata.org, download
   the ward-level shapefile, filter to Isiolo county, convert to GeoJSON
   (`ogr2ogr -f GeoJSON isiolo_wards.geojson kenya_wards.shp -where "COUNTY='Isiolo'"`).
2. **GADM** (gadm.org) — has Kenya level-3 boundaries but these are constituencies
   in some releases, not always wards 1:1. Check against IEBC ward list before use.
3. **Manual digitize** — for the single validation ward (day 1-4 gate), it's
   often faster to trace the one ward boundary in a tool like geojson.io against
   an OSM basemap than to wrangle a full shapefile.

Drop the files here as `<ward_name>.geojson` (one Polygon/MultiPolygon Feature)
and `isiolo_county.geojson` for the full-county scale-up run.

**This repo does not include these files yet — tell me which ward to validate
on first and I'll fetch/build its boundary, or share a file and I'll wire it in.**
