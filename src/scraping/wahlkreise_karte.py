"""
Generated with Claude

Download and prepare the constituency (Wahlkreis) boundaries of the Bundestag elections.

What this script does:
    1. Downloads the generalized constituency shapefiles of the elections 2013, 2017, 2021
       and 2025 from the website of the Bundeswahlleiterin
       (https://www.bundeswahlleiterin.de/bundestagswahlen/<year>/wahlkreiseinteilung/downloads.html)
       and stores the ZIP files in data/raw/wahlkreise_karte/.
       Already downloaded files are reused; set RELOAD = True to download them again.
    2. Extracts the shapefiles and writes one cleaned shapefile per election to
       data/interim/wahlkreise_karte/wahlkreise_<year>.shp (+ .shx, .dbf, .prj, .cpg).

Important notes:
    - The constituency boundaries change between elections, so every year has its own file.
    - All interim files use geographic coordinates (WGS84, EPSG:4326), which web maps expect.
      For 2013 the Bundeswahlleiterin only offers UTM32 coordinates, so that year is reprojected.
    - All interim files have the same columns: WKR_NR, WKR_NAME, LAND_NR, LAND_NAME, geometry.
      Join them with the election results via the constituency number (WKR_NR), not the name.

Requires: geopandas, certifi
"""

import re
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import certifi
import geopandas as gpd


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Set to True to download all files again, even if they already exist in data/raw
RELOAD = False

# Project root is two levels above this file (src/scraping/wahlkreise_karte.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "wahlkreise_karte"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim" / "wahlkreise_karte"

# Download page of the Bundeswahlleiterin, {year} is replaced by the election year
DOWNLOAD_PAGE = "https://www.bundeswahlleiterin.de/bundestagswahlen/{year}/wahlkreiseinteilung/downloads.html"

# File name of the generalized shapefile ZIP on the download page of each election.
# The download links contain a changing ID, so the link is looked up on the page by this name.
# Files with "vg250" / "vg1000" in their name are the non-generalized versions and are not used.
SHAPEFILE_NAMES = {
    2013: "btw13_geometrie_wahlkreise_etrs89_geo_shp.zip",  # only available in UTM32
    2017: "btw17_geometrie_wahlkreise_geo_shp.zip",
    2021: "btw21_geometrie_wahlkreise_geo_shp.zip",
    2025: "btw25_geometrie_wahlkreise_shp_geo.zip",
}

# The 2013 file has no .cpg file, its texts are encoded in Windows-1252
SOURCE_ENCODINGS = {2013: "cp1252"}

# Columns kept in the interim files (in this order)
COLUMNS = ["WKR_NR", "WKR_NAME", "LAND_NR", "LAND_NAME", "geometry"]

# Coordinate reference system of the interim files (WGS84, longitude / latitude)
TARGET_CRS = "EPSG:4326"

# Some servers reject requests without a browser-like user agent
HEADERS = {"User-Agent": "Mozilla/5.0 (btw-data-pipeline)"}

# Trust the system certificates plus the certifi bundle. On Windows, Python only sees root
# certificates that are already in the Windows store, which may lack the D-Trust root used
# by bundeswahlleiterin.de (CERTIFICATE_VERIFY_FAILED). Certificate verification stays enabled.
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.load_verify_locations(cafile=certifi.where())


# --------------------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------------------

def fetch(url: str) -> bytes:
    """Download a URL and return its content."""
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
        return response.read()


def find_download_url(year: int) -> str:
    """Look up the absolute download URL of the generalized shapefile on the download page."""
    page_url = DOWNLOAD_PAGE.format(year=year)
    page = fetch(page_url).decode("utf-8", errors="replace")

    # Search all links for the expected file name (the link path in front of it contains an ID)
    file_name = SHAPEFILE_NAMES[year]
    for href in re.findall(r'href="([^"]+)"', page):
        if href.endswith("/" + file_name):
            return urljoin(page_url, href)

    raise RuntimeError(f"Could not find '{file_name}' on {page_url}")


def download(year: int) -> Path:
    """Download the shapefile ZIP of one election into data/raw (skipped if present and RELOAD is False)."""
    zip_path = RAW_DIR / f"wahlkreise_{year}.zip"

    if zip_path.exists() and not RELOAD:
        print(f"[{year}] Using existing download {zip_path.relative_to(PROJECT_ROOT)}")
        return zip_path

    url = find_download_url(year)
    print(f"[{year}] Downloading {url}")
    content = fetch(url)

    # Write to a temporary file first, so an aborted download never leaves a broken ZIP behind
    temp_path = zip_path.with_suffix(".zip.part")
    temp_path.write_bytes(content)
    if not zipfile.is_zipfile(temp_path):
        temp_path.unlink()
        raise RuntimeError(f"[{year}] Downloaded file is not a valid ZIP archive: {url}")
    temp_path.replace(zip_path)

    print(f"[{year}] Saved {zip_path.relative_to(PROJECT_ROOT)} ({len(content) / 1024:.0f} kB)")
    return zip_path


# --------------------------------------------------------------------------------------
# Extraction and cleaning
# --------------------------------------------------------------------------------------

def extract(year: int, zip_path: Path) -> Path:
    """Read the shapefile from the ZIP, clean it and write it to data/interim."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Unpack the archive; besides .shp/.shx/.dbf/.prj it contains files that are not
        # needed later (.sbn/.sbx spatial index, .shp.xml metadata), which are discarded here
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temp_dir)

        shapefiles = list(Path(temp_dir).rglob("*.shp"))
        if len(shapefiles) != 1:
            raise RuntimeError(f"[{year}] Expected exactly one .shp file in {zip_path.name}, found {len(shapefiles)}")

        constituencies = gpd.read_file(shapefiles[0], encoding=SOURCE_ENCODINGS.get(year))

    # Bring every year to the same coordinate system (only 2013 actually needs a conversion)
    if constituencies.crs is None:
        raise RuntimeError(f"[{year}] Shapefile has no coordinate reference system (.prj missing)")
    if constituencies.crs != TARGET_CRS:
        print(f"[{year}] Reprojecting from {constituencies.crs.name} to WGS84")
        constituencies = constituencies.to_crs(TARGET_CRS)

    # Keep only the common columns in a fixed order and use consistent data types
    missing = set(COLUMNS) - set(constituencies.columns)
    if missing:
        raise RuntimeError(f"[{year}] Missing columns in shapefile: {sorted(missing)}")
    constituencies = constituencies[COLUMNS].copy()
    constituencies["WKR_NR"] = constituencies["WKR_NR"].astype(int)
    constituencies["LAND_NR"] = constituencies["LAND_NR"].astype(str).str.zfill(2)
    constituencies = constituencies.sort_values("WKR_NR").reset_index(drop=True)

    # Write the cleaned shapefile (UTF-8, geopandas also writes the .cpg file for the encoding)
    output_path = INTERIM_DIR / f"wahlkreise_{year}.shp"
    constituencies.to_file(output_path, encoding="utf-8")

    print(f"[{year}] Wrote {output_path.relative_to(PROJECT_ROOT)} ({len(constituencies)} constituencies)")
    return output_path


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> None:
    print(f"RELOAD = {RELOAD}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Start with an empty interim folder, so no outdated files remain
    if INTERIM_DIR.exists():
        shutil.rmtree(INTERIM_DIR)
    INTERIM_DIR.mkdir(parents=True)

    for year in SHAPEFILE_NAMES:
        zip_path = download(year)
        extract(year, zip_path)

    print("Done.")


if __name__ == "__main__":
    main()
