"""
Generated with Claude

Interactive map of the German federal election (Bundestagswahl) results per constituency.

What this script does:
    1. Reads the processed election results   data/processed/btw_results.csv
       (created by btw_results.py, only the rows with Level == 'constituency' are used)
    2. Reads the constituency boundaries      data/interim/wahlkreise_karte/wahlkreise_<year>.shp
       (created by wahlkreise_karte.py, one shapefile per election year)
    3. Joins both on the constituency number (WKR_NR in the shapefile = "Constituency number"
       in the results), separately for every election year.
    4. Writes ONE self-contained HTML page to data/processed/results_map.html with:
         - a dropdown to select the election year (2013, 2017, 2021, 2025)
         - a switch between first votes and second votes
         - a map in which every constituency is coloured by the party with the most votes
         - a click on a constituency shows its exact results (first and second votes of all
           parties, voter turnout, direct mandate) in the side panel
         - as long as no constituency is clicked, the side panel shows the nationwide results
         - a dropdown to select a federal state, which shows the results of that state
           (including whether it belongs to the West or the East)

Important notes:
    - The nationwide and state results are taken from the 'federal' and 'state' rows of
      btw_results.csv. The West / East classification comes from get_bundesland_classification
      in src/preprocessing/labels.py.
    - Party names are used exactly as they appear in btw_results.csv (labels from labels.py).
    - The party "Other" is not a single party, so it is never coloured as the winner.
      Its share is still shown in the results table.
    - The map is drawn with Leaflet, which is loaded from the internet (unpkg.com) when the
      HTML page is opened, so an internet connection is needed to view the page.

Requires: pandas, geopandas (and labels.py from src/preprocessing)
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Project root is two levels above this file (src/Analysis/results_map.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESULTS_INPUT = PROJECT_ROOT / "data" / "processed" / "btw_results.csv"
SHAPES_DIR = PROJECT_ROOT / "data" / "interim" / "wahlkreise_karte"
OUTPUT_FILE = PROJECT_ROOT / "dashboard" / "results_map.html"

# labels.py lives in src/preprocessing, so that folder is added to the import path
# (it is the sibling folder of the folder this script is in, e.g. src/analysis -> src/preprocessing)
PREPROCESSING_DIR = Path(__file__).resolve().parent.parent / "preprocessing"
if not (PREPROCESSING_DIR / "labels.py").exists():
    raise FileNotFoundError(f"labels.py not found in {PREPROCESSING_DIR}")
sys.path.insert(0, str(PREPROCESSING_DIR))
from labels import get_bundesland_classification  # noqa: E402

# Display names for the West / East classification returned by labels.py
REGION_NAMES = {"W": "West", "E": "East"}

# Prefixes of the vote columns in btw_results.csv
FIRST_VOTE_PREFIX = "First vote "
SECOND_VOTE_PREFIX = "Second vote "

# Label that is not a real party and therefore never wins a constituency on the map
NOT_A_PARTY = "Other"

# Colours of the party labels (labels as defined in labels.py); unknown labels are grey
PARTY_COLORS = {
    "CDU": "#000000",
    "CSU": "#4a90d9",
    "SPD": "#e3000f",
    "Greens": "#46962b",
    "Linke": "#be3075",
    "FDP": "#ffed00",
    "AfD": "#009ee0",
    "BSW": "#7d2a68",
    "Other": "#999999",
}
DEFAULT_COLOR = "#cccccc"


# --------------------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------------------

def get_parties(results: pd.DataFrame) -> list[str]:
    """Return all party labels that appear in the vote columns, in column order."""
    parties: list[str] = []
    for column in results.columns:
        for prefix in (FIRST_VOTE_PREFIX, SECOND_VOTE_PREFIX):
            if column.startswith(prefix):
                party = column[len(prefix):]
                if party not in parties:
                    parties.append(party)
    return parties


def clean(value: object) -> object:
    """Turn missing values into None (-> null in JSON) and numpy numbers into plain Python."""
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def get_shares(row: pd.Series, prefix: str, parties: list[str]) -> dict[str, float | None]:
    """Collect the vote shares of all parties of one constituency for one vote type."""
    return {party: clean(row.get(prefix + party)) for party in parties}


def get_winner(shares: dict[str, float | None]) -> str | None:
    """Return the party with the highest share (ignoring 'Other' and missing values)."""
    candidates = {p: v for p, v in shares.items() if v is not None and p != NOT_A_PARTY}
    if not candidates:
        return None
    return max(candidates, key=candidates.get)


def build_area(row: pd.Series, name: str, region: str | None, parties: list[str]) -> dict:
    """Collect turnout and vote shares of a 'federal' or 'state' row."""
    return {
        "name": name,
        "region": region,
        "turnout": clean(row["Voter turnout"]),
        "first": get_shares(row, FIRST_VOTE_PREFIX, parties),
        "second": get_shares(row, SECOND_VOTE_PREFIX, parties),
    }


# --------------------------------------------------------------------------------------
# Building the data for one election
# --------------------------------------------------------------------------------------

def build_year(year: int, results: pd.DataFrame, parties: list[str]) -> dict:
    """Join the shapes and results of one election year; also collect the federal and state results."""
    shape_file = SHAPES_DIR / f"wahlkreise_{year}.shp"
    if not shape_file.exists():
        raise FileNotFoundError(f"Shapefile for {year} not found: {shape_file}")

    shapes = gpd.read_file(shape_file)

    # Results of this election year (all levels)
    year_all = results[results["Date"].str[:4].astype(int) == year]

    # Constituency results, indexed by the constituency number
    year_results = year_all[year_all["Level"] == "constituency"]
    year_results = year_results.set_index(year_results["Constituency number"].astype(int))

    features = []
    missing = []
    for _, shape in shapes.iterrows():
        number = int(shape["WKR_NR"])

        # Join on the constituency number
        if number not in year_results.index:
            missing.append(number)
            continue
        row = year_results.loc[number]

        first = get_shares(row, FIRST_VOTE_PREFIX, parties)
        second = get_shares(row, SECOND_VOTE_PREFIX, parties)

        features.append({
            "type": "Feature",
            "geometry": shape.geometry.__geo_interface__,
            "properties": {
                "number": number,
                "name": clean(row["Constituency"]),
                "state": clean(row["State"]),
                # West / East classification derived from the state of the constituency
                "region": REGION_NAMES.get(get_bundesland_classification(clean(row["State"]))),
                "turnout": clean(row["Voter turnout"]),
                "direct_mandate": clean(row["Direct mandate"]),
                "first": first,
                "second": second,
                "winner_first": get_winner(first),
                "winner_second": get_winner(second),
            },
        })

    if missing:
        print(f"WARNING: [{year}] No results found for constituency number(s): {missing}")

    unmatched = set(year_results.index) - set(shapes["WKR_NR"].astype(int))
    if unmatched:
        print(f"WARNING: [{year}] No shape found for constituency number(s): {sorted(unmatched)}")

    print(f"[{year}] Joined {len(features)} constituencies")

    # Nationwide result
    federal_rows = year_all[year_all["Level"] == "federal"]
    federal = build_area(federal_rows.iloc[0], "Germany", None, parties) if len(federal_rows) else None
    if federal is None:
        print(f"WARNING: [{year}] No federal result found")

    # State results including the West / East classification
    states = {}
    for _, row in year_all[year_all["Level"] == "state"].iterrows():
        name = row["State"]
        region = REGION_NAMES.get(get_bundesland_classification(name))
        if region is None:
            print(f"WARNING: [{year}] No West / East classification for state '{name}'")
        states[name] = build_area(row, name, region, parties)

    return {
        "map": {"type": "FeatureCollection", "features": features},
        "federal": federal,
        "states": states,
    }


# --------------------------------------------------------------------------------------
# HTML page
# --------------------------------------------------------------------------------------

# The placeholders __DATA__, __COLORS__ and __DEFAULT_COLOR__ are replaced by the script
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bundestag election results per constituency</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { height: 100%; margin: 0; font-family: Arial, sans-serif; }
  #app { display: flex; height: 100%; }
  #map { flex: 1; background: #f4f4f4; }
  #side { width: 340px; padding: 14px; overflow-y: auto; border-left: 1px solid #ccc; box-sizing: border-box; }
  h1 { font-size: 18px; margin: 0 0 12px 0; }
  .control { margin-bottom: 10px; }
  .control label { margin-right: 10px; }
  select { font-size: 14px; padding: 2px; }
  #legend { margin: 12px 0; font-size: 13px; }
  .legend-item { display: flex; align-items: center; margin: 2px 0; }
  .swatch { width: 14px; height: 14px; margin-right: 6px; border: 1px solid #888; }
  #details h2 { font-size: 16px; margin: 12px 0 4px 0; }
  #details table { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 6px; }
  #details th, #details td { padding: 3px 4px; text-align: right; border-bottom: 1px solid #e0e0e0; }
  #details th:first-child, #details td:first-child { text-align: left; }
  #details tr.selected-vote td { font-weight: bold; }
  .muted { color: #666; font-size: 13px; }
</style>
</head>
<body>
<div id="app">
  <div id="map"></div>
  <div id="side">
    <h1>Bundestag election results</h1>
    <div class="control">
      <label for="year">Year:</label>
      <select id="year"></select>
    </div>
    <div class="control">
      <label for="area">State:</label>
      <select id="area"></select>
    </div>
    <div class="control">
      <label><input type="radio" name="vote" value="first" checked> First votes</label>
      <label><input type="radio" name="vote" value="second"> Second votes</label>
    </div>
    <div class="muted">Colour = party with the most votes in the constituency.</div>
    <div id="legend"></div>
    <div id="details"></div>
  </div>
</div>

<script>
// ---- Data (written by results_map.py) ----
const DATA = __DATA__;            // {year: {map: GeoJSON, federal: {...}, states: {name: {...}}}}
const COLORS = __COLORS__;        // {party label: colour}
const DEFAULT_COLOR = "__DEFAULT_COLOR__";

// ---- State ----
let currentYear = null;
let currentVote = "first";
let layer = null;
let selectedNumber = null;
let currentArea = "";             // "" = nationwide, otherwise the name of a federal state

const map = L.map("map", { zoomSnap: 0.25 });

function colorOf(party) {
  return party ? (COLORS[party] || DEFAULT_COLOR) : DEFAULT_COLOR;
}

function winnerOf(feature) {
  return currentVote === "first" ? feature.properties.winner_first : feature.properties.winner_second;
}

function styleOf(feature) {
  const selected = feature.properties.number === selectedNumber;
  return {
    fillColor: colorOf(winnerOf(feature)),
    fillOpacity: 0.85,
    color: selected ? "#ff00ff" : "#ffffff",
    weight: selected ? 3 : 0.6,
  };
}

// Legend: only the parties that win at least one constituency in the current view
function updateLegend() {
  const counts = {};
  DATA[currentYear].map.features.forEach(f => {
    const w = winnerOf(f);
    if (w) counts[w] = (counts[w] || 0) + 1;
  });
  const html = Object.keys(counts)
    .sort((a, b) => counts[b] - counts[a])
    .map(p => `<div class="legend-item"><span class="swatch" style="background:${colorOf(p)}"></span>${p} (${counts[p]})</div>`)
    .join("");
  document.getElementById("legend").innerHTML = html;
}

function fmt(value) {
  return value === null || value === undefined ? "-" : value.toFixed(2).replace(".", ",") + " %";
}

// Results table (first and second votes of all parties), sorted by the selected vote type
function resultsTable(first, second) {
  const parties = Object.keys(first);
  const current = currentVote === "first" ? first : second;
  parties.sort((a, b) => (current[b] ?? -1) - (current[a] ?? -1));

  const rows = parties.map(party => `<tr>
      <td><span class="swatch" style="background:${colorOf(party)};display:inline-block"></span> ${party}</td>
      <td class="${currentVote === "first" ? "sel" : ""}">${fmt(first[party])}</td>
      <td class="${currentVote === "second" ? "sel" : ""}">${fmt(second[party])}</td>
    </tr>`).join("");

  return `<table>
      <tr><th>Party</th><th>First votes</th><th>Second votes</th></tr>
      ${rows}
    </table>`;
}

// Side panel with the exact results of the selected constituency
function showDetails(feature) {
  const p = feature.properties;
  document.getElementById("details").innerHTML = `
    <h2>${p.number} - ${p.name}</h2>
    <div class="muted">${p.state ?? ""}${p.region ? ` (${p.region})` : ""} | ${currentYear}</div>
    <div style="margin-top:6px">Voter turnout: <b>${fmt(p.turnout)}</b></div>
    <div>Direct mandate: <b>${p.direct_mandate ?? "-"}</b></div>
    ${resultsTable(p.first, p.second)}`;
}

// Side panel with the results of the selected state or, by default, of the whole country
function showArea() {
  const year = DATA[currentYear];
  const area = currentArea === "" ? year.federal : year.states[currentArea];
  if (!area) {
    document.getElementById("details").innerHTML = '<p class="muted">No results available.</p>';
    return;
  }
  const region = area.region ? ` (${area.region})` : "";
  document.getElementById("details").innerHTML = `
    <h2>${area.name}${region}</h2>
    <div class="muted">${currentYear}</div>
    <div style="margin-top:6px">Voter turnout: <b>${fmt(area.turnout)}</b></div>
    ${resultsTable(area.first, area.second)}
    <p class="muted">Click on a constituency to see its results.</p>`;
}

function drawYear(fitBounds) {
  if (layer) map.removeLayer(layer);
  layer = L.geoJSON(DATA[currentYear].map, {
    style: styleOf,
    onEachFeature: (feature, lyr) => {
      lyr.bindTooltip(`${feature.properties.number} ${feature.properties.name}`, { sticky: true });
      lyr.on("click", () => {
        selectedNumber = feature.properties.number;
        layer.setStyle(styleOf);
        lyr.bringToFront();
        showDetails(feature);
      });
    },
  }).addTo(map);
  if (fitBounds) map.fitBounds(layer.getBounds());
  updateLegend();

  // Keep the side panel in sync if the selected constituency exists in this year
  const selected = DATA[currentYear].map.features.find(f => f.properties.number === selectedNumber);
  if (selected) {
    showDetails(selected);
  } else {
    selectedNumber = null;
    showArea();
  }
}

// ---- Controls ----
const yearSelect = document.getElementById("year");
Object.keys(DATA).sort().forEach(y => {
  const option = document.createElement("option");
  option.value = y;
  option.textContent = y;
  yearSelect.appendChild(option);
});
currentYear = yearSelect.value = Object.keys(DATA).sort().slice(-1)[0];

// State dropdown: first entry = nationwide results
const areaSelect = document.getElementById("area");
areaSelect.innerHTML = '<option value="">Germany (nationwide)</option>' +
  Object.keys(DATA[currentYear].states).sort()
    .map(name => `<option value="${name}">${name}</option>`).join("");

// Choosing a state (or Germany) removes the constituency selection and shows that area
areaSelect.addEventListener("change", () => {
  currentArea = areaSelect.value;
  selectedNumber = null;
  layer.setStyle(styleOf);
  showArea();
});

yearSelect.addEventListener("change", () => {
  currentYear = yearSelect.value;
  drawYear(false);
});
document.querySelectorAll('input[name="vote"]').forEach(radio => {
  radio.addEventListener("change", () => {
    currentVote = radio.value;
    drawYear(false);
  });
});

drawYear(true);
</script>
</body>
</html>
"""


def write_html(data: dict[int, dict]) -> None:
    """Fill the HTML template with the map data and write it to the output file."""
    # "</" is escaped so the JSON can never end the surrounding <script> tag
    data_json = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    html = (
        HTML_TEMPLATE
        .replace("__DATA__", data_json)
        .replace("__COLORS__", json.dumps(PARTY_COLORS))
        .replace("__DEFAULT_COLOR__", DEFAULT_COLOR)
    )
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> None:
    print(f"Reading results from {RESULTS_INPUT}")
    results = pd.read_csv(RESULTS_INPUT, dtype={"Constituency number": str})

    # The years are taken from the constituency rows (they are needed for the map)
    constituencies = results[results["Level"] == "constituency"]
    years = [int(year) for year in sorted(constituencies["Date"].str[:4].astype(int).unique())]
    parties = get_parties(results)
    print(f"Found {len(years)} election years {years} and {len(parties)} parties: {', '.join(parties)}")

    # Join shapes and results for every year
    data = {year: build_year(year, results, parties) for year in years}

    write_html(data)
    print(f"Wrote {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()