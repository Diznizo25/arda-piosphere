"""
Earth Engine index computation, kept as pure functions over ee.Image/ee.ImageCollection
so gee_compute_export.py stays a thin orchestrator.

Domain context (see CLAUDE_CODE_PROMPT.md "Satellite indices" section — read that
before touching this file): standard greenness indices (NDVI/NDRE/EVI/SAVI) read
cured, dry rangeland grass as low, indistinguishable from bare soil. SATVI is the
primary signal for standing dry forage; BSI is a secondary cross-check; VCI
normalizes against seasonal history so "low NDVI in dry season" isn't misread as
a problem. All bands below are computed and stacked — none are discarded because
they "look bare" under NDVI alone. That interpretation happens in
app/services/advisory_logic.py, not here.

Sentinel-2 SR bands used (10-20m native resolution):
  B2 blue, B3 green, B4 red, B5 red-edge 1, B8 NIR, B8A narrow NIR,
  B11 SWIR1, B12 SWIR2
"""
from __future__ import annotations

import ee

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
SATVI_L = 0.5  # soil-adjustment factor, same convention as SAVI


def mask_s2_clouds(image: ee.Image) -> ee.Image:
    """QA60 bitmask cloud/cirrus mask for Sentinel-2 SR."""
    qa = image.select("QA60")
    cloud_bit = 1 << 10
    cirrus_bit = 1 << 11
    mask = (
        qa.bitwiseAnd(cloud_bit).eq(0)
        .And(qa.bitwiseAnd(cirrus_bit).eq(0))
    )
    return image.updateMask(mask).divide(10000).copyProperties(image, ["system:time_start"])


def get_s2_composite(region: ee.Geometry, start_date: str, end_date: str) -> ee.Image:
    """Median composite over the window, cloud-masked. Callers pass a
    piosphere zone's outer (camel) ring as `region` — never a whole ward."""
    coll = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        .map(mask_s2_clouds)
    )
    return coll.median().clip(region)


def compute_indices(img: ee.Image) -> ee.Image:
    """Stack NDVI, NDRE, SATVI, BSI, NDMI, NDWI as named bands on one image."""
    ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndre = img.normalizedDifference(["B8", "B5"]).rename("NDRE")

    satvi = (
        img.expression(
            "((SWIR1 - RED) / (SWIR1 + RED + L)) * (1 + L) - (SWIR2 / 2)",
            {
                "SWIR1": img.select("B11"),
                "SWIR2": img.select("B12"),
                "RED": img.select("B4"),
                "L": SATVI_L,
            },
        ).rename("SATVI")
    )

    bsi = (
        img.expression(
            "((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))",
            {
                "SWIR1": img.select("B11"),
                "RED": img.select("B4"),
                "NIR": img.select("B8"),
                "BLUE": img.select("B2"),
            },
        ).rename("BSI")
    )

    # NDMI (Gao's NDWI): vegetation moisture / curing-stage proxy
    ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
    # NDWI (McFeeters): open-water / surface-wetness signal, distinct from NDMI
    ndwi = img.normalizedDifference(["B3", "B8"]).rename("NDWI")

    return ee.Image.cat([ndvi, ndre, satvi, bsi, ndmi, ndwi])


def compute_vci(current_ndvi: ee.Image, region: ee.Geometry, center_date: ee.Date,
                 years_back: int = 5, window_days: int = 15) -> ee.Image:
    """Vegetation Condition Index: normalizes current NDVI against the
    historical min/max NDVI for this same time-of-year window, so seasonally-
    normal dry-season lows aren't reported as abnormal."""
    doy = center_date.getRelative("day", "year")

    def year_offset_image(i):
        i = ee.Number(i)
        yr_date = center_date.advance(ee.Number(-1).multiply(i), "year")
        start = yr_date.advance(-window_days, "day")
        end = yr_date.advance(window_days, "day")
        composite = get_s2_composite(region, start.format(), end.format())
        return composite.normalizedDifference(["B8", "B4"]).rename("NDVI")

    years = ee.List.sequence(1, years_back)
    hist_coll = ee.ImageCollection(years.map(year_offset_image))

    ndvi_min = hist_coll.min()
    ndvi_max = hist_coll.max()

    vci = (
        current_ndvi.subtract(ndvi_min)
        .divide(ndvi_max.subtract(ndvi_min).max(1e-6))
        .multiply(100)
        .rename("VCI")
        .clamp(0, 100)
    )
    return vci


def get_jrc_gsw_monthly_recurrence(month: int) -> ee.Image:
    """JRC Global Surface Water monthly recurrence for the water-source layer:
    how reliably a point historically holds water in this calendar month."""
    coll = ee.ImageCollection("JRC/GSW1_4/MonthlyRecurrence")
    img = coll.filter(ee.Filter.eq("month", month)).first()
    return img.select("monthly_recurrence").rename("GSW_MONTHLY_RECURRENCE")


def build_stacked_image_for_month(region: ee.Geometry, as_of_date: str, month: int,
                                   composite_window_days: int = 30,
                                   vci_years_back: int = 5) -> ee.Image:
    """Full stack for one water point's outer (camel) ring: current-window
    indices + VCI + JRC GSW monthly recurrence, all as float32 bands of one
    image ready to export as a COG. `month` (1-12) is passed explicitly by the
    orchestrator so no client-side .getInfo() round-trip is needed mid-build."""
    end = ee.Date(as_of_date)
    start = end.advance(-composite_window_days, "day")

    composite = get_s2_composite(region, start.format(), end.format())
    indices = compute_indices(composite)
    vci = compute_vci(indices.select("NDVI"), region, end, years_back=vci_years_back)
    gsw = get_jrc_gsw_monthly_recurrence(month)

    return ee.Image.cat([indices, vci, gsw]).clip(region).toFloat()
