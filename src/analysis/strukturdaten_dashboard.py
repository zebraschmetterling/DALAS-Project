"""
Interactive dashboard: structural data of the constituencies vs. the second-vote share of a party.

Reads : data/processed/btw_strukturdaten.csv   (src/preprocessing/btw_strukturdaten.py)
        data/processed/btw_results.csv         (src/preprocessing/btw_results.py, rows with Level == 'constituency')
        data/interim/wahlkreise_karte/wahlkreise_<year>.shp   (src/scraping/wahlkreise_karte.py)
Writes: dashboard/strukturdaten.html   (one self-contained page; Leaflet and Plotly are loaded from the internet)
Run   : python src/analysis/strukturdaten_dashboard.py

Page layout (all views show one election year at a time):
    - Controls: year, party, structural variable (and, for a composition, the part shown).
    - Two linked maps (zoom and pan are synchronised): left = structural variable per constituency,
      right = the party's second-vote share per constituency. Hover shows both values, a click selects the
      constituency in both maps, in the scatter and in the detail panel.
    - Scatter: one point per constituency, x = structural variable, y = party share, coloured East / West,
      with the Pearson r for all constituencies, the West and the East.
    - Compositions ("Age (composition)", "Education (composition)"): the map and the scatter show the selected
      part; the detail panel shows the full composition of the selected constituency, and an extra chart shows
      the average composition of the constituencies grouped into five equal groups (quintiles) by party share.

Choices (also in docs/decisions.md, 2026-11-20):
    - Constituencies are joined on year + constituency number, separately for every election. Numbers are only
      valid within one election, so the selection is cleared when the year changes.
    - Skewed variables (population density, area, GDP) are coloured and plotted on a log10 scale, and their
      Pearson r is computed on the log10 values, i.e. on what the scatter shows.
    - Colour scales run from the 2nd to the 98th percentile of the year, so a few extreme constituencies
      (e.g. inner Berlin, Wolfsburg) do not flatten the scale; values outside get the end colours.
    - "School leavers other qualifications pct" is derived as 100 - without qualification - with Abitur
      (Haupt- and Realschulabschluss), only for the education composition, and labelled as derived.
    - The quintile chart averages the composition weighted by population, because constituencies differ
      in size (193 to 395 thousand inhabitants).
    - Missing values (income and GDP 2013, 6 constituencies of 2017) are drawn grey and left out of r.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

# PARTY_COLORS and clean() are reused from the results map (src/analysis/result_map.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from result_map import PARTY_COLORS, clean  # noqa: E402


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRUKTUR_INPUT = PROJECT_ROOT / "data" / "processed" / "btw_strukturdaten.csv"
RESULTS_INPUT = PROJECT_ROOT / "data" / "processed" / "btw_results.csv"
SHAPES_DIR = PROJECT_ROOT / "data" / "interim" / "wahlkreise_karte"
OUTPUT_FILE = PROJECT_ROOT / "dashboard" / "strukturdaten.html"

SECOND_VOTE_PREFIX = "Second vote "
# "Other" is not a party, so it is not offered in the party selector
NOT_A_PARTY = "Other"
DEFAULT_PARTY = "AfD"

# Coordinates are rounded to 0.0001 degrees (about 10 m) to keep the HTML file small
COORDINATE_PRECISION = 0.0001

DERIVED_EDUCATION = "School leavers other qualifications pct"

# Structural variables offered in the selector: (column, label, group, log scale)
VARIABLES = [
    ("Population density per km2", "Population density (inhabitants per km²)", "Population", True),
    ("Area km2", "Area (km²)", "Population", True),
    ("Population (1000)", "Population (1000)", "Population", False),
    ("Foreigners pct", "Foreigners (%)", "Population", False),
    ("Net migration per 1000 inhabitants", "Net migration (per 1000 inhabitants)", "Population", False),
    ("Age under 18 pct", "Age under 18 (%)", "Age", False),
    ("Age 18-24 pct", "Age 18-24 (%)", "Age", False),
    ("Age 25-34 pct", "Age 25-34 (%)", "Age", False),
    ("Age 35-59 pct", "Age 35-59 (%)", "Age", False),
    ("Age 60-74 pct", "Age 60-74 (%)", "Age", False),
    ("Age 75+ pct", "Age 75+ (%)", "Age", False),
    ("School leavers without qualification pct", "School leavers without qualification (%)", "Education", False),
    ("School leavers with Abitur pct", "School leavers with Abitur (%)", "Education", False),
    (DERIVED_EDUCATION, "School leavers with other qualifications (%, derived)", "Education", False),
    ("Disposable income EUR per inhabitant", "Disposable income (EUR per inhabitant)", "Economy", False),
    ("GDP EUR per inhabitant", "GDP (EUR per inhabitant)", "Economy", True),
    ("Employees per 1000 inhabitants", "Employees (per 1000 inhabitants)", "Labour", False),
    ("Employees in manufacturing pct", "Employees in manufacturing (%)", "Labour", False),
    ("Unemployment rate pct", "Unemployment rate (%)", "Labour", False),
    ("SGB II recipients per 1000 inhabitants", "SGB II recipients (per 1000 inhabitants)", "Labour", False),
]

# Compositions: parts that add up to 100 %, with one colour per part
COMPOSITIONS = {
    "comp:age": {
        "label": "Age (composition)",
        "group": "Age",
        "parts": ["Age under 18 pct", "Age 18-24 pct", "Age 25-34 pct", "Age 35-59 pct", "Age 60-74 pct", "Age 75+ pct"],
        "colors": ["#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"],
    },
    "comp:education": {
        "label": "Education (composition)",
        "group": "Education",
        "parts": ["School leavers without qualification pct", DERIVED_EDUCATION, "School leavers with Abitur pct"],
        "colors": ["#d95f02", "#bdbdbd", "#1b9e77"],
    },
}

DEFAULT_VARIABLE = "Population density per km2"


# --------------------------------------------------------------------------------------
# Building the data
# --------------------------------------------------------------------------------------

def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Read the structural data and the constituency results; return both plus the party labels."""
    struktur = pd.read_csv(STRUKTUR_INPUT)
    # Derived education part (see the docstring): everything between "without" and "Abitur"
    struktur[DERIVED_EDUCATION] = (
        100 - struktur["School leavers without qualification pct"] - struktur["School leavers with Abitur pct"]
    ).round(1)

    results = pd.read_csv(RESULTS_INPUT)
    results = results[results["Level"] == "constituency"].copy()
    results["Constituency number"] = results["Constituency number"].astype(int)
    parties = [
        c[len(SECOND_VOTE_PREFIX):] for c in results.columns
        if c.startswith(SECOND_VOTE_PREFIX) and c != SECOND_VOTE_PREFIX + NOT_A_PARTY
    ]
    return struktur, results, parties


def build_year(year: int, struktur: pd.DataFrame, results: pd.DataFrame, parties: list[str]) -> dict:
    """Join shapes, structural data and results of one election on the constituency number."""
    shapes = gpd.read_file(SHAPES_DIR / f"wahlkreise_{year}.shp")
    shapes["geometry"] = shapely.set_precision(shapes.geometry.values, COORDINATE_PRECISION)

    s_year = struktur[struktur["Date"].str[:4].astype(int) == year].set_index("Constituency number")
    r_year = results[results["Date"].str[:4].astype(int) == year].set_index("Constituency number")

    features, missing = [], []
    for _, shape in shapes.iterrows():
        number = int(shape["WKR_NR"])
        if number not in s_year.index or number not in r_year.index:
            missing.append(number)
            continue
        s, r = s_year.loc[number], r_year.loc[number]
        features.append({
            "type": "Feature",
            "geometry": shape.geometry.__geo_interface__,
            "properties": {
                "number": number,
                "name": clean(s["Constituency"]),
                "state": clean(s["State"]),
                "region": clean(s["Region"]),
                "area_wide": bool(s["Area-wide values"]),
                "population": clean(s["Population (1000)"]),
                "v": {column: clean(s[column]) for column, *_ in VARIABLES},
                "p": {party: clean(r[SECOND_VOTE_PREFIX + party]) for party in parties},
            },
        })

    if missing:
        print(f"WARNING: [{year}] no structural data or results for constituency number(s): {missing}")
    print(f"[{year}] joined {len(features)} constituencies")
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------------------
# HTML page
# --------------------------------------------------------------------------------------

# Placeholders __DATA__, __CONFIG__ are replaced by the script
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Structural data and election results per constituency</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font-family: Arial, sans-serif; color: #222; background: #fff; }
  header { padding: 10px 16px; border-bottom: 1px solid #ccc; }
  h1 { font-size: 18px; margin: 0 0 8px 0; }
  .controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 14px; }
  select { font-size: 14px; padding: 2px; }
  .maps { display: flex; gap: 8px; padding: 8px 16px 0 16px; }
  .panel { flex: 1; min-width: 0; }
  .panel h2 { font-size: 14px; margin: 4px 0; }
  .map { height: 520px; background: #f4f4f4; border: 1px solid #ccc; }
  .legend { font-size: 12px; margin: 4px 0; display: flex; align-items: center; gap: 6px; }
  .ramp { flex: 1; height: 10px; border: 1px solid #999; }
  .missing { display: inline-block; width: 12px; height: 10px; background: #e0e0e0; border: 1px solid #999; }
  #details { margin: 8px 16px; padding: 8px; border: 1px solid #ddd; font-size: 13px; min-height: 38px; }
  .compbar { display: flex; height: 18px; margin-top: 6px; border: 1px solid #999; }
  .compbar div { height: 100%; }
  .complegend { font-size: 12px; margin-top: 4px; }
  .complegend span { display: inline-block; margin-right: 10px; }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 3px; border: 1px solid #888; }
  .charts { padding: 0 16px; }
  #scatter, #quintiles { width: 100%; height: 420px; }
  #notes { margin: 8px 16px 20px 16px; font-size: 12px; color: #555; }
  .muted { color: #666; }
  @media (max-width: 800px) { .maps { flex-direction: column; } .map { height: 380px; } }
</style>
</head>
<body>
<header>
  <h1>Structural data and second-vote share per constituency</h1>
  <div class="controls">
    <label>Year <select id="year"></select></label>
    <label>Party <select id="party"></select></label>
    <label>Structural variable <select id="variable"></select></label>
    <label id="part-label" style="display:none">Part shown <select id="part"></select></label>
  </div>
</header>

<div class="maps">
  <div class="panel">
    <h2 id="left-title"></h2>
    <div id="map-left" class="map"></div>
    <div id="legend-left" class="legend"></div>
  </div>
  <div class="panel">
    <h2 id="right-title"></h2>
    <div id="map-right" class="map"></div>
    <div id="legend-right" class="legend"></div>
  </div>
</div>

<div id="details"></div>

<div class="charts">
  <div id="scatter"></div>
  <div id="quintiles" style="display:none"></div>
</div>

<div id="notes"></div>

<script>
// ---- Data (written by strukturdaten_dashboard.py) ----
const DATA = __DATA__;      // {year: GeoJSON FeatureCollection}
const CONFIG = __CONFIG__;  // variables, compositions, party colours, defaults

const VARS = {};
CONFIG.variables.forEach(v => VARS[v.key] = v);
const MISSING_COLOR = "#e0e0e0";
const REGION_COLORS = { West: "#4c78a8", East: "#e45756" };
// Sequential palette for the structural variable (light -> dark)
const VAR_RAMP = ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"];

// ---- State ----
let year, party, variable, part = null, selected = null;
let layerLeft = null, layerRight = null;
let scaleLeft = null, scaleRight = null;

// ---- Small helpers ----
function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
function rgbToHex(c) { return "#" + c.map(x => Math.round(x).toString(16).padStart(2, "0")).join(""); }
function mix(a, b, t) { const A = hexToRgb(a), B = hexToRgb(b); return rgbToHex(A.map((x, i) => x + (B[i] - x) * t)); }
function rampColor(ramp, t) {
  t = Math.max(0, Math.min(1, t));
  const pos = t * (ramp.length - 1), i = Math.min(Math.floor(pos), ramp.length - 2);
  return mix(ramp[i], ramp[i + 1], pos - i);
}
function quantile(sorted, q) {
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
function pearson(xs, ys) {
  const n = xs.length;
  if (n < 3) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
  return sxy / Math.sqrt(sxx * syy);
}
function fmt(v, digits = 1) { return v === null || v === undefined ? "–" : v.toLocaleString("en", { maximumFractionDigits: digits }); }

// The variable that is actually shown: the variable itself, or the selected part of a composition
function shownVar() { return CONFIG.compositions[variable] ? part : variable; }
function features() { return DATA[year].features; }
function varValue(f) { return f.properties.v[shownVar()]; }
function partyValue(f) { return f.properties.p[party]; }
// Log variables are transformed before colouring, plotting and computing r
function transform(v) { return VARS[shownVar()].log ? Math.log10(v) : v; }

// Colour scale from the 2nd to the 98th percentile of the transformed values of this year
function makeScale(values, ramp) {
  const s = values.filter(v => v !== null && v !== undefined && isFinite(v)).sort((a, b) => a - b);
  if (s.length === 0) return null;
  return { lo: quantile(s, 0.02), hi: quantile(s, 0.98), ramp };
}
function colorFor(scale, v) {
  if (!scale || v === null || v === undefined || !isFinite(v)) return MISSING_COLOR;
  return rampColor(scale.ramp, (v - scale.lo) / ((scale.hi - scale.lo) || 1));
}

// ---- Maps ----
const mapLeft = L.map("map-left", { zoomSnap: 0.25 });
const mapRight = L.map("map-right", { zoomSnap: 0.25, zoomControl: false });
// Synchronise zoom and pan in both directions (the flag prevents an endless loop)
let syncing = false;
function sync(a, b) {
  a.on("move", () => {
    if (syncing) return;
    syncing = true;
    b.setView(a.getCenter(), a.getZoom(), { animate: false });
    syncing = false;
  });
}
sync(mapLeft, mapRight);
sync(mapRight, mapLeft);

function styleLeft(f) {
  const v = varValue(f);
  return styleBase(f, colorFor(scaleLeft, v === null || v === undefined ? null : transform(v)));
}
function styleRight(f) { return styleBase(f, colorFor(scaleRight, partyValue(f))); }
function styleBase(f, fill) {
  const isSel = f.properties.number === selected;
  return { fillColor: fill, fillOpacity: 0.9, color: isSel ? "#ff00ff" : "#ffffff", weight: isSel ? 3 : 0.5 };
}

function tooltip(f) {
  const p = f.properties, v = varValue(f);
  const label = VARS[shownVar()].label;
  const aw = p.area_wide ? '<br><i>Some values are for the whole city/district (area-wide).</i>' : "";
  return `<b>${p.number} ${p.name}</b><br>${p.state} (${p.region})<br>` +
         `${label}: <b>${fmt(v)}</b><br>${party} ${year}: <b>${fmt(partyValue(f), 2)} %</b>${aw}`;
}

function drawLayers(fit) {
  if (layerLeft) mapLeft.removeLayer(layerLeft);
  if (layerRight) mapRight.removeLayer(layerRight);
  const opts = style => ({
    style,
    onEachFeature: (f, lyr) => {
      lyr.bindTooltip(() => tooltip(f), { sticky: true });
      lyr.on("click", () => select(f.properties.number));
    },
  });
  layerLeft = L.geoJSON(DATA[year], opts(styleLeft)).addTo(mapLeft);
  layerRight = L.geoJSON(DATA[year], opts(styleRight)).addTo(mapRight);
  if (fit) mapLeft.fitBounds(layerLeft.getBounds());
}

function legend(id, scale, back, unit) {
  const el = document.getElementById(id);
  if (!scale) { el.innerHTML = '<span class="missing"></span> no data for this year'; return; }
  const grad = `linear-gradient(to right, ${scale.ramp.join(",")})`;
  el.innerHTML = `<span>${fmt(back(scale.lo))}</span><div class="ramp" style="background:${grad}"></div>` +
                 `<span>${fmt(back(scale.hi))}${unit}</span><span class="missing"></span><span class="muted">no data</span>`;
}

function updateMaps() {
  const v = VARS[shownVar()];
  scaleLeft = makeScale(features().map(f => { const x = varValue(f); return x === null ? null : transform(x); }), VAR_RAMP);
  const partyColor = CONFIG.party_colors[party] || "#666666";
  // Light grey -> party colour -> darker party colour, so high shares stand out for light party colours too
  scaleRight = makeScale(features().map(partyValue), ["#f7f7f7", partyColor, mix(partyColor, "#000000", 0.45)]);
  layerLeft.setStyle(styleLeft);
  layerRight.setStyle(styleRight);
  document.getElementById("left-title").textContent = `${v.label}${v.log ? " – log scale" : ""}, ${year}`;
  document.getElementById("right-title").textContent = `${party} second-vote share (%), ${year}`;
  legend("legend-left", scaleLeft, x => v.log ? Math.pow(10, x) : x, "");
  legend("legend-right", scaleRight, x => x, " %");
}

// ---- Selection and detail panel ----
function select(number) {
  selected = number;
  layerLeft.setStyle(styleLeft);
  layerRight.setStyle(styleRight);
  [layerLeft, layerRight].forEach(layer => layer.eachLayer(l => { if (l.feature.properties.number === number) l.bringToFront(); }));
  updateDetails();
  updateScatter();
}

function updateDetails() {
  const el = document.getElementById("details");
  const f = features().find(x => x.properties.number === selected);
  if (!f) { el.innerHTML = '<span class="muted">Click a constituency on a map or a point in the scatter to see its values.</span>'; return; }
  const p = f.properties, comp = CONFIG.compositions[variable];
  let html = `<b>${p.number} ${p.name}</b> – ${p.state} (${p.region}), ${year} | ` +
             `${VARS[shownVar()].label}: <b>${fmt(varValue(f))}</b> | ${party}: <b>${fmt(partyValue(f), 2)} %</b>`;
  if (p.area_wide) html += ' | <i>some values are area-wide (city/district)</i>';
  if (comp) {
    const bar = comp.parts.map((k, i) => {
      const val = p.v[k];
      return val === null ? "" : `<div title="${VARS[k].label}: ${fmt(val)} %" style="width:${val}%;background:${comp.colors[i]}"></div>`;
    }).join("");
    const leg = comp.parts.map((k, i) => `<span><span class="swatch" style="background:${comp.colors[i]}"></span>${VARS[k].label}: ${fmt(p.v[k])}</span>`).join("");
    html += `<div class="compbar">${bar}</div><div class="complegend">${leg}</div>`;
  }
  el.innerHTML = html;
}

// ---- Scatter ----
function updateScatter() {
  const v = VARS[shownVar()];
  const traces = [], rs = {};
  const all = { x: [], y: [] };
  ["West", "East"].forEach(region => {
    const fs = features().filter(f => f.properties.region === region && varValue(f) !== null && partyValue(f) !== null);
    const xs = fs.map(varValue), ys = fs.map(partyValue);
    rs[region] = pearson(xs.map(transform), ys);
    all.x.push(...xs.map(transform)); all.y.push(...ys);
    traces.push({
      type: "scatter", mode: "markers", name: region, x: xs, y: ys,
      customdata: fs.map(f => f.properties.number),
      text: fs.map(f => `${f.properties.number} ${f.properties.name}`),
      hovertemplate: `%{text}<br>${v.label}: %{x}<br>${party}: %{y:.2f} %<extra>${region}</extra>`,
      marker: {
        color: REGION_COLORS[region], opacity: 0.75,
        size: fs.map(f => f.properties.number === selected ? 14 : 7),
        line: { width: fs.map(f => f.properties.number === selected ? 2 : 0), color: "#ff00ff" },
      },
    });
  });
  const rAll = pearson(all.x, all.y);
  const rText = r => r === null ? "–" : r.toFixed(2);
  const title = all.x.length === 0
    ? `${v.label}: no data for ${year}`
    : `Pearson r: all ${rText(rAll)} | West ${rText(rs.West)} | East ${rText(rs.East)}` +
      (v.log ? " (computed on log10 values)" : "");
  Plotly.react("scatter", traces, {
    title: { text: title, font: { size: 14 } },
    xaxis: { title: v.label, type: v.log ? "log" : "linear" },
    yaxis: { title: `${party} second-vote share (%)` },
    margin: { t: 40, r: 10, b: 50, l: 60 }, legend: { orientation: "h", y: 1.08, x: 1, xanchor: "right" },
    template: "plotly_white",
  }, { responsive: true, displayModeBar: false });
}

// ---- Quintile chart (compositions only) ----
function updateQuintiles() {
  const el = document.getElementById("quintiles");
  const comp = CONFIG.compositions[variable];
  if (!comp) { el.style.display = "none"; return; }
  el.style.display = "block";
  // Constituencies with a party share and all parts, sorted by party share, split into five equal groups
  const fs = features()
    .filter(f => partyValue(f) !== null && comp.parts.every(k => f.properties.v[k] !== null))
    .sort((a, b) => partyValue(a) - partyValue(b));
  const groups = [[], [], [], [], []];
  fs.forEach((f, i) => groups[Math.floor(i * 5 / fs.length)].push(f));
  const names = groups.map((g, i) =>
    `Q${i + 1}: ${party} ${fmt(partyValue(g[0]))}–${fmt(partyValue(g[g.length - 1]))} % (${g.length})`);
  // Population-weighted mean of every part per group
  const traces = comp.parts.map((k, j) => ({
    type: "bar", orientation: "h", name: VARS[k].label, y: names,
    x: groups.map(g => {
      const w = g.reduce((s, f) => s + f.properties.population, 0);
      return g.reduce((s, f) => s + f.properties.v[k] * f.properties.population, 0) / w;
    }),
    marker: { color: comp.colors[j] },
    hovertemplate: `%{y}<br>${VARS[k].label}: %{x:.1f} %<extra></extra>`,
  }));
  Plotly.react("quintiles", traces, {
    barmode: "stack",
    title: { text: `Average ${comp.label.toLowerCase()} by ${party} share (quintiles of constituencies, population-weighted), ${year}`, font: { size: 14 } },
    xaxis: { title: "%", range: [0, 100] },
    yaxis: { automargin: true },
    margin: { t: 70, r: 10, b: 50, l: 10 }, legend: { orientation: "h", y: -0.2, traceorder: "normal" },
    template: "plotly_white",
  }, { responsive: true, displayModeBar: false });
}

// ---- Notes ----
function updateNotes() {
  const notes = [
    "Constituencies are compared within one election only: boundaries and numbers change between elections, so the selection is cleared when the year changes.",
    "Colour scales run from the 2nd to the 98th percentile of the year; more extreme constituencies get the end colour. Grey = no data.",
    "Some constituencies of large cities and districts show city- or district-wide values for some variables (marked in the tooltip).",
  ];
  const sv = shownVar();
  if (sv.startsWith("School leavers")) notes.push("Education: shares of the school leavers of one year, not of the adult population – a proxy for the regional education environment. 'Other qualifications' is derived as 100 − without qualification − Abitur.");
  if (sv === "School leavers with Abitur pct" || variable === "comp:education") notes.push("2013: Abitur without Fachhochschulreife; from 2017 including Fachhochschulreife. Do not compare 2013 with later years.");
  if ((sv.startsWith("Disposable") || sv.startsWith("GDP")) && year === "2013") notes.push("Disposable income and GDP are not published in the 2013 structural data.");
  document.getElementById("notes").innerHTML = "<b>Notes</b><ul>" + notes.map(n => `<li>${n}</li>`).join("") + "</ul>" +
    "Sources: Die Bundeswahlleiterin (Strukturdaten, results, constituency boundaries).";
}

// ---- Controls ----
const yearSel = document.getElementById("year"), partySel = document.getElementById("party");
const varSel = document.getElementById("variable"), partSel = document.getElementById("part");

Object.keys(DATA).sort().forEach(y => yearSel.add(new Option(y, y)));
CONFIG.parties.forEach(p => partySel.add(new Option(p, p)));

// Variable selector, grouped; each group lists its composition first
const groups = {};
CONFIG.variables.forEach(v => (groups[v.group] = groups[v.group] || []).push([v.key, v.label]));
Object.entries(CONFIG.compositions).forEach(([key, c]) => groups[c.group].unshift([key, c.label]));
Object.entries(groups).forEach(([g, items]) => {
  const og = document.createElement("optgroup");
  og.label = g;
  items.forEach(([key, label]) => og.appendChild(new Option(label, key)));
  varSel.appendChild(og);
});

function updatePartSelector() {
  const comp = CONFIG.compositions[variable];
  document.getElementById("part-label").style.display = comp ? "" : "none";
  if (!comp) { part = null; return; }
  partSel.innerHTML = "";
  comp.parts.forEach(k => partSel.add(new Option(VARS[k].label, k)));
  part = comp.parts[comp.parts.length - 1];
  partSel.value = part;
}

function refresh() { updateMaps(); updateDetails(); updateScatter(); updateQuintiles(); updateNotes(); }

yearSel.addEventListener("change", () => { year = yearSel.value; selected = null; drawLayers(false); refresh(); });
partySel.addEventListener("change", () => { party = partySel.value; refresh(); });
varSel.addEventListener("change", () => { variable = varSel.value; updatePartSelector(); refresh(); });
partSel.addEventListener("change", () => { part = partSel.value; refresh(); });

// Initial state
year = yearSel.value = Object.keys(DATA).sort().slice(-1)[0];
party = partySel.value = CONFIG.default_party;
variable = varSel.value = CONFIG.default_variable;
updatePartSelector();
drawLayers(true);
refresh();

// A click on a scatter point selects that constituency
document.getElementById("scatter").on("plotly_click", ev => select(ev.points[0].customdata));
</script>
</body>
</html>
"""


def write_html(data: dict[int, dict], parties: list[str]) -> None:
    """Fill the HTML template with the data and the configuration and write it to the output file."""
    config = {
        "variables": [{"key": c, "label": label, "group": group, "log": log} for c, label, group, log in VARIABLES],
        "compositions": COMPOSITIONS,
        "parties": parties,
        "party_colors": PARTY_COLORS,
        "default_party": DEFAULT_PARTY,
        "default_variable": DEFAULT_VARIABLE,
    }
    # "</" is escaped so the JSON can never end the surrounding <script> tag (as in result_map.py)
    data_json = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    config_json = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DATA__", data_json).replace("__CONFIG__", config_json)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> None:
    struktur, results, parties = load_tables()
    years = [int(y) for y in sorted(struktur["Date"].str[:4].astype(int).unique())]
    print(f"Years {years}, parties {parties}")
    data = {str(year): build_year(year, struktur, results, parties) for year in years}
    write_html(data, parties)
    print(f"Wrote {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
