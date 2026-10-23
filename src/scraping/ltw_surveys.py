"""
Scrape Landtag (state parliament) polls from wahlrecht.de into one table.

What this script does
----------------------
1. Reads the overview at https://www.wahlrecht.de/umfragen/landtage/ and
   downloads one HTML page per Bundesland into data/raw/ltw_surveys.
   Set REFRESH_HTML to False to skip the download and reuse those files,
   so the script can run offline.
2. Parses every poll table on those pages. Column layouts differ by state
   and by time period (older tables have no AfD or BSW column, Hamburg used
   to list the Greens as GAL, Brandenburg lists Freie Waehler as BVB/FW).
3. Writes data/interim/ltw_surveys.csv with the columns
   Bundesland, Institut, Datum, CDU/CSU, SPD, Grüne, FDP, Linke, AfD, FW,
   BSW, then one column per other party that exceeds PARTY_COLUMN_ABOVE
   in at least one saved poll (for example SSW), then Sonstige, Befragte
   and Zeitraum.
4. SURVEYS_FROM is the first publication date that is written to the CSV
   (inclusive). None keeps every poll. A partial date such as "Juni 2013"
   or "??.03.2013" is kept when it could still fall on or after that day.
   Only those saved polls decide which extra party columns are created.

Value rules
-----------
- A party that is not reported in a poll is left blank. A dash on the
  website means "not shown", so it is stored as an empty cell, not as 0.
- CDU and CSU share the CDU/CSU column. GAL (Hamburg) is counted as Gruene.
  BVB/FW is counted as FW.
- A party outside the fixed columns gets its own column when it is above
  5 % in at least one saved poll. SSW is the typical case. The same party
  keeps one column across spellings (PIR and PIRATEN, BIW and BIW/BD,
  Schill and Offensive (Ex-Schill)). Baskets such as "Rechte" stay in
  Sonstige, and so does every named party that never clears 5 %.
- When the site nests parties inside the Sonstige cell ("FW 4 % /
  Sonst. 6 %", "BSW 2 % / BP 1 % / Sonst. 5 %"), FW, BSW and any party
  with its own column are moved out and the rest is summed.
- Official election results (Landtagswahl, Abgeordnetenhauswahl,
  Buergerschaftswahl) are not polls and are skipped.
- Struck-through percentages are old figures; the replacement value is kept.
- Befragte is the sample size. The fieldwork window, which the site prints
  in the same cell, is stored in Zeitraum.

Usage
-----
    python src/scraping/ltw_surveys.py

Paths below are resolved from this file, i.e. ../../data/... from
src/scraping/ltw_surveys.py.
"""

import calendar
import re
import time
import unicodedata
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pandas as pd
from bs4 import BeautifulSoup


# Set True to download the state pages again.
# Set False to parse the HTML already stored in data/raw/ltw_surveys
# (the script then runs without a network connection).
REFRESH_HTML = True

# First publication date to write into the CSV, inclusive.
# "YYYY-MM-DD" or "DD.MM.YYYY", for example "2013-02-06".
# None keeps every poll found on the state pages.
SURVEYS_FROM = "2013-01-01"

# A party without a fixed column gets its own column when its share is
# above this percentage in at least one poll that is actually saved.
# 5 means "more than 5 %", so a party that only ever reaches 5 % stays
# inside Sonstige.
PARTY_COLUMN_ABOVE = 5

# Month names used when a publication date is only "Juni 2007" or "Sept. 2004".
_MONTH_NUMBERS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "marz": 3,
    "maerz": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}

OVERVIEW_URL = "https://www.wahlrecht.de/umfragen/landtage/"
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "ltw_surveys"
OUTPUT_CSV = ROOT / "data" / "interim" / "ltw_surveys.csv"

# Output schema. "Gruene" is written as "Grüne" — the umlaut is the column
# name requested for the table, not a transliteration. Further party
# columns are inserted before Sonstige while the table is built.
PARTY_COLUMNS = ["CDU/CSU", "SPD", "Grüne", "FDP", "Linke", "AfD", "FW", "BSW"]
BASE_COLUMNS = [
    "Bundesland",
    "Institut",
    "Datum",
    *PARTY_COLUMNS,
    "Sonstige",
    "Befragte",
    "Zeitraum",
]

# Header and in-cell labels, after norm_key(), that belong to a fixed column.
# Anything else (PIRATEN, NPD, SSW, BIW, REP, ...) falls into Sonstige.
CANONICAL_PARTIES = {
    "cdu": "CDU/CSU",
    "csu": "CDU/CSU",
    "cdu/csu": "CDU/CSU",
    "spd": "SPD",
    "gruene": "Grüne",
    "gal": "Grüne",  # Gruen-Alternative Liste, the Hamburg Greens
    "b90": "Grüne",
    "b'90": "Grüne",
    "buendnis 90": "Grüne",
    "buendnis 90/die gruenen": "Grüne",
    "b90/gruene": "Grüne",
    "fdp": "FDP",
    "linke": "Linke",
    "die linke": "Linke",
    "pds": "Linke",
    "linke.pds": "Linke",
    "die linke.pds": "Linke",
    "afd": "AfD",
    "fw": "FW",
    "freie waehler": "FW",
    "bvb/fw": "FW",  # Freie Waehler list in Brandenburg
    "bsw": "BSW",
}

# Labels that mean "the remainder", not a named party.
RESIDUAL_LABELS = {"sonst", "sonstige", "son", "andere"}

# Baskets of several parties. They stay in Sonstige even above the cutoff.
GROUP_LABELS = {"rechte", "rechtsradik", "rechtsradikal"}

# Different labels the site uses for one and the same party.
PARTY_ALIASES = {
    "pir": "PIRATEN",
    "piraten": "PIRATEN",
    "pp": "PIRATEN",
    "ssw": "SSW",
    "npd": "NPD",
    "rep": "REP",
    "dvu": "DVU",
    "wasg": "WASG",
    "biw": "BIW/BD",
    "bd": "BIW/BD",
    "biw/bd": "BIW/BD",
    "bd/biw": "BIW/BD",
    "schill": "Schill",
    "offensive (ex-schill)": "Schill",
    # PRO is the Schill party. "Pro DM Schill" is a separate list and stays
    # under its own name, so it is not aliased here.
    "pro": "Schill",
    "oedp": "ödp",
    "odp": "ödp",
    "volt": "Volt",
    "bp": "BP",
    "bayernpartei": "BP",
    "familie": "FAMILIE",
    "graue": "GRAUE",
    "statt": "STATT",
    "tierschutzpartei": "Tierschutzpartei",
    "tierschutzp": "Tierschutzpartei",
    "tp": "Tierschutzpartei",
    "tsp": "Tierschutzpartei",
    "freie sachsen": "FS",
    "fs": "FS",
    "bfth": "BfTh",
}

# Rows whose visible text contains one of these are election results.
ELECTION_RE = re.compile(
    r"landtagswahl|abgeordnetenhauswahl|b[uü]rgerschaftswahl|"
    r"bundestagswahl|wahlergebnis",
    re.IGNORECASE,
)

# "BSW 2 %" / "38–39 %" / "4,5" / "Sonst. 5 %". The percent sign is optional
# because a few older cells omit it.
VALUE_RE = re.compile(
    r"""
    (?P<label>
        [A-Za-zÄÖÜäöüß]
        [A-Za-zÄÖÜäöüß0-9./'’+\-]*
        (?:[ \t]+[A-Za-zÄÖÜäöüß(][A-Za-zÄÖÜäöüß0-9./'’+\-)]*){0,4}
    )?
    [ \t]*
    (?P<value>
        \d{1,3}(?:,\d+)?
        (?:[ \t]*[–\-−][ \t]*\d{1,3}(?:,\d+)?)?
    )
    [ \t]*%?
    """,
    re.VERBOSE,
)

# Sample size: "1.207", "750", "~500", "≈1.000". Not a date fragment like
# "12.10" and not a year standing next to a month name ("Oktober 2007").
COUNT_RE = re.compile(
    r"(?:ca\.?\s*|≈\s*|~\s*)?(?:\d{1,3}(?:\.\d{3})+|\d{2,6})(?!\s*[.\-–]\d)"
)

USER_AGENT = "Mozilla/5.0 (compatible; ltw-surveys/1.0; research)"


def norm_key(text):
    """Lower-case label with umlauts folded, for dictionary lookups."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00ad", "").replace("\xa0", " ")
    text = text.casefold()
    text = (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def clean_space(text):
    """Collapse whitespace, including non-breaking spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00ad", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def join_wrapped_lines(text):
    """Join a cell that the site breaks across lines.

    "Forschungs-\\ngruppe Wahlen" is one word. "Infratest\\ndimap" is two.
    A trailing "*" is a footnote marker, not part of the name.
    """
    text = unicodedata.normalize("NFKC", text or "").replace("\u00ad", "")
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    merged = lines[0]
    for nxt in lines[1:]:
        # Hyphen at a line break is syllable hyphenation when the next
        # line continues in lower case ("Forschungs-" + "gruppe").
        if merged.endswith("-") and nxt[:1].islower():
            merged = merged[:-1] + nxt
        elif merged.endswith("/"):
            merged = merged + nxt
        else:
            merged = merged + " " + nxt
    merged = re.sub(r"\s+", " ", merged).strip()
    merged = re.sub(r"\s*\*+\s*$", "", merged).strip()
    return merged


def is_blank_value(text):
    """True for dashes and question marks, which mean 'not reported'."""
    token = clean_space(text).rstrip("%").strip()
    return token.casefold() in {"", "-", "–", "—", "−", "?", "na", "n.a.", "k.a."}


def party_name(label):
    """Stable name for one party, or None when the label is not a party.

    Fixed parties and known aliases share one name. "Rechte" is a basket
    of parties, so it has no name of its own.
    """
    key = norm_key(label)
    if not key or key in RESIDUAL_LABELS or key in GROUP_LABELS:
        return None
    if key in CANONICAL_PARTIES:
        return CANONICAL_PARTIES[key]
    if key in PARTY_ALIASES:
        return PARTY_ALIASES[key]
    return clean_space(label).strip(".")


def canonical_party(label, default):
    """Map a party label onto a column. Baskets and leftovers go to Sonstige."""
    if label:
        name = party_name(label)
        if name:
            return name
        return "Sonstige"
    return default


def parse_value_text(text):
    """Yield (label or None, numeric token) pairs from one cell fragment."""
    for match in VALUE_RE.finditer(text):
        label = match.group("label")
        value = re.sub(r"\s+", "", match.group("value"))
        if label:
            label = clean_space(label)
        yield label, value


def format_percent(value):
    """Format a sum the way the source pages do: '23 %' or '4,5 %'."""
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral():
        rendered = str(quantized.to_integral())
    else:
        rendered = f"{quantized:.2f}".rstrip("0").rstrip(".")
        rendered = rendered.replace(".", ",")
    return f"{rendered} %"


def parse_decimal(token):
    """Parse a German percentage token. Ranges ('38-39') return None."""
    if re.search(r"[–\-−]", token):
        return None
    number = token.strip()
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    return Decimal(number)


def footnote_texts(soup):
    """id -> visible text, used to tell WASG markers from other footnotes."""
    notes = {}
    for tag in soup.find_all(id=True):
        notes[tag["id"]] = tag.get_text(" ", strip=True)
    return notes


def segments_of(cell):
    """Split a cell on <br>. The site stacks two figures that way."""
    html = cell.decode_contents()
    parts = re.split(r"(?i)<br\s*/?>", html)
    return parts or [html]


def contributions_from_cell(cell, default_party, footnotes):
    """Return (column, token) pairs for one percentage cell.

    Struck-through numbers are dropped (the site uses them for corrected
    figures). A footnote whose text is only 'WASG' marks that figure as
    the Wahlalternative, which is not Die Linke and therefore goes to
    Sonstige. A footnote such as 'zusammen mit WASG' leaves the number in
    the Linke column.
    """
    found = []
    for part in segments_of(cell):
        fragment = BeautifulSoup(part, "lxml")
        for tag in fragment.find_all(["s", "strike", "del"]):
            tag.decompose()

        wasg_separate = False
        for anchor in list(fragment.find_all("a", href=True)):
            anchor_text = anchor.get_text(strip=True)
            # Party names are links too ("BSW"). Only asterisks are footnotes.
            if not re.fullmatch(r"\*+", anchor_text or ""):
                continue
            note = footnotes.get(anchor["href"].lstrip("#"), "")
            lowered = note.casefold()
            if "wasg" in lowered and not any(
                hint in lowered for hint in ("zusammen", " und ", "linke", "pds", "/")
            ):
                wasg_separate = True
            anchor.decompose()

        text = clean_space(fragment.get_text(" ", strip=True))
        if not text or is_blank_value(text):
            continue
        # "14,? %" is an unknown digit, not a percentage.
        if re.search(r"\d\s*,\s*\?", text):
            continue

        parsed = list(parse_value_text(text))
        if not parsed:
            continue
        if wasg_separate:
            unlabeled = [i for i, (label, _) in enumerate(parsed) if not label]
            if unlabeled:
                index = unlabeled[-1]
                label, value = parsed[index]
                parsed[index] = ("WASG", value)

        for label, value in parsed:
            column = canonical_party(label, default_party)
            if column:
                found.append((column, value))
    return found


def split_sample(text):
    """Split the Befragte cell into sample size and fieldwork window.

    The site stores both in one cell, e.g. "TOM • 1.207" and "12.10.–17.10.".
    The method prefix (T, O, F, TOM, TSM) is dropped; Zeitraum keeps the
    date range or the month name ("Oktober 2007", "Ende Juni").
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\xa0", " ").replace("•", " ").replace("·", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()
    text = re.sub(
        r"^(?:TOM|TSM|CATI|CAWI|[OTFS])\b\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )

    count_match = None
    for match in COUNT_RE.finditer(text):
        raw = re.search(r"\d{1,3}(?:\.\d{3})+|\d{2,6}", match.group(0)).group(0)
        # A bare year belongs to the fieldwork window ("Oktober 2007").
        if "." not in raw and re.fullmatch(r"(?:19|20)\d{2}", raw):
            continue
        count_match = match
        break

    if count_match is None:
        return "", cleanup_period(text)

    count = re.sub(r"\s+", "", count_match.group(0))
    rest = text[: count_match.start()] + " " + text[count_match.end() :]
    return count, cleanup_period(rest)


def cleanup_period(text):
    """Turn the leftover Befragte text into a Zeitraum string."""
    text = re.sub(r"\s+", " ", text).strip(" ,;/")
    # A lone "?" between the sample size and the dates is not a period.
    text = re.sub(r"(^|\s)\?(?=\s|$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text in {"?", "–", "-", "—"}:
        return ""
    return text


def clean_datum(text):
    """Publication date, with footnote stars and stray spaces removed."""
    text = clean_space(text)
    text = re.sub(r"\s*\*+\s*$", "", text).strip()
    # "06.09 .2000" has a stray space before the year.
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    if text in {"?", "–", "-", "—", ""}:
        return ""
    return text


def expand_header(header_row):
    """Repeat each header across its colspan so body cells line up.

    Some older tables use colspan="2" on every party (a leftover of a
    city-versus-state layout). Expanding both the header and the body by
    colspan keeps 'CDU' attached to the CDU cell.
    """
    labels = []
    for cell in header_row.find_all(["th", "td"], recursive=False):
        span = max(int(cell.get("colspan") or 1), 1)
        labels.extend([clean_space(cell.get_text(" ", strip=True))] * span)
    return labels


def header_role(label):
    """Classify a header as institut, befragte, datum, a party, or ignore."""
    key = norm_key(label)
    if key == "institut":
        return "institut", None
    if key == "befragte":
        return "befragte", None
    if key == "datum":
        return "datum", None
    if key in {"auftraggeber", "quelle", ""}:
        return "ignore", None
    if key in RESIDUAL_LABELS or key in GROUP_LABELS:
        return "party", "Sonstige"
    name = party_name(label)
    if name:
        return "party", name
    return "ignore", None


def data_rows(table):
    """Body rows, including rows the site left outside <tbody>.

    Several pages close </tfoot> and then continue with <tr> without
    opening a new <tbody>. Those rows are still polls. Header and footnote
    rows live in thead/tfoot and are skipped.
    """
    rows = []
    for row in table.find_all("tr"):
        if row.find_parent("thead") or row.find_parent("tfoot"):
            continue
        rows.append(row)
    return rows


def aligned_cells(row, headers):
    """Yield (header_label, cell) in header order, honouring colspan."""
    column = 0
    for cell in row.find_all(["td", "th"], recursive=False):
        span = max(int(cell.get("colspan") or 1), 1)
        if column >= len(headers):
            break
        yield headers[column], cell
        column += span


class PartyTotals:
    """Collect percentages for every party mentioned in one poll."""

    def __init__(self):
        self.numbers = {}
        self.ranges = {}

    def add(self, column, token):
        number = parse_decimal(token)
        if number is None:
            self.ranges.setdefault(column, []).append(token)
        else:
            self.numbers.setdefault(column, []).append(number)


def render_share(numbers, ranges):
    """Format one party's figures in a single poll."""
    parts = []
    if numbers:
        parts.append(format_percent(sum(numbers, Decimal("0"))))
    parts.extend(f"{token} %" for token in ranges)
    return " / ".join(parts)


def share_exceeds(numbers, ranges, limit):
    """True when a reported share is strictly above the limit.

    A range counts only when its whole band is above the limit, so
    "5–6 %" does not clear a limit of 5 while "6–7 %" does.
    """
    if any(number > limit for number in numbers):
        return True
    for token in ranges:
        first = re.split(r"[–\-−]", token, maxsplit=1)[0]
        lower = parse_decimal(first)
        if lower is not None and lower > limit:
            return True
    return False


def output_columns(extra):
    """Fixed columns, then extra party columns, then Sonstige and the meta fields."""
    return [
        "Bundesland",
        "Institut",
        "Datum",
        *PARTY_COLUMNS,
        *extra,
        "Sonstige",
        "Befragte",
        "Zeitraum",
    ]


def extra_party_columns(records):
    """Parties that clear PARTY_COLUMN_ABOVE in at least one saved poll."""
    limit = Decimal(PARTY_COLUMN_ABOVE)
    maxima = {}
    for record in records:
        for name, numbers in record["_numbers"].items():
            if name in PARTY_COLUMNS or name == "Sonstige":
                continue
            ranges = record["_ranges"].get(name, [])
            if share_exceeds(numbers, ranges, limit):
                maxima[name] = True
        for name, ranges in record["_ranges"].items():
            if name in PARTY_COLUMNS or name == "Sonstige" or name in maxima:
                continue
            if share_exceeds([], ranges, limit):
                maxima[name] = True
    return sorted(maxima, key=str.casefold)


def materialize(record, extra):
    """Flatten one poll. Parties without a column are added to Sonstige."""
    chosen = set(extra)
    row = {
        "Bundesland": record["Bundesland"],
        "Institut": record["Institut"],
        "Datum": record["Datum"],
        "Befragte": record["Befragte"],
        "Zeitraum": record["Zeitraum"],
    }
    for name in PARTY_COLUMNS:
        row[name] = render_share(
            record["_numbers"].get(name, []),
            record["_ranges"].get(name, []),
        )
    for name in extra:
        row[name] = render_share(
            record["_numbers"].get(name, []),
            record["_ranges"].get(name, []),
        )

    sonst_numbers = list(record["_numbers"].get("Sonstige", []))
    sonst_ranges = list(record["_ranges"].get("Sonstige", []))
    for name, numbers in record["_numbers"].items():
        if name in PARTY_COLUMNS or name == "Sonstige" or name in chosen:
            continue
        sonst_numbers.extend(numbers)
    for name, ranges in record["_ranges"].items():
        if name in PARTY_COLUMNS or name == "Sonstige" or name in chosen:
            continue
        sonst_ranges.extend(ranges)
    row["Sonstige"] = render_share(sonst_numbers, sonst_ranges)
    return row


def parse_row(row, headers, state, footnotes):
    """Parse one poll row into the output schema, or return None to skip."""
    visible = row.get_text(" ", strip=True)
    if ELECTION_RE.search(visible):
        return None
    # Footnotes and editor's notes are full-width rows, not polls.
    if any("foot" in (cell.get("class") or []) for cell in row.find_all(["td", "th"])):
        return None

    institut = ""
    datum = ""
    befragte = ""
    zeitraum = ""
    totals = PartyTotals()

    for label, cell in aligned_cells(row, headers):
        role, default_party = header_role(label)
        if role == "institut":
            institut = join_wrapped_lines(cell.get_text("\n", strip=True))
        elif role == "datum":
            datum = clean_datum(cell.get_text(" ", strip=True))
        elif role == "befragte":
            befragte, zeitraum = split_sample(cell.get_text("\n", strip=True))
        elif role == "party":
            for column, token in contributions_from_cell(cell, default_party, footnotes):
                totals.add(column, token)

    # Notes and half-parsed rows have no institute or no publication date.
    if not institut or not datum or len(institut) > 80:
        return None
    if norm_key(institut) in {"institut", "auftraggeber", "quelle"}:
        return None

    return {
        "Bundesland": state,
        "Institut": institut,
        "Datum": datum,
        "Befragte": befragte,
        "Zeitraum": zeitraum,
        "_numbers": totals.numbers,
        "_ranges": totals.ranges,
    }


def extract_state_name(soup, filename):
    """'Umfragen Baden-Württemberg' -> 'Baden-Württemberg'."""
    heading = soup.find("h1")
    if not heading:
        return filename
    name = clean_space(heading.get_text(" ", strip=True))
    name = re.sub(r"^Umfragen\s+", "", name)
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    return name


def parse_state_html(html, filename):
    """Parse every poll table on one state page."""
    soup = BeautifulSoup(html, "lxml")
    state = extract_state_name(soup, filename)
    footnotes = footnote_texts(soup)
    records = []
    for table in soup.find_all("table", class_="wilko"):
        head = table.find("thead")
        if head is None or head.find("tr") is None:
            continue
        headers = expand_header(head.find("tr"))
        if "Institut" not in headers or "Datum" not in headers:
            continue
        for row in data_rows(table):
            record = parse_row(row, headers, state, footnotes)
            if record is not None:
                records.append(record)
    return state, records


def fetch_bytes(url):
    """Download one page. wahlrecht.de expects a normal browser agent."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read()


def state_links(overview_html):
    """(filename, absolute url) for every Bundesland linked from the overview."""
    soup = BeautifulSoup(overview_html, "lxml")
    table = soup.find("table", class_="wilko")
    if table is None:
        raise RuntimeError("Overview page has no poll table.")
    found = []
    seen = set()
    for anchor in table.find_all("a", href=True):
        href = anchor["href"]
        # State pages are siblings ("bayern.htm"). Skip termine/news links.
        if "/" in href or not href.endswith(".htm"):
            continue
        if href in seen:
            continue
        seen.add(href)
        found.append((href, urljoin(OVERVIEW_URL, href)))
    if len(found) < 16:
        raise RuntimeError(f"Expected 16 Bundeslaender, found {len(found)}.")
    return found


def download_pages():
    """Download the overview and each state page into RAW_DIR."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading overview: {OVERVIEW_URL}")
    overview = fetch_bytes(OVERVIEW_URL)
    (RAW_DIR / "overview.htm").write_bytes(overview)

    saved = 0
    for filename, url in state_links(overview):
        print(f"Downloading {filename}: {url}")
        try:
            payload = fetch_bytes(url)
        except (HTTPError, URLError, TimeoutError) as error:
            print(f"FAILED {filename}: {error}")
            continue
        (RAW_DIR / filename).write_bytes(payload)
        saved += 1
        time.sleep(0.5)
    print(f"Saved {saved} state pages to {RAW_DIR}")


def saved_state_files():
    """State HTML files in the same order as the overview page."""
    overview_path = RAW_DIR / "overview.htm"
    if overview_path.exists():
        ordered = []
        for filename, _url in state_links(overview_path.read_bytes()):
            path = RAW_DIR / filename
            if path.exists():
                ordered.append(path)
        if ordered:
            return ordered
    return sorted(
        path for path in RAW_DIR.glob("*.htm") if path.name != "overview.htm"
    )


def resolve_surveys_from(value):
    """Turn SURVEYS_FROM into a date, or None when every poll should be kept."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        "SURVEYS_FROM must be YYYY-MM-DD or DD.MM.YYYY, " f"got {value!r}."
    )


def _year_from_short(token):
    """Map a two-digit year: 70 -> 1970, 03 -> 2003."""
    year = int(token)
    if len(token) == 4:
        return year
    return 2000 + year if year <= 30 else 1900 + year


def publication_bounds(datum):
    """Earliest and latest day a publication-date cell can refer to.

    Returns None when the cell cannot be read. A complete date has the
    same earliest and latest day. "??.01.2013" covers the whole of January,
    "1996" the whole year, "Juni 2007" the whole month.
    """
    text = clean_datum(datum)
    if not text:
        return None

    full = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if full:
        day, month, year = (int(part) for part in full.groups())
        try:
            found = date(year, month, day)
        except ValueError:
            return None
        return found, found

    partial = re.fullmatch(r"([\d?]{1,2})\.([\d?]{1,2})\.(\d{4})", text)
    if partial:
        return _partial_bounds(*partial.groups())

    named = re.fullmatch(
        r"([A-Za-zÄÖÜäöüß.]+)\s*'?(\d{2}|\d{4})",
        text,
    )
    if named:
        month = _MONTH_NUMBERS.get(norm_key(named.group(1)))
        if month is None:
            return None
        year = _year_from_short(named.group(2))
        latest_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, latest_day)

    year_only = re.fullmatch(r"(19|20)\d{2}", text)
    if year_only:
        year = int(text)
        return date(year, 1, 1), date(year, 12, 31)
    return None


def _partial_bounds(day_token, month_token, year_token):
    """Bounds for a date that still contains '?'."""
    year = int(year_token)
    if "?" in month_token:
        months = list(range(1, 13))
    else:
        month = int(month_token)
        if not 1 <= month <= 12:
            return None
        months = [month]

    if "?" in day_token:
        last = calendar.monthrange(year, months[-1])[1]
        return date(year, months[0], 1), date(year, months[-1], last)

    day = int(day_token)
    valid = [month for month in months if 1 <= day <= calendar.monthrange(year, month)[1]]
    if not valid:
        return None
    return date(year, valid[0], day), date(year, valid[-1], day)


def keep_from_date(datum, cutoff):
    """True when this poll should be written.

    A partial date is kept when its latest possible day is still on or
    after the cutoff, and dropped only when every possible day is older.
    A date that cannot be read is kept.
    """
    if cutoff is None:
        return True
    bounds = publication_bounds(datum)
    if bounds is None:
        return True
    _earliest, latest = bounds
    return latest >= cutoff


def build_table():
    """Parse the saved HTML files and return the poll table."""
    files = saved_state_files()
    if not files:
        print(
            f"No saved HTML in {RAW_DIR}. "
            "Set REFRESH_HTML = True to download the state pages."
        )
        return pd.DataFrame(columns=BASE_COLUMNS)

    cutoff = resolve_surveys_from(SURVEYS_FROM)
    if cutoff is None:
        print("SURVEYS_FROM is not set; keeping polls from every date.")
    else:
        print(f"Keeping polls published on or after {cutoff.isoformat()}.")

    print(f"Parsing saved HTML from {RAW_DIR}")
    records = []
    dropped = 0
    unread_dates = 0
    for path in files:
        html = path.read_text(encoding="utf-8", errors="replace")
        state, rows = parse_state_html(html, path.name)
        kept = []
        for row in rows:
            if cutoff is not None and publication_bounds(row["Datum"]) is None:
                unread_dates += 1
            if keep_from_date(row["Datum"], cutoff):
                kept.append(row)
            else:
                dropped += 1
        print(f"  {state}: {len(kept)} polls")
        records.extend(kept)

    if cutoff is not None:
        print(f"Left out {dropped} polls published before {cutoff.isoformat()}.")
        if unread_dates:
            print(
                f"Kept {unread_dates} polls whose publication date "
                "could not be read."
            )

    extra = extra_party_columns(records)
    if extra:
        print(
            "Added party columns for shares above "
            f"{PARTY_COLUMN_ABOVE} %: {', '.join(extra)}"
        )
    else:
        print(f"No party was above {PARTY_COLUMN_ABOVE} % in any saved poll.")

    flat = [materialize(record, extra) for record in records]
    frame = pd.DataFrame.from_records(flat)
    frame = frame.reindex(columns=output_columns(extra)).fillna("")
    # A poll sitting on the boundary of two tables (different party columns
    # before and after an election) is sometimes printed twice.
    before = len(frame)
    frame = frame.drop_duplicates().reset_index(drop=True)
    duplicates = before - len(frame)
    if duplicates:
        print(f"Dropped {duplicates} duplicate poll(s) repeated across tables.")
    return frame


def main():
    if REFRESH_HTML:
        download_pages()
    else:
        print(
            f"Reusing saved HTML in {RAW_DIR} "
            "(set REFRESH_HTML = True to download again)."
        )

    frame = build_table()
    if frame.empty:
        print("No polls parsed; CSV was not written.")
        return

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {len(frame)} polls to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
