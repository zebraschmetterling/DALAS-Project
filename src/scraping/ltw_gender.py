"""
ltw_gender.py - Vote choice by gender in German state elections
===============================================================

What this script does
---------------------
1. Scrapes the election analysis overview of Forschungsgruppe Wahlen
   (https://www.forschungsgruppe.de/Wahlen/Wahlanalysen/) and downloads the
   "Kurzanalyse" PDF of every state election (Landtagswahl, including the
   Abgeordnetenhaus election in Berlin and the Bürgerschaft elections in
   Hamburg and Bremen) from the most recent one back to and including 2013
   (configurable via --min-year). PDFs are stored in
   ../../data/raw/Kurzanalysen_ForschergruppeWahlen and are only downloaded if
   the file does not exist yet.

2. Extracts the chart "Wahlentscheidung nach Geschlecht" (vote choice by
   gender) from every downloaded PDF and writes one tidy table to
   ../../data/interim/ltw_gender.csv with the columns
       Bundesland | Datum | Geschlecht | CDU/CSU | SPD | Grüne | FDP | Linke
       | AfD | FW | BSW | SSW | <other named parties> | Sonstige
   A party column is only included if that party has a printed value in at
   least one extracted chart (since 2013 by default), so there are no empty
   party columns.
   PDFs that do not contain this chart are skipped (only 12 of 44 state
   election newsletters since 2013 show it).

Important notes
---------------
* The layout of the newsletters differs between elections: the chart can be in
  the left or right page column, the party order, colours and set of parties
  change per state, bar segments are drawn as rectangles or as curves, and
  very small segments carry no number label. Reading the numbers in text order
  would therefore shift values to the wrong party. Instead, every number is
  assigned to the coloured bar segment it is printed on, and the colour is
  mapped to a party via the chart legend.
* Some PDFs have layout quirks that are handled explicitly: legend squares in a
  slightly different shade than the bars (Linke in Brandenburg, Sachsen and
  Thüringen 2014), number labels of tiny segments staggered above/below the bar
  (e.g. Rheinland-Pfalz 2026), and labels printed twice on top of each other
  (2016 PDFs).
* CSU is reported as CDU/CSU and "Freie Wähler" (also BVB/FW) as FW. Parties
  that are named in a legend but are not in the list above (e.g. "NPD"
  in Sachsen 2014, "BIW" in Bremen 2015) get their own column, placed before
  "Sonstige".
* Values are percentages as printed in the PDF. Segments that are too small to
  carry a label, and parties that are not shown in a chart, are left empty.
* The "Wahlergebnis" row (official overall result shown as reference bar in
  each chart) is no voter group and is therefore dropped.
* The row labels are kept as printed ("männlich", "weiblich").
* Newsletters that cover several states in one PDF (only before 2013) are not
  supported and are skipped with a warning.
* The PDFs are the same for ltw_education.py, ltw_gender.py and ltw_age.py, so
  each file is downloaded only once, whichever script runs first.
* Paths are resolved relative to this file, so the script can be started from
  any working directory:  python src/scraping/ltw_gender.py

Dependencies: requests, beautifulsoup4, pdfplumber, pandas
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_URL = "https://www.forschungsgruppe.de/Wahlen/Wahlanalysen/"

SCRIPT_DIR = Path(__file__).resolve().parent
RAW_DIR = (SCRIPT_DIR / "../../data/raw/Kurzanalysen_ForschergruppeWahlen").resolve()
INTERIM_DIR = (SCRIPT_DIR / "../../data/interim").resolve()

# Chart extracted by this script and the resulting table.
CHART_TITLE = "Wahlentscheidung nach Geschlecht"
CHART_TITLE_REGEX = r"Wahlentscheidung\s+nach\s+Geschlecht"
GROUP_COLUMN = "Geschlecht"
OUTPUT_CSV = INTERIM_DIR / "ltw_gender.csv"

# Row labels are kept as printed ("männlich", "weiblich").
GROUP_ALIASES: dict[str, str] = {}

# Oldest election year that is downloaded and parsed (inclusive).
DEFAULT_MIN_YEAR = 2013

# The 16 German states as written on the overview page.
STATES = [
    "Baden-Württemberg",
    "Bayern",
    "Berlin",
    "Brandenburg",
    "Bremen",
    "Hamburg",
    "Hessen",
    "Mecklenburg-Vorpommern",
    "Niedersachsen",
    "Nordrhein-Westfalen",
    "Rheinland-Pfalz",
    "Saarland",
    "Sachsen",
    "Sachsen-Anhalt",
    "Schleswig-Holstein",
    "Thüringen",
]

# Overview rows that mention a state but are no state election.
NON_STATE_ELECTION_REGEX = re.compile(r"Bundestagswahl|Europawahl", re.IGNORECASE)


def state_slug(state: str) -> str:
    """ASCII-only version of a state name for file names (e.g. 'Thueringen')."""
    for umlaut, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        state = state.replace(umlaut, replacement)
    return state


# File names encode state and year, so existing files can be recognised
# without the website (e.g. LTW_Baden-Wuerttemberg_2021_Kurzanalyse.pdf).
PDF_NAME_TEMPLATE = "LTW_{slug}_{year}_Kurzanalyse.pdf"
PDF_NAME_REGEX = re.compile(r"^LTW_([A-Za-z-]+)_(\d{4})_Kurzanalyse\.pdf$")
STATE_BY_SLUG = {state_slug(s): s for s in STATES}

# Party columns in output order. A column is only written if the party has a
# printed value in at least one extracted chart; other named parties are
# inserted before "Sonstige".
PARTY_COLUMNS = ["CDU/CSU", "SPD", "Grüne", "FDP", "Linke", "AfD", "FW", "BSW", "SSW"]
OTHER_COLUMN = "Sonstige"

# Maps the (normalised, upper-case, whitespace-free) legend labels used in the
# different PDFs onto the canonical column names.
PARTY_ALIASES = {
    "CDU/CSU": "CDU/CSU",
    "CDU": "CDU/CSU",
    "CSU": "CDU/CSU",
    "UNION": "CDU/CSU",
    "SPD": "SPD",
    "GRÜNE": "Grüne",
    "GRUENE": "Grüne",
    "B90/GRÜNE": "Grüne",
    "BÜNDNIS90/DIEGRÜNE": "Grüne",
    "FDP": "FDP",
    "LINKE": "Linke",
    "DIELINKE": "Linke",
    "LINKE.PDS": "Linke",
    "PDS": "Linke",
    "AFD": "AfD",
    "FW": "FW",
    "FREIEWÄHLER": "FW",
    "BVB/FW": "FW",
    "BVB/FREIEWÄHLER": "FW",
    "BSW": "BSW",
    "SSW": "SSW",
    "SONSTIGE": OTHER_COLUMN,
    "ANDERE": OTHER_COLUMN,
}

# Chart rows that are no voter group (overall result used as reference bar).
EXCLUDED_GROUPS = {"Wahlergebnis"}

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ltw-gender-scraper/1.0)"}
HTTP_TIMEOUT = 30

# Geometry thresholds in PDF points (1/72 inch).
LEGEND_SQUARE_MAX_SIZE = 7.0  # legend colour squares are ~4.7 pt wide/high
BAR_MIN_HEIGHT = 8.0  # bar segments are ~13-15 pt high
BAR_MAX_HEIGHT = 26.0  # bars are at most ~22 pt high; the grey title box is 30 pt
ROW_TOP_TOLERANCE = 4.0  # segments whose tops differ less belong to one bar
COLOR_TOLERANCE = 0.03  # max. difference per RGB channel for "same colour"
# Some PDFs (e.g. Brandenburg 2014) draw a legend square in a slightly different
# shade than the bars; such pairs are matched up to this per-channel difference.
COLOR_FALLBACK_TOLERANCE = 0.25
# Max. distance between a number label and the bar segment it belongs to, used
# when labels of small neighbouring segments are staggered above/below the bar.
LABEL_MAX_OFFSET = 5.0
CHART_MAX_HEIGHT = 220.0  # fallback chart height if no source line is found

# A bar whose printed values (all segments labelled) do not add up to roughly
# 100 % indicates a parsing problem and is reported.
ROW_SUM_TOLERANCE = 3.0


# --------------------------------------------------------------------------
# Part 1: Download
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Election:
    """One state election listed on the overview page."""

    state: str
    year: int
    url: str

    @property
    def filename(self) -> str:
        return PDF_NAME_TEMPLATE.format(slug=state_slug(self.state), year=self.year)


def states_in(text: str) -> list[str]:
    """Return all state names mentioned in `text`.

    Word boundaries that also exclude hyphens make sure that "Sachsen" does not
    match inside "Sachsen-Anhalt" or "Niedersachsen".
    """
    return [s for s in STATES if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", text)]


def find_ltw_pdf_links(min_year: int) -> list[Election]:
    """Return all state elections >= min_year that have a Kurzanalyse PDF.

    The overview page contains one table row per election: the first cell holds
    the election name (e.g. "Landtagswahl Hessen 2023", "Bürgerschaftswahl
    Bremen 2023", "Wahl zum Abgeordnetenhaus Berlin 2026"), the second cell the
    link labelled "Kurzanalyse".
    """
    response = requests.get(BASE_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    elections: dict[tuple[str, int], Election] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        name = " ".join(cells[0].get_text(" ", strip=True).split())
        year_match = re.search(r"(\d{4})\s*$", name)
        if not year_match or NON_STATE_ELECTION_REGEX.search(name):
            continue
        year = int(year_match.group(1))
        if year < min_year:
            continue

        states = states_in(name)
        if not states:
            continue  # e.g. "Dreifachwahl März 2016" (summary of several elections)
        if len(states) > 1:
            print(f"  [warn] '{name}': newsletter covers several states, skipped")
            continue

        # Only the link explicitly called "Kurzanalyse" is used (some rows only
        # link to zdf.de graphics or to a summary document).
        pdf_links = [
            a
            for a in row.find_all("a", href=True)
            if ".pdf" in a["href"].lower() and "kurzanalyse" in a.get_text(strip=True).lower()
        ]
        if not pdf_links:
            print(f"  [warn] '{name}': no Kurzanalyse PDF on the overview page")
            continue

        # Strip session ids such as ";jsessionid=..." that are sometimes appended.
        href = pdf_links[0]["href"].split(";")[0]
        key = (states[0], year)
        if key in elections:
            print(f"  [warn] '{name}': second election in {states[0]} {year} ignored")
            continue
        elections[key] = Election(states[0], year, urljoin(BASE_URL, href))

    return sorted(elections.values(), key=lambda e: (-e.year, e.state))


def download_pdf(url: str, target: Path) -> None:
    """Download a PDF to `target` (via a temporary file, so aborted downloads leave no broken PDF)."""
    response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Response from {url} is not a PDF")

    tmp_path = target.with_suffix(target.suffix + ".part")
    tmp_path.write_bytes(response.content)
    tmp_path.replace(target)


def download_kurzanalysen(min_year: int) -> None:
    """Download all missing Kurzanalyse PDFs of state elections >= min_year."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching election overview: {BASE_URL}")

    try:
        elections = find_ltw_pdf_links(min_year)
    except requests.RequestException as exc:
        # Without the website we can still parse PDFs that were downloaded earlier.
        print(f"  [warn] Could not load overview page ({exc}); using existing files only.")
        return

    if not elections:
        print("  [warn] No state election PDFs found on the overview page.")
        return

    for election in elections:
        label = f"{election.state} {election.year}"
        target = RAW_DIR / election.filename
        if target.exists():
            print(f"  [skip] {label}: {target.name} already exists")
            continue
        try:
            download_pdf(election.url, target)
            print(f"  [done] {label}: downloaded {election.url} -> {target.name}")
        except (requests.RequestException, ValueError) as exc:
            print(f"  [error] {label}: download of {election.url} failed ({exc})")


# --------------------------------------------------------------------------
# Part 2: Extraction
# --------------------------------------------------------------------------


@dataclass
class Box:
    """Axis-aligned rectangle in PDF page coordinates (top < bottom)."""

    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    def contains_x(self, x: float, tol: float = 0.0) -> bool:
        return self.x0 - tol <= x <= self.x1 + tol

    def contains(self, x: float, y: float, tol: float = 0.0) -> bool:
        return self.contains_x(x, tol) and self.top - tol <= y <= self.bottom + tol


@dataclass
class Segment(Box):
    """One coloured part of a stacked bar."""

    party: str = ""


@dataclass
class BarRow(Box):
    """One horizontal bar (= one voter group) of the chart."""

    segments: list[Segment] = field(default_factory=list)


def to_rgb(color) -> tuple[float, float, float] | None:
    """Normalise a pdfplumber colour (gray, RGB or CMYK) to an RGB tuple."""
    if color is None or isinstance(color, str):
        return None
    if isinstance(color, (int, float)):
        color = (color,)
    color = tuple(float(c) for c in color if isinstance(c, (int, float)))
    if len(color) == 1:
        return (color[0],) * 3
    if len(color) == 3:
        return color  # type: ignore[return-value]
    if len(color) == 4:
        c, m, y, k = color
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def same_color(a, b) -> bool:
    return a is not None and b is not None and all(abs(x - y) <= COLOR_TOLERANCE for x, y in zip(a, b))


def is_white(color) -> bool:
    return same_color(color, (1.0, 1.0, 1.0))


def normalise_party(label: str) -> str | None:
    """Map a legend label to its column name.

    Known labels (e.g. 'GRÜNE', 'CSU', 'Freie Wähler') are mapped to the
    canonical column; any other named party keeps its printed label so that it
    gets a column of its own. Returns None for empty labels.
    """
    label = " ".join(label.split())
    if not label:
        return None
    key = label.replace(" ", "").upper()
    return PARTY_ALIASES.get(key, label)


def parse_number(text: str) -> float | None:
    """Convert German number strings such as '16,4' to floats."""
    text = text.strip()
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return None
    return float(text.replace(",", "."))


def filled_shapes(page) -> list[tuple[Box, tuple[float, float, float] | None]]:
    """All filled rectangles and closed curves of a page with their fill colour.

    Some PDFs draw individual bar segments as curves instead of rectangles, so
    both object types are considered via their bounding box.
    """
    shapes = []
    for obj in list(page.rects) + list(page.curves):
        if not obj.get("fill"):
            continue
        box = Box(obj["x0"], obj["x1"], obj["top"], obj["bottom"])
        shapes.append((box, to_rgb(obj.get("non_stroking_color"))))
    return shapes


def locate_chart(pdf, title_regex: str) -> tuple[object, Box] | None:
    """Find the page and the bounding box of the chart with the given title.

    Returns None if the PDF does not contain the chart. The box spans the page
    column of the title, from the title down to the chart's source line
    ("Forschungsgruppe Wahlen: Befragung am Wahltag ...").
    """
    for page in pdf.pages:
        hits = page.search(title_regex, regex=True, case=False)
        if not hits:
            continue
        title = hits[0]

        # The newsletters use a two-column layout; the chart lives in the title's column.
        half = page.width / 2
        if title["x0"] < half:
            x0, x1 = 0.0, half
        else:
            x0, x1 = half, float(page.width)

        # The chart ends at its source line (or at the next chart title).
        bottom = min(title["bottom"] + CHART_MAX_HEIGHT, float(page.height))
        for word in page.extract_words():
            if (
                word["top"] > title["bottom"] + 20
                and x0 <= word["x0"] < x1
                and word["text"] in ("Forschungsgruppe", "Wahlentscheidung")
            ):
                bottom = min(bottom, word["bottom"] + 1)
        return page, Box(x0, x1, title["top"], bottom)

    return None


def read_legend(shapes, words, chart: Box, first_bar_top: float) -> list[tuple[tuple, str]]:
    """Return [(rgb, party)] from the small colour squares of the legend.

    Each legend entry is a small coloured square followed by the party name;
    the text between one square and the next is taken as the label (this also
    covers multi-word labels such as "Freie Wähler").
    """
    squares = [
        (box, rgb)
        for box, rgb in shapes
        if rgb is not None
        and box.x1 - box.x0 <= LEGEND_SQUARE_MAX_SIZE
        and box.bottom - box.top <= LEGEND_SQUARE_MAX_SIZE
        and chart.contains(box.cx, box.cy)
        and box.bottom <= first_bar_top
    ]
    squares.sort(key=lambda s: (round(s[0].top), s[0].x0))

    legend = []
    for i, (box, rgb) in enumerate(squares):
        next_x0 = chart.x1
        if i + 1 < len(squares) and abs(squares[i + 1][0].cy - box.cy) < 3:
            next_x0 = squares[i + 1][0].x0
        label_words = [
            w["text"]
            for w in words
            if box.x1 - 1 <= w["x0"] < next_x0 and abs((w["top"] + w["bottom"]) / 2 - box.cy) < 5
        ]
        party = normalise_party(" ".join(label_words))
        if party is None:
            print("    [warn] Legend square without label ignored")
            continue
        if party in (p for _, p in legend):
            print(f"    [warn] Party '{party}' appears twice in the legend, second entry ignored")
            continue
        legend.append((rgb, party))
    return legend


def color_distance(a, b) -> float:
    """Largest difference of the RGB channels of two colours."""
    return max(abs(x - y) for x, y in zip(a, b))


def match_bar_colors(bar_colors: list[tuple], legend) -> dict[tuple, str]:
    """Map every bar colour to a party of the legend.

    Colours are matched exactly (within COLOR_TOLERANCE) first. Legend entries
    without any exact match are then paired with the closest remaining bar
    colour, if it is within COLOR_FALLBACK_TOLERANCE.
    """
    mapping: dict[tuple, str] = {}
    for rgb in bar_colors:
        party = next((p for c, p in legend if same_color(c, rgb)), None)
        if party is not None:
            mapping[rgb] = party

    unmatched_colors = [c for c in bar_colors if c not in mapping]
    for legend_rgb, party in legend:
        if party in mapping.values() or not unmatched_colors:
            continue
        closest = min(unmatched_colors, key=lambda c: color_distance(c, legend_rgb))
        if color_distance(closest, legend_rgb) <= COLOR_FALLBACK_TOLERANCE:
            mapping[closest] = party
            unmatched_colors.remove(closest)
            print(f"    [info] Bar colour {tuple(round(x, 2) for x in closest)} matched to legend entry '{party}' (closest shade)")
    return mapping


def build_bar_rows(shapes, chart: Box, legend) -> list[BarRow]:
    """Group legend-coloured bar segments into horizontal bars (one per row)."""
    # Candidate segments: bar-sized, coloured shapes inside the chart.
    candidates = [
        (box, rgb)
        for box, rgb in shapes
        if BAR_MIN_HEIGHT <= box.bottom - box.top <= BAR_MAX_HEIGHT
        and chart.contains(box.cx, box.cy)
        and rgb is not None
        and not is_white(rgb)
    ]
    bar_colors = list(dict.fromkeys(rgb for _, rgb in candidates))
    party_by_color = match_bar_colors(bar_colors, legend)

    segments = []
    for box, rgb in candidates:
        party = party_by_color.get(rgb)
        if party is not None:
            segments.append(Segment(box.x0, box.x1, box.top, box.bottom, party=party))

    rows: list[BarRow] = []
    for seg in sorted(segments, key=lambda s: (s.top, s.x0)):
        if rows and abs(rows[-1].top - seg.top) < ROW_TOP_TOLERANCE:
            row = rows[-1]
            row.segments.append(seg)
            row.x0, row.x1 = min(row.x0, seg.x0), max(row.x1, seg.x1)
            row.bottom = max(row.bottom, seg.bottom)
        else:
            rows.append(BarRow(seg.x0, seg.x1, seg.top, seg.bottom, segments=[seg]))
    return rows


def read_row_values(row: BarRow, chars, white_boxes: list[Box]) -> dict[str, float]:
    """Read the numbers printed on one bar and assign them to parties.

    Numbers are printed on small white label boxes. Grouping characters by
    label box splits labels that touch each other (pdfplumber would otherwise
    merge e.g. '8,8' '4,97' '4,6' into one word). Characters without a label
    box are grouped by their horizontal distance.
    """
    row_chars = sorted(
        (
            c
            for c in chars
            if row.top <= (c["top"] + c["bottom"]) / 2 <= row.bottom
            and row.contains_x((c["x0"] + c["x1"]) / 2, tol=3)
            and c["text"] in "0123456789,."
        ),
        key=lambda c: c["x0"],
    )
    row_boxes = [b for b in white_boxes if row.contains(b.cx, b.cy)]

    def box_index(char) -> int | None:
        cx, cy = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
        return next((i for i, b in enumerate(row_boxes) if b.contains(cx, cy)), None)

    # Build number tokens: [[chars...], ...]
    tokens: list[list[dict]] = []
    prev_box: int | None = None
    for char in row_chars:
        idx = box_index(char)
        if tokens:
            prev = tokens[-1][-1]
            same_box = idx is not None and idx == prev_box
            touching = idx is None and prev_box is None and char["x0"] - prev["x1"] < 1.0
            if same_box or touching:
                tokens[-1].append(char)
                continue
        tokens.append([char])
        prev_box = idx

    values: dict[str, float] = {}
    for token in tokens:
        text = "".join(c["text"] for c in token)
        value = parse_number(text)
        if value is None:
            continue
        cx = (token[0]["x0"] + token[-1]["x1"]) / 2

        # Segment under the label; if the label sticks out, take the nearest segment.
        segment = next((s for s in row.segments if s.contains_x(cx)), None)
        if segment is None:
            segment = min(row.segments, key=lambda s: segment_distance(s, cx))
        if segment.party in values:
            # Staggered labels of tiny neighbouring segments can be centred just
            # beyond their own segment; use the closest segment still without a value.
            free = [s for s in row.segments if s.party not in values]
            nearest = min(free, key=lambda s: segment_distance(s, cx), default=None)
            if nearest is None or segment_distance(nearest, cx) > LABEL_MAX_OFFSET:
                print(f"    [warn] Second value {text} for {segment.party} ignored")
                continue
            segment = nearest
        values[segment.party] = value
    return values


def segment_distance(segment: Segment, x: float) -> float:
    """Horizontal distance of x from a segment (0 if x lies inside it)."""
    return max(segment.x0 - x, 0.0, x - segment.x1)


def read_row_label(row: BarRow, rows: list[BarRow], words, aliases: dict[str, str]) -> str:
    """Return the group label printed left of a bar.

    Labels can span two lines ("Hauptschul-" / "abschluss"), so every word left
    of the bars is assigned to the vertically closest bar.
    """
    bars_x0 = min(r.x0 for r in rows)
    label_words = []
    for w in words:
        if w["x1"] > bars_x0 + 1:
            continue
        cy = (w["top"] + w["bottom"]) / 2
        if not (rows[0].top - 6 <= cy <= rows[-1].bottom + 6):
            continue
        nearest = min(rows, key=lambda r: abs(r.cy - cy))
        if nearest is row:
            label_words.append(w)

    label_words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    label = " ".join(w["text"] for w in label_words)
    label = re.sub(r"-\s+(?=[a-zäöü])", "", label)  # join hyphenated line breaks
    label = " ".join(label.split())
    return aliases.get(label, label)


def read_election_date(page, chart: Box, pdf, year: int) -> str:
    """Return the election date (ISO format) from the chart's source line.

    The source line reads e.g. "Befragung am Wahltag in Bayern, 08.10.2023".
    Falls back to the whole document text and finally to the year only.
    """
    candidates = [page.crop((chart.x0, chart.top, chart.x1, chart.bottom)).extract_text() or ""]
    candidates.append("\n".join(p.extract_text() or "" for p in pdf.pages))
    for text in candidates:
        for day, month, yyyy in re.findall(r"Wahltag[^\d]{0,40}(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
            if int(yyyy) == year:
                return f"{yyyy}-{int(month):02d}-{int(day):02d}"
    print(f"    [warn] No election date found in the PDF, using '{year}'")
    return f"{year}"


def extract_chart_table(pdf_path: Path, state: str, year: int) -> tuple[pd.DataFrame, list[str]] | None:
    """Extract the chart CHART_TITLE from one PDF.

    Returns (table, parties in legend order) or None if the chart is missing.
    """
    with pdfplumber.open(pdf_path) as pdf:
        located = locate_chart(pdf, CHART_TITLE_REGEX)
        if located is None:
            print(f"  [skip] {pdf_path.name}: no '{CHART_TITLE}' chart")
            return None
        page, chart = located
        # Some PDFs (e.g. Berlin 2016) print labels twice on top of each other
        # ("WWaahhlleerrggeebbnniiss"); drop such duplicate characters.
        page = page.dedupe_chars()

        shapes = filled_shapes(page)
        words = [
            w for w in page.extract_words() if chart.contains((w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2)
        ]

        # The legend sits above the first bar; bars are the tall shapes in the chart.
        tall = [b for b, rgb in shapes if b.bottom - b.top >= BAR_MIN_HEIGHT and chart.contains(b.cx, b.cy)]
        legend_limit = min((b.top for b in tall if b.top > chart.top + 15), default=chart.bottom)
        legend = read_legend(shapes, words, chart, legend_limit)
        if not legend:
            print(f"  [skip] {pdf_path.name}: chart found, but no legend could be read")
            return None

        rows = build_bar_rows(shapes, chart, legend)
        if not rows:
            print(f"  [skip] {pdf_path.name}: chart found, but no bars could be read")
            return None

        white_boxes = [b for b, rgb in shapes if is_white(rgb) and b.bottom - b.top < BAR_MIN_HEIGHT * 2]
        date = read_election_date(page, chart, pdf, year)

        records = []
        for row in rows:
            # All rows are still needed above to assign two-line labels to the right bar.
            label = read_row_label(row, rows, words, GROUP_ALIASES)
            if label in EXCLUDED_GROUPS:
                continue
            values = read_row_values(row, page.chars, white_boxes)

            missing = [s.party for s in row.segments if s.party not in values]
            if missing:
                print(f"    [info] {state} {year} / {label}: no printed value for {', '.join(missing)} (left empty)")
            elif abs(sum(values.values()) - 100) > ROW_SUM_TOLERANCE:
                # Every segment has a value, so the bar should add up to ~100 %.
                print(f"    [warn] {state} {year} / {label}: values add up to {sum(values.values()):g} %")

            records.append({"Bundesland": state, "Datum": date, GROUP_COLUMN: label, **values})

    parties = [p for _, p in legend]
    extra = [p for p in parties if p not in PARTY_COLUMNS and p != OTHER_COLUMN]
    note = f" (additional column: {', '.join(extra)})" if extra else ""
    print(f"  [done] {pdf_path.name}: {len(records)} rows, date {date}, parties: {', '.join(parties)}{note}")
    return pd.DataFrame.from_records(records), parties


def output_columns(parties: list[str]) -> list[str]:
    """Columns for the given parties: known parties in schema order, then other
    named parties in order of first appearance, then 'Sonstige'."""
    known = [p for p in PARTY_COLUMNS if p in parties]
    extra = [p for p in parties if p not in PARTY_COLUMNS and p != OTHER_COLUMN]
    other = [OTHER_COLUMN] if OTHER_COLUMN in parties else []
    return ["Bundesland", "Datum", GROUP_COLUMN, *known, *extra, *other]


def build_chart_csv(min_year: int) -> pd.DataFrame:
    """Parse all downloaded state election PDFs (>= min_year) and write the combined CSV."""
    print(f"Extracting '{CHART_TITLE}' charts from {RAW_DIR}")

    # Collect the downloaded PDFs; state and year are taken from the file name.
    pdfs = []
    for path in RAW_DIR.glob("LTW_*.pdf"):
        match = PDF_NAME_REGEX.match(path.name)
        if not match or match.group(1) not in STATE_BY_SLUG:
            continue
        year = int(match.group(2))
        if year >= min_year:
            pdfs.append((year, STATE_BY_SLUG[match.group(1)], path))

    frames = []
    seen_parties: list[str] = []
    for year, state, path in sorted(pdfs):
        try:
            result = extract_chart_table(path, state, year)
        except Exception as exc:  # a single broken PDF must not stop the whole run
            print(f"  [error] {path.name}: extraction failed ({exc})")
            continue
        if result is None:
            continue
        frame, parties = result
        frames.append(frame)
        # Remember every party named in a legend, in order of first appearance.
        for party in parties:
            if party not in seen_parties:
                seen_parties.append(party)

    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    # Parties that are named in a legend but never carry a printed number
    # (e.g. BIW in the Bremen 2015 gender chart) would only give empty columns.
    with_values = [p for p in seen_parties if p in table.columns and table[p].notna().any()]
    for party in seen_parties:
        if party not in with_values:
            print(f"  [info] '{party}' has no printed value in any chart, column omitted")
    table = table.reindex(columns=output_columns(with_values))
    if not table.empty:
        table = table.sort_values(["Datum", "Bundesland"], kind="stable", ignore_index=True)

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUTPUT_CSV, index=False, float_format="%g", encoding="utf-8")
    print(f"Saved {len(table)} rows from {len(frames)} elections to {OUTPUT_CSV}")
    return table


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    """Command line entry point: download missing PDFs, then extract the chart."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--min-year",
        type=int,
        default=DEFAULT_MIN_YEAR,
        help=f"oldest state election to include (default: {DEFAULT_MIN_YEAR})",
    )
    args = parser.parse_args()

    download_kurzanalysen(args.min_year)
    table = build_chart_csv(args.min_year)
    if not table.empty:
        print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
