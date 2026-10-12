"""
Scrape the 21st-century event table from taschenhirn.de and parse the event date
from the entry text.

Source : https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/
Raw    : data/raw/taschenhirn_21jh.html   (untouched bytes, for reproducibility;
         reused if present, --force refetches)
Interim: data/interim/events_taschenhirn.csv with columns
    quelle            "taschenhirn"
    quelle_url        page URL
    abgerufen_am      fetch timestamp (ISO)
    jahr              year or range as printed in the first column ("2001", "2010-2012")
    jahr_start        first year as int
    jahr_ende         last year as int (== jahr_start for single years)
    datum             YYYY-MM-DD parsed from the entry text, empty if the text
                      gives no day or month (see date rules below)
    datum_ende        end day for ranges like "14./15. Juli" or "8. bis 25. Februar"
    datum_praezision  tag | monat | jahr
    datum_roh         the exact text fragment the date was parsed from
    ereignis          one event per row; multi-event cells split at paragraphs / line breaks

Date rules (the page only has a year column; the day often appears inside the text):
  * The text is scanned left to right for date tokens of the forms
    "15. April", "15. April 2013", "14./15. Juli", "8. bis 25. Februar",
    "6.2.2023", "19.12." and month-only tokens "Anfang/Mitte/Ende/Ab/Im Oktober",
    "Dezember 2019", "August/September".
  * The first token whose year is the row's year is used. Tokens with another
    explicit year (e.g. "Wahlsieg vom 8. November 2016" in a 2017 row) are skipped,
    because they describe context, not the event. Tokens without a year get the
    row's year (jahr_start).
  * Day tokens give datum_praezision "tag"; month-only tokens give "monat" with
    datum set to the first of the month; no token gives "jahr" with datum empty.
    Seasons ("im Herbst") are not parsed.
  * Nothing is looked up or guessed; datum_roh shows what was matched so every
    row can be checked against the text.

Encoding: the server sends "Content-Type: text/html" without a charset, so
requests would guess Latin-1 and garble every umlaut. The page declares
<meta charset="UTF-8">, so the raw bytes are decoded as UTF-8 explicitly.

Run from the project root:  python -m src.scraping.taschenhirn [--force]
Only needs `requests`; parsing uses the standard library.
"""

import argparse
import csv
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

URL = "https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/"
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "taschenhirn_21jh.html"
OUT = ROOT / "data" / "interim" / "events_taschenhirn.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}

YEAR_RE = re.compile(r"^\s*(\d{4})\s*(?:[-–/]\s*(\d{4}))?\s*$")

MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
MONTH_RE = "|".join(MONTHS)  # alternation used inside the patterns below

# Date token patterns, tried at every position of the text; the leftmost match wins.
# Named groups: d1 (day), d2 (end day of a range), m (month name), mn (month number), y (year).
DATE_PATTERNS = [
    # "14./15. Juli 2021", "7.-9. Mai", "8. bis 25. Februar", "15. und 16. Juli"
    re.compile(rf"\b(?P<d1>\d{{1,2}})\.\s*(?:/|–|-|bis|und)\s*(?P<d2>\d{{1,2}})\.\s*(?P<m>{MONTH_RE})s?(?:\s+(?P<y>\d{{4}}))?\b", re.I),
    # "15. April", "15. April 2013", "13. Novembers" (genitive s)
    re.compile(rf"\b(?P<d1>\d{{1,2}})\.\s*(?P<m>{MONTH_RE})s?(?:\s+(?P<y>\d{{4}}))?\b", re.I),
    # "6.2.2023", "19.12." , "13.7." (not followed by another digit, so "8,50" or "1:7" never match)
    re.compile(r"\b(?P<d1>\d{1,2})\.(?P<mn>\d{1,2})\.(?:(?P<y>\d{4})|(?!\d))"),
    # month only: "Anfang Juli", "Ende August", "Mitte September", "Ab Oktober", "Im Dezember 2019",
    # "August/September" (start month wins), "Dezember 2019"
    re.compile(rf"\b(?:Anfang|Mitte|Ende|Ab|Im|Seit|Vom)\s+(?P<m>{MONTH_RE})(?:/(?:{MONTH_RE}))?(?:\s+(?P<y>\d{{4}}))?\b", re.I),
    re.compile(rf"\b(?P<m>{MONTH_RE})(?:/(?:{MONTH_RE}))?\s+(?P<y>\d{{4}})\b", re.I),
]


class TableParser(HTMLParser):
    """Collects every <table> as rows of cells; a cell is a list of text
    fragments, split whenever a <br>, <p>, <li> or <div> starts."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = [""]
        elif tag in ("br", "p", "li", "div") and self._cell is not None:
            self._cell.append("")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[-1] += data


def clean(text: str) -> str:
    # \xa0 is the non-breaking space the page uses a lot; treat it as a space
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def fetch(force: bool = False) -> tuple[str, str]:
    """Return (html, fetch timestamp). Uses the cached raw file unless --force."""
    if RAW.exists() and not force:
        fetched = datetime.fromtimestamp(RAW.stat().st_mtime, tz=timezone.utc)
    else:
        r = requests.get(URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_bytes(r.content)  # raw bytes, no decoding involved
        fetched = datetime.now(tz=timezone.utc)
    html = RAW.read_bytes().decode("utf-8")  # page declares <meta charset="UTF-8">
    return html, fetched.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text: str, year: int) -> dict:
    """Find the first date token in the text that belongs to `year` (see module docstring)."""
    candidates = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            candidates.append((m.start(), m))
    candidates.sort(key=lambda t: t[0])  # leftmost first, regardless of pattern

    for _, m in candidates:
        g = m.groupdict()
        y = int(g["y"]) if g.get("y") else year
        if y != year:
            continue  # context date from another year, e.g. "Wahlsieg vom 8. November 2016"
        month = MONTHS[g["m"].lower()] if g.get("m") else int(g["mn"])
        if not 1 <= month <= 12:
            continue
        try:
            if g.get("d1"):
                start = date(y, month, int(g["d1"]))
                end = date(y, month, int(g["d2"])) if g.get("d2") else None
                return {"datum": start.isoformat(), "datum_ende": end.isoformat() if end else "",
                        "datum_praezision": "tag", "datum_roh": m.group(0)}
            return {"datum": date(y, month, 1).isoformat(), "datum_ende": "",
                    "datum_praezision": "monat", "datum_roh": m.group(0)}
        except ValueError:
            continue  # e.g. "31. Februar" or "1.7" misread; try the next token
    return {"datum": "", "datum_ende": "", "datum_praezision": "jahr", "datum_roh": ""}


def parse(html: str, fetched: str) -> list[dict]:
    parser = TableParser()
    parser.feed(html)
    rows = []
    for table in parser.tables:
        for cells in table:
            if len(cells) < 2:
                continue
            year_text = clean(" ".join(cells[0]))
            m = YEAR_RE.match(year_text)
            if not m:  # header row or decade heading
                continue
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            for frag in cells[1]:
                ev = clean(frag)
                if not ev:
                    continue
                # for a year range there is no single event year; only an explicit year in the text counts
                row = {"quelle": "taschenhirn", "quelle_url": URL, "abgerufen_am": fetched,
                       "jahr": year_text, "jahr_start": start, "jahr_ende": end}
                row.update(parse_date(ev, start) if start == end else
                           {"datum": "", "datum_ende": "", "datum_praezision": "jahr", "datum_roh": ""})
                row["ereignis"] = ev
                rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="refetch the page even if the raw file exists")
    args = ap.parse_args()

    html, fetched = fetch(force=args.force)
    rows = parse(html, fetched)
    if not rows:
        raise SystemExit("No rows found. Page layout may have changed; inspect data/raw/taschenhirn_21jh.html.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["quelle", "quelle_url", "abgerufen_am", "jahr", "jahr_start", "jahr_ende",
              "datum", "datum_ende", "datum_praezision", "datum_roh", "ereignis"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    by_prec = {p: sum(r["datum_praezision"] == p for r in rows) for p in ("tag", "monat", "jahr")}
    print(f"{len(rows)} events written to {OUT.relative_to(ROOT)}; date precision: {by_prec}")


if __name__ == "__main__":
    main()
